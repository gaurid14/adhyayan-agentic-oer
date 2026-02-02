import json
import os
import re
import textstat
from asgiref.sync import sync_to_async
from langchain.tools import tool

from accounts.models import ContentScore, UploadCheck
from langgraph_agents.services.gemini_service import llm
from mcp.client.session import ClientSession


# ------------------------------
# PATH SETTINGS
# ------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXTRACTED_JSON_DIR = os.path.join(BASE_DIR, "storage", "extracted_content")


def load_extracted_json(upload_id: int) -> dict:
    json_path = os.path.join(EXTRACTED_JSON_DIR, f"upload_{upload_id}.json")

    if not os.path.exists(json_path):
        return {}

    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------
# LEVEL SETTINGS (PreSchool → Higher Education)
# ------------------------------
LEVELS = {
    # very simple language expected
    "preschool": {"fre_good": 85, "fre_ok": 70, "sent_good": 8, "sent_ok": 12},
    "primary": {"fre_good": 75, "fre_ok": 60, "sent_good": 10, "sent_ok": 14},
    "middle": {"fre_good": 65, "fre_ok": 50, "sent_good": 12, "sent_ok": 16},
    "secondary": {"fre_good": 55, "fre_ok": 40, "sent_good": 14, "sent_ok": 18},
    "hsc": {"fre_good": 50, "fre_ok": 35, "sent_good": 16, "sent_ok": 20},

    # engineering / college: technical words are normal
    "undergrad": {"fre_good": 45, "fre_ok": 25, "sent_good": 18, "sent_ok": 24},
    "postgrad": {"fre_good": 40, "fre_ok": 20, "sent_good": 20, "sent_ok": 28},
    "phd": {"fre_good": 35, "fre_ok": 15, "sent_good": 22, "sent_ok": 30},

    # default fallback
    "default": {"fre_good": 45, "fre_ok": 25, "sent_good": 18, "sent_ok": 24},
}


# ------------------------------
# TECHNICAL TERM NORMALIZATION
# ------------------------------
def normalize_for_readability(text: str) -> str:
    """
    Makes readability scoring fair for technical docs by normalizing:
    - Dataset names / acronyms / model names
    - dimensions like 32x32, 28×28
    - versions like MobileNetV2
    This is ONLY for readability metric computation, not for Gemini.
    """
    if not text:
        return ""

    t = text

    # normalize common patterns
    t = re.sub(r"\b(CNN|ANN|RNN|LSTM|GRU|DNN)\b", "model", t)
    t = re.sub(r"\b(MNIST|CIFAR-?10|GTSRB|ImageNet)\b", "dataset", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(TensorFlow|Keras|PyTorch|scikit-?learn|NumPy|Pandas)\b", "library", t, flags=re.IGNORECASE)

    # normalize model names like MobileNetV2, ResNet50, BERT-base
    t = re.sub(r"\b[A-Za-z]+NetV?\d+\b", "model", t)
    t = re.sub(r"\bResNet\d+\b", "model", t)
    t = re.sub(r"\bBERT[-_ ]?(base|large)?\b", "model", t, flags=re.IGNORECASE)

    # normalize dimensions
    t = re.sub(r"\b\d+\s*[x×]\s*\d+\b", "dimension", t)

    # remove excessive symbols that break readability parsing
    t = re.sub(r"[•◦▪▫→⇒▶]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()

    return t


# ------------------------------
# Helpers
# ------------------------------
def count_passive_voice(text: str) -> int:
    passive_patterns = [
        r"\bwas\b \w+ed",
        r"\bwere\b \w+ed",
        r"\bbeen\b \w+ed",
        r"\bis\b \w+ed",
        r"\bare\b \w+ed",
    ]
    return sum(len(re.findall(p, text.lower())) for p in passive_patterns)


def avg_sentence_length(text: str) -> float:
    sentences = re.split(r"[.!?]", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return 0
    words = text.split()
    return len(words) / max(len(sentences), 1)


def python_clarity_score(text: str, target_level: str = "undergrad") -> dict:
    """
    Python score = simple readability heuristics.
    Scales automatically based on target_level.
    """
    cfg = LEVELS.get(target_level, LEVELS["default"])

    normalized = normalize_for_readability(text)

    passive = count_passive_voice(normalized)
    avg_len = avg_sentence_length(normalized)

    # FRE is based on syllables, so normalize_for_readability prevents unfair penalty
    readability = textstat.flesch_reading_ease(normalized)

    score = 0

    # Readability scoring (scaled by education level)
    if readability >= cfg["fre_good"]:
        score += 4
    elif readability >= cfg["fre_ok"]:
        score += 3
    elif readability >= (cfg["fre_ok"] - 15):
        score += 2
    else:
        score += 1

    # Sentence length scoring (scaled by education level)
    if avg_len <= cfg["sent_good"]:
        score += 4
    elif avg_len <= cfg["sent_ok"]:
        score += 3
    elif avg_len <= (cfg["sent_ok"] + 10):
        score += 2
    else:
        score += 1

    # Passive voice scoring
    if passive <= 5:
        score += 2
    elif passive <= 15:
        score += 1

    return {
        "score": min(10, score),
        "readability": readability,
        "avg_sentence_length": avg_len,
        "passive_voice_count": passive,
        "target_level": target_level,
    }


# ------------------------------
# Gemini Clarity Agent (with sub-scores)
# ------------------------------
def safe_extract_json(text: str) -> dict:
    """
    Gemini sometimes returns extra explanation.
    This function extracts the FIRST JSON object safely.
    """
    if not text:
        return {}

    # exact JSON
    try:
        return json.loads(text)
    except Exception:
        pass

    # extract JSON object inside text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}

    return {}


async def analyze_clarity_with_gemini(content: str, target_level: str = "undergrad") -> dict:
    prompt = f"""
You are evaluating CLARITY of educational content for student level: {target_level}.

Important rules:
- Technical terms are allowed (engineering/science content).
- Do NOT reduce score just because technical words exist.
- Reduce score only if terms are not explained, steps are confusing, or definitions are missing.

Return JSON ONLY:
{{
  "clarity": <1-10>,
  "definition_quality": <0-5>,
  "instruction_clarity": <0-5>,
  "term_explanation": <0-5>
}}

Content:
{content}
"""
    response = llm.invoke(prompt)

    gem = safe_extract_json(getattr(response, "content", "") or "")

    if not gem:
        # print("[ERROR] Gemini JSON parsing failed:", raw[:300])
        return {
            "clarity": 5,
            "definition_quality": 2,
            "instruction_clarity": 2,
            "term_explanation": 2,
        }

    return {
        "clarity": float(gem.get("clarity", 5)),
        "definition_quality": float(gem.get("definition_quality", 2)),
        "instruction_clarity": float(gem.get("instruction_clarity", 2)),
        "term_explanation": float(gem.get("term_explanation", 2)),
    }


# ------------------------------
# FINAL CLARITY COMBINATION (USES SUB-SCORES)
# ------------------------------
def combine_clarity(python_result: dict, gemini_result: dict) -> float:
    """
    Final clarity is blended from:
    - python_score (0-10)
    - gemini_clarity (0-10)
    - gemini_subscores (0-5 each) scaled to (0-10)

    This ensures:
    technical terms are okay
    explanation quality matters a lot
    """

    python_score = float(python_result.get("score", 5))
    gem_clarity = float(gemini_result.get("clarity", 5))

    # subscores are 0-5 → scale to 0-10
    defq = float(gemini_result.get("definition_quality", 2)) * 2
    instr = float(gemini_result.get("instruction_clarity", 2)) * 2
    termx = float(gemini_result.get("term_explanation", 2)) * 2

    # Gemini internal clarity (teacher-like)
    gem_internal = (0.4 * gem_clarity) + (0.2 * defq) + (0.2 * instr) + (0.2 * termx)

    # Final combination (Python 30%, Gemini 70%)
    final = (0.3 * python_score) + (0.7 * gem_internal)

    return round(min(10, final), 2)


# ------------------------------
# CLARITY AGENT TOOL
# ------------------------------
@tool
async def evaluate_clarity(state: dict) -> dict:
    """
    Clarity Agent:
    - Reads extracted JSON from: storage/extracted_content/upload_{upload_id}.json
    - Computes Python clarity
    - Computes Gemini clarity + subscores
    - Combines into final score (0-10)
    - Stores in ContentScore table
    """
    upload_id = state.get("upload_id")
    target_level = state.get("target_level", "undergrad")

    if not upload_id:
        return {**state, "status": "clarity_failed", "reason": "upload_id missing"}

    extracted_data = load_extracted_json(upload_id)
    if not extracted_data:
        return {**state, "status": "clarity_failed", "reason": "json missing"}

    combined_text = (extracted_data.get("content", {}).get("combined_text") or "").strip()
    if not combined_text:
        return {**state, "status": "clarity_failed", "reason": "combined_text empty"}

    py_result = python_clarity_score(combined_text, target_level=target_level)
    gem_result = await analyze_clarity_with_gemini(combined_text, target_level=target_level)
    final_score = combine_clarity(py_result, gem_result)

    # Save using MCP session from graph state
    session = state.get("mcp_session")
    if session:
        save_resp = await session.call_tool(
            "db_save_scores_generic",
            {"upload_id": upload_id, "scores": {"clarity": final_score}}
        )
        print("✅ MCP saved clarity:", save_resp.content[0].text)
    else:
        print("⚠️ mcp_session missing in state")

    return {
        **state,
        "status": "clarity_evaluated",
        "clarity_score": final_score,
        "python": py_result,
        "gemini": gem_result,
    }
