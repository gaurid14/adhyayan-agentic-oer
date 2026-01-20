import json
from difflib import SequenceMatcher

from asgiref.sync import sync_to_async

from accounts.models import UploadCheck, ContentScore
from langgraph_agents.agents.engagement import extract_all_pdf_texts_recursive, extract_all_video_transcripts_recursive
from langgraph_agents.services.gemini_service import llm
from langchain.tools import tool

def paragraph_similarity(text: str) -> float:
    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 50]
    if len(paragraphs) < 2:
        return 0

    sims = []
    for i in range(len(paragraphs) - 1):
        s = SequenceMatcher(None, paragraphs[i], paragraphs[i+1]).ratio()
        sims.append(s)

    return sum(sims) / len(sims)


def python_coherence_score(text: str) -> dict:
    sim = paragraph_similarity(text)

    # ideal similarity is 0.3–0.7 (connected but not repetitive)
    if sim < 0.2: score = 4
    elif sim < 0.4: score = 6
    elif sim < 0.7: score = 8
    else: score = 5  # too repetitive

    return {
        "score": score,
        "paragraph_similarity": sim
    }

async def analyze_coherence_with_gemini(content: str) -> dict:
    prompt = f"""
    Evaluate COHERENCE of educational content.
    Return JSON ONLY:

    {{
      "coherence": <1-10>,
      "logical_flow": <0-5>,
      "section_connectivity": <0-5>,
      "topic_continuity": <0-5>
    }}

    Content:
    {content}
    """

    response = llm.invoke(prompt)
    try:
        return json.loads(response.content)
    except:
        return {"coherence": 5, "logical_flow": 2, "section_connectivity": 2, "topic_continuity": 2}

def combine_coherence(py, ai):
    py_score = py["score"]
    ai_score = ai["coherence"]
    final = (py_score * 0.4) + (ai_score * 0.6)
    return round(min(10, final), 2)

@tool
async def evaluate_coherence(contributor_id: int, chapter_id: int, drive_folders: dict, **kwargs):
    """
    Evaluates coherence of educational content using:
    - Python paragraph similarity metric
    - Gemini-based evaluation of logical flow, continuity, and transitions
    Returns a weighted score (0–10).
    """

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

    pdf_texts = await sync_to_async(extract_all_pdf_texts_recursive)(drive_folders.get("pdf"))
    video_texts = await sync_to_async(extract_all_video_transcripts_recursive)(drive_folders.get("videos"))
    full_text = "\n\n".join(pdf_texts + video_texts)

    if not full_text.strip():
        return {"status": "no_content_found"}

    py_result = python_coherence_score(full_text)
    gem_result = await analyze_coherence_with_gemini(full_text)

    final_score = combine_coherence(py_result, gem_result)

    await sync_to_async(lambda: _save_coherence(score_obj, final_score))()

    return {
        "status": "coherence_evaluated",
        "python": py_result,
        "gemini": gem_result,
        "score": final_score
    }


def _save_coherence(score_obj, coherence):
    score_obj.coherence = coherence
    score_obj.save()
