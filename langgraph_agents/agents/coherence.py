import json
import os
import re
from difflib import SequenceMatcher
from langchain.tools import tool

from langgraph_agents.services.gemini_service import llm


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
# SAFE JSON EXTRACTION
# ------------------------------
def safe_extract_json(text: str) -> dict:
    if not text:
        return {}

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}

    return {}


# ------------------------------
# COHERENCE SCALING (Levels)
# ------------------------------
COHERENCE_LEVELS = {
    # younger students need very smooth flow
    "preschool": {"py_weight": 0.25, "ai_weight": 0.75},
    "primary": {"py_weight": 0.25, "ai_weight": 0.75},
    "middle": {"py_weight": 0.30, "ai_weight": 0.70},
    "secondary": {"py_weight": 0.35, "ai_weight": 0.65},
    "hsc": {"py_weight": 0.35, "ai_weight": 0.65},

    # college: topic jumps are normal sometimes, rely more on AI semantic judgement
    "undergrad": {"py_weight": 0.30, "ai_weight": 0.70},
    "postgrad": {"py_weight": 0.25, "ai_weight": 0.75},
    "phd": {"py_weight": 0.20, "ai_weight": 0.80},

    "default": {"py_weight": 0.30, "ai_weight": 0.70},
}


# ------------------------------
# PYTHON COHERENCE
# ------------------------------
def paragraph_similarity(text: str) -> float:
    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 50]
    if len(paragraphs) < 2:
        return 0.0

    sims = []
    for i in range(len(paragraphs) - 1):
        s = SequenceMatcher(None, paragraphs[i], paragraphs[i + 1]).ratio()
        sims.append(s)

    return sum(sims) / max(len(sims), 1)


def python_coherence_score(text: str) -> dict:
    sim = paragraph_similarity(text)

    # score smoothly on 0–10 instead of buckets
    # best range ~0.3 to 0.7
    if sim < 0.2:
        score = 4.0
    elif sim < 0.3:
        score = 6.0
    elif sim < 0.7:
        score = 8.5
    elif sim < 0.85:
        score = 7.0   # slightly repetitive
    else:
        score = 5.5   # too repetitive / copy paste

    return {
        "score": round(min(10.0, score), 2),
        "paragraph_similarity": round(sim, 3),
    }


# ------------------------------
# GEMINI COHERENCE
# ------------------------------
async def analyze_coherence_with_gemini(content: str, target_level: str = "undergrad") -> dict:
    prompt = f"""
You are evaluating COHERENCE of educational content for student level: {target_level}.

Meaning of coherence:
- Logical flow from one idea to the next
- Sections connect properly
- No sudden topic jumps
- Not repetitive copy-paste

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
    raw = getattr(response, "content", "") or ""

    data = safe_extract_json(raw)

    if not data:
        print("[ERROR] Gemini JSON parsing failed:", raw[:300])
        return {
            "coherence": 5,
            "logical_flow": 2,
            "section_connectivity": 2,
            "topic_continuity": 2,
        }

    return {
        "coherence": float(data.get("coherence", 5)),
        "logical_flow": float(data.get("logical_flow", 2)),
        "section_connectivity": float(data.get("section_connectivity", 2)),
        "topic_continuity": float(data.get("topic_continuity", 2)),
    }


# ------------------------------
# FINAL COHERENCE COMBINE (subscores included)
# ------------------------------
def combine_coherence(py: dict, ai: dict, target_level: str = "undergrad") -> float:
    cfg = COHERENCE_LEVELS.get(target_level, COHERENCE_LEVELS["default"])

    py_score = float(py.get("score", 5))
    ai_main = float(ai.get("coherence", 5))

    # subscores 0–5 → scale to 0–10
    logical = float(ai.get("logical_flow", 2)) * 2
    connect = float(ai.get("section_connectivity", 2)) * 2
    cont = float(ai.get("topic_continuity", 2)) * 2

    # Gemini internal coherence score (teacher judgement)
    ai_internal = (0.4 * ai_main) + (0.2 * logical) + (0.2 * connect) + (0.2 * cont)

    final = (cfg["py_weight"] * py_score) + (cfg["ai_weight"] * ai_internal)

    return round(min(10.0, final), 2)


# ------------------------------
# COHERENCE AGENT TOOL
# ------------------------------
@tool
async def evaluate_coherence(state: dict) -> dict:
    """
    Coherence Agent:
    - Reads extracted JSON upload_{upload_id}.json
    - Python coherence via paragraph similarity
    - Gemini coherence + subscores
    - Final score 0–10
    - Saves using MCP: db_save_scores_generic
    """
    upload_id = state.get("upload_id")
    target_level = state.get("target_level", "undergrad")

    if not upload_id:
        return {**state, "status": "coherence_failed", "reason": "upload_id missing"}

    extracted_data = load_extracted_json(upload_id)
    if not extracted_data:
        return {**state, "status": "coherence_failed", "reason": "json missing"}

    combined_text = (extracted_data.get("content", {}).get("combined_text") or "").strip()
    if not combined_text:
        return {**state, "status": "coherence_failed", "reason": "combined_text empty"}

    # Run scoring
    py_result = python_coherence_score(combined_text)
    gem_result = await analyze_coherence_with_gemini(combined_text, target_level=target_level)
    final_score = combine_coherence(py_result, gem_result, target_level=target_level)

    # Save using MCP session from graph state
    session = state.get("mcp_session")
    if session:
        save_resp = await session.call_tool(
            "db_save_scores_generic",
            {"upload_id": upload_id, "scores": {"coherence": final_score}}
        )
        print("✅ MCP saved coherence:", save_resp.content[0].text)
    else:
        print("⚠️ mcp_session missing in state")

    return {
        **state,
        "status": "coherence_evaluated",
        "coherence_score": final_score,
        "python": py_result,
        "gemini": gem_result,
    }
