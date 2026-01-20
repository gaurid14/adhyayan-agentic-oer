import json
import re
import textstat
from asgiref.sync import sync_to_async
from langchain.tools import tool
from accounts.models import UploadCheck, ContentScore
from langgraph_agents.agents.engagement import extract_all_pdf_texts_recursive, extract_all_video_transcripts_recursive
from langgraph_agents.services.gemini_service import llm

def count_passive_voice(text: str) -> int:
    # crude but effective passive voice detector
    passive_patterns = [
        r"\bwas\b \w+ed",
        r"\bwere\b \w+ed",
        r"\bbeen\b \w+ed",
        r"\bis\b \w+ed",
        r"\bare\b \w+ed"
    ]
    return sum(len(re.findall(p, text.lower())) for p in passive_patterns)


def avg_sentence_length(text: str) -> float:
    sentences = re.split(r"[.!?]", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return 0
    words = text.split()
    return len(words) / len(sentences)


def python_clarity_score(text: str) -> dict:
    passive = count_passive_voice(text)
    avg_len = avg_sentence_length(text)
    readability = textstat.flesch_reading_ease(text)

    # scoring logic
    score = 0

    # readability scoring
    if readability > 60:
        score += 4
    elif readability > 40:
        score += 3
    elif readability > 20:
        score += 2
    else:
        score += 1

    # sentence length scoring
    if avg_len <= 18:
        score += 4
    elif avg_len <= 25:
        score += 3
    elif avg_len <= 35:
        score += 2
    else:
        score += 1

    # passive voice (low is good)
    if passive <= 5:
        score += 2
    elif passive <= 15:
        score += 1

    return {
        "score": min(10, score),
        "readability": readability,
        "avg_sentence_length": avg_len,
        "passive_voice_count": passive
    }

async def analyze_clarity_with_gemini(content: str) -> dict:
    prompt = f"""
    You evaluate CLARITY of educational content.

    Return JSON ONLY:
    {{
      "clarity": <1-10 score>,
      "definition_quality": <0-5>,
      "instruction_clarity": <0-5>,
      "term_explanation": <0-5>
    }}

    Content:
    {content}
    """

    response = llm.invoke(prompt)
    try:
        return json.loads(response.content)
    except:
        return {"clarity": 5, "definition_quality": 2, "instruction_clarity": 2, "term_explanation": 2}

def combine_clarity(python_result, gemini_result):
    python_score = python_result["score"]
    ai_score = gemini_result["clarity"]

    final = (python_score * 0.4) + (ai_score * 0.6)
    return round(min(10, final), 2)

@tool
async def evaluate_clarity(contributor_id: int, chapter_id: int, drive_folders: dict, **kwargs):
    """
    Evaluates clarity using:
    - Python readability metrics (Flesch, sentence length, passive voice)
    - Gemini clarity analysis (definitions, instructions, explanations)
    Produces a final clarity score (0–10).
    """

    # ORM fetch
    upload = await sync_to_async(
        lambda: UploadCheck.objects.filter(
            contributor_id=contributor_id,
            chapter_id=chapter_id
        ).order_by('-timestamp').first()
    )()

    if not upload:
        return {"status": "no_upload_found"}

    score_obj, _ = await sync_to_async(
        lambda: ContentScore.objects.get_or_create(upload=upload)
    )()

    # Extract content
    pdf_texts = await sync_to_async(extract_all_pdf_texts_recursive)(drive_folders.get("pdf"))
    video_texts = await sync_to_async(extract_all_video_transcripts_recursive)(drive_folders.get("videos"))
    full_text = "\n\n".join(pdf_texts + video_texts)

    if not full_text.strip():
        return {"status": "no_content_found"}

    # Python clarity
    py_result = python_clarity_score(full_text)

    # Gemini clarity
    gem_result = await analyze_clarity_with_gemini(full_text)

    final_score = combine_clarity(py_result, gem_result)

    # Save
    await sync_to_async(lambda: _save_clarity(score_obj, final_score))()

    return {
        "status": "clarity_evaluated",
        "python": py_result,
        "gemini": gem_result,
        "score": final_score
    }


def _save_clarity(score_obj, clarity):
    score_obj.clarity = clarity
    score_obj.save()
