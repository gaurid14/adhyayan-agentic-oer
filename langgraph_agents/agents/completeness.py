import json
import os
import re
from typing import Any, Dict, List, Tuple

from langchain.tools import tool

from langgraph_agents.services.gemini_service import llm


# ------------------------------
# PATH SETTINGS
# ------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXTRACTED_JSON_DIR = os.path.join(BASE_DIR, "storage", "extracted_content")


def load_extracted_json(upload_id: int) -> dict:
    """Load extracted content JSON saved by submission_agent."""
    json_path = os.path.join(EXTRACTED_JSON_DIR, f"upload_{upload_id}.json")
    if not os.path.exists(json_path):
        return {}
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------
# SAFE JSON EXTRACTION
# ------------------------------
def safe_extract_json(text: str) -> dict:
    """Extract the first JSON object from an LLM response."""
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
# TOPIC EXTRACTION (from chapter description)
# ------------------------------
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "onto", "over", "under",
    "about", "above", "below", "between", "within", "without", "a", "an", "to", "of", "in",
    "on", "at", "by", "is", "are", "was", "were", "be", "been", "being", "as", "it", "its",
    "or", "not", "we", "you", "your", "they", "their", "them", "these", "those", "will", "can",
    "may", "might", "should", "must", "also",
}


def _extract_topic_terms(chapter_name: str = "", chapter_description: str = "", max_terms: int = 30) -> List[str]:
    text = f"{chapter_name}\n{chapter_description}".strip().lower()
    if not text:
        return []

    raw_parts = re.split(r"[\n\r\t•\-–—:;,.()\[\]{}<>/\\|]+", text)
    terms: List[str] = []

    for p in raw_parts:
        p = p.strip()
        if not p:
            continue
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9_+\-]{2,}", p)
        for w in words:
            wl = w.lower()
            if wl in _STOPWORDS:
                continue
            if len(wl) < 4:
                continue
            terms.append(wl)

    freq: Dict[str, int] = {}
    for t in terms:
        freq[t] = freq.get(t, 0) + 1

    sorted_terms = sorted(freq.keys(), key=lambda k: (freq[k], len(k)), reverse=True)
    return sorted_terms[:max_terms]


def _term_coverage_ratio(text: str, terms: List[str]) -> float:
    if not text or not terms:
        return 0.0

    lower = text.lower()
    hit = 0
    for t in terms:
        if t and t in lower:
            hit += 1
    return hit / max(len(terms), 1)


# ------------------------------
# PYTHON COMPLETENESS HEURISTICS (0–10)
# ------------------------------
_COMPLETENESS_TARGET_WORDS = {
    "preschool": 250,
    "primary": 400,
    "middle": 650,
    "secondary": 900,
    "hsc": 1100,
    "undergrad": 1400,
    "postgrad": 1700,
    "phd": 2000,
    "default": 1400,
}


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _section_cues(text: str) -> Dict[str, bool]:
    lower = (text or "").lower()
    cues = {
        "has_intro": bool(re.search(r"\b(introduction|overview|objective|aim)\b", lower)),
        "has_core": bool(re.search(r"\b(concept|theory|definition|explain|method|procedure)\b", lower)),
        "has_examples": bool(re.search(r"\b(example|illustration|case study|scenario)\b", lower)),
        "has_summary": bool(re.search(r"\b(summary|conclusion|recap|key takeaways)\b", lower)),
        "has_practice": bool(re.search(r"\b(exercise|question|quiz|activity|practice)\b", lower)),
    }
    return cues


def python_completeness_score(
    content: str,
    chapter_name: str = "",
    chapter_description: str = "",
    target_level: str = "undergrad",
) -> dict:
    """Heuristic completeness score.

    Completeness means: coverage + depth + learning flow. Python heuristics estimate:
    - enough content length for level
    - presence of typical learning-flow sections
    - rough coverage of chapter description terms
    """

    text = (content or "").strip()
    words = _count_words(text)

    target_words = _COMPLETENESS_TARGET_WORDS.get(target_level, _COMPLETENESS_TARGET_WORDS["default"])

    # 1) Length component (0–4)
    length_ratio = min(1.0, words / max(target_words, 1))
    length_score = 4.0 * length_ratio

    # 2) Structure / flow component (0–3)
    cues = _section_cues(text)
    cue_count = sum(1 for v in cues.values() if v)
    structure_score = min(3.0, (cue_count / 5.0) * 3.0)

    # 3) Topic coverage component (0–3)
    terms = _extract_topic_terms(chapter_name=chapter_name, chapter_description=chapter_description)
    coverage = _term_coverage_ratio(text, terms)
    topic_score = min(3.0, coverage * 3.0)  # coverage 0..1 => 0..3

    final = length_score + structure_score + topic_score

    return {
        "score": round(min(10.0, final), 2),
        "word_count": words,
        "target_word_count": target_words,
        "length_ratio": round(length_ratio, 3),
        "section_cues": cues,
        "section_cue_count": cue_count,
        "topic_terms": terms,
        "topic_coverage_ratio": round(coverage, 3),
        "target_level": target_level,
    }


# ------------------------------
# GEMINI COMPLETENESS ANALYSIS
# ------------------------------
def analyze_completeness_with_gemini_sync(
    content: str,
    chapter_name: str = "",
    chapter_description: str = "",
    target_level: str = "undergrad",
) -> dict:
    context_block = ""
    if chapter_name or chapter_description:
        context_block = f"\nCHAPTER CONTEXT:\n- Title: {chapter_name}\n- Description: {chapter_description}\n"

    prompt = f"""
You are evaluating COMPLETENESS of educational content for student level: {target_level}.

Definition of completeness:
- Covers the main ideas implied by the chapter title/description.
- Has enough depth for the target level.
- Has a usable learning flow (intro -> core -> examples -> summary/practice).

Important rules:
- Technical terms are allowed.
- Do NOT browse the web.

Return JSON ONLY:
{{
  "completeness": <1-10>,
  "topic_coverage": <0-5>,
  "depth": <0-5>,
  "learning_flow": <0-5>
}}

{context_block}

Content:
{content}
""".strip()

    response = llm.invoke(prompt)
    raw = getattr(response, "content", "") or ""

    data = safe_extract_json(raw)

    if not data:
        print("[ERROR] Gemini JSON parsing failed (completeness):", raw[:300])
        return {
            "completeness": 5,
            "topic_coverage": 2,
            "depth": 2,
            "learning_flow": 2,
        }

    return {
        "completeness": float(data.get("completeness", 5)),
        "topic_coverage": float(data.get("topic_coverage", 2)),
        "depth": float(data.get("depth", 2)),
        "learning_flow": float(data.get("learning_flow", 2)),
    }


async def analyze_completeness_with_gemini(
    content: str,
    chapter_name: str = "",
    chapter_description: str = "",
    target_level: str = "undergrad",
) -> dict:
    return analyze_completeness_with_gemini_sync(
        content=content,
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        target_level=target_level,
    )


# ------------------------------
# FINAL COMPLETENESS COMBINE
# ------------------------------
_COMPLETENESS_BLEND = {
    "preschool": {"py_weight": 0.35, "ai_weight": 0.65},
    "primary": {"py_weight": 0.35, "ai_weight": 0.65},
    "middle": {"py_weight": 0.32, "ai_weight": 0.68},
    "secondary": {"py_weight": 0.30, "ai_weight": 0.70},
    "hsc": {"py_weight": 0.30, "ai_weight": 0.70},
    "undergrad": {"py_weight": 0.30, "ai_weight": 0.70},
    "postgrad": {"py_weight": 0.28, "ai_weight": 0.72},
    "phd": {"py_weight": 0.25, "ai_weight": 0.75},
    "default": {"py_weight": 0.30, "ai_weight": 0.70},
}


def combine_completeness(py: dict, ai: dict, target_level: str = "undergrad") -> float:
    cfg = _COMPLETENESS_BLEND.get(target_level, _COMPLETENESS_BLEND["default"])

    py_score = float(py.get("score", 5))
    ai_main = float(ai.get("completeness", 5))

    # subscores 0–5 -> scale to 0–10
    coverage = float(ai.get("topic_coverage", 2)) * 2
    depth = float(ai.get("depth", 2)) * 2
    flow = float(ai.get("learning_flow", 2)) * 2

    ai_internal = (0.4 * ai_main) + (0.2 * coverage) + (0.2 * depth) + (0.2 * flow)

    final = (cfg["py_weight"] * py_score) + (cfg["ai_weight"] * ai_internal)

    return round(min(10.0, max(0.0, final)), 2)


# ------------------------------
# COMPLETENESS AGENT TOOL (STATE IN / STATE OUT)
# ------------------------------
@tool
async def evaluate_completeness(state: dict) -> dict:
    """Completeness Agent:

    - Reads extracted JSON from: storage/extracted_content/upload_{upload_id}.json
    - Computes Python completeness heuristics (0–10)
    - Computes Gemini completeness + subscores
    - Combines into final score (0–10)
    - Saves via MCP tool: db_save_scores_generic
    """

    upload_id = state.get("upload_id")
    target_level = state.get("target_level", "undergrad")

    if not upload_id:
        return {**state, "status": "completeness_failed", "reason": "upload_id missing"}

    extracted_data = load_extracted_json(upload_id)
    if not extracted_data:
        return {**state, "status": "completeness_failed", "reason": "json missing"}

    combined_text = (extracted_data.get("content", {}).get("combined_text") or "").strip()
    if not combined_text:
        return {**state, "status": "completeness_failed", "reason": "combined_text empty"}

    chapter_details = extracted_data.get("chapter_details", {}) or {}
    chapter_name = (chapter_details.get("chapter_name") or "").strip()
    chapter_description = (chapter_details.get("chapter_description") or "").strip()

    py_result = python_completeness_score(
        combined_text,
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        target_level=target_level,
    )

    gem_result = await analyze_completeness_with_gemini(
        combined_text,
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        target_level=target_level,
    )

    final_score = combine_completeness(py_result, gem_result, target_level=target_level)

    # Save using MCP session from graph state
    session = state.get("mcp_session")
    if session:
        save_resp = await session.call_tool(
            "db_save_scores_generic",
            {"upload_id": upload_id, "scores": {"completeness": final_score}},
        )
        try:
            print("✅ MCP saved completeness:", save_resp.content[0].text)
        except Exception:
            print("✅ MCP saved completeness")
    else:
        print("⚠️ mcp_session missing in state")

    return {
        **state,
        "status": "completeness_evaluated",
        "completeness_score": final_score,
        "python": py_result,
        "gemini": gem_result,
    }


# ------------------------------
# OPTIONAL: MCP tool registration (for direct calls)
# ------------------------------
def mcp_register(mcp) -> None:
    """Register a direct MCP tool for completeness.

    This does NOT affect your LangGraph workflow. It's an optional entrypoint.
    """

    @mcp.tool(
        name="evaluate_completeness",
        description="Evaluate completeness (0-10) for an upload using extracted content. Optionally saves to ContentScore.",
    )
    async def evaluate_completeness_tool(upload_id: int, target_level: str = "undergrad", save: bool = True) -> Dict[str, Any]:
        extracted_data = load_extracted_json(upload_id)
        if not extracted_data:
            return {"status": "error", "reason": "json missing", "upload_id": upload_id}

        combined_text = (extracted_data.get("content", {}).get("combined_text") or "").strip()
        if not combined_text:
            return {"status": "error", "reason": "combined_text empty", "upload_id": upload_id}

        chapter_details = extracted_data.get("chapter_details", {}) or {}
        chapter_name = (chapter_details.get("chapter_name") or "").strip()
        chapter_description = (chapter_details.get("chapter_description") or "").strip()

        py_result = python_completeness_score(
            combined_text,
            chapter_name=chapter_name,
            chapter_description=chapter_description,
            target_level=target_level,
        )
        gem_result = analyze_completeness_with_gemini_sync(
            combined_text,
            chapter_name=chapter_name,
            chapter_description=chapter_description,
            target_level=target_level,
        )
        final_score = combine_completeness(py_result, gem_result, target_level=target_level)

        if save:
            from accounts.models import ContentScore

            obj, _ = ContentScore.objects.get_or_create(upload_id=upload_id)
            setattr(obj, "completeness", float(final_score))
            obj.save(update_fields=["completeness"])

        return {
            "status": "ok",
            "upload_id": upload_id,
            "completeness": final_score,
            "python": py_result,
            "gemini": gem_result,
            "saved": bool(save),
        }


# ------------------------------
# OPTIONAL: A2A wrapper (kept for compatibility; returns 0-100)
# ------------------------------
try:
    from .a2a_types import A2AEnvelope, EvalInput, MetricResult
except Exception:  # pragma: no cover
    A2AEnvelope = None  # type: ignore
    EvalInput = None  # type: ignore
    MetricResult = None  # type: ignore


def _a2a_completeness_from_input(inp: "EvalInput") -> Tuple[float, Dict[str, Any], Dict[str, Any]]:
    chapter_name = (inp.chapter_title or "").strip()
    chapter_description = (inp.chapter_description or "").strip()

    py_result = python_completeness_score(
        inp.content_text,
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        target_level="undergrad",
    )

    gem_result = analyze_completeness_with_gemini_sync(
        inp.content_text,
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        target_level="undergrad",
    )

    final_0_10 = combine_completeness(py_result, gem_result, target_level="undergrad")
    return final_0_10, py_result, gem_result


def a2a_handle(envelope: "A2AEnvelope") -> Dict[str, Any]:
    """A2A handler: takes A2AEnvelope, returns JSON dict.

    Returns score 0-100 for backward compatibility.
    """

    if A2AEnvelope is None or EvalInput is None or MetricResult is None:  # pragma: no cover
        return {"error": "A2A types not available"}

    inp = EvalInput(**envelope.payload)
    final_0_10, py_result, gem_result = _a2a_completeness_from_input(inp)

    score_0_100 = int(round(final_0_10 * 10))

    result = MetricResult(
        metric="completeness",
        score=score_0_100,
        confidence=0.6,
        summary=f"Completeness score: {final_0_10}/10",
        key_issues=[
            f"word_count={py_result.get('word_count')} (target={py_result.get('target_word_count')})",
            f"topic_coverage_ratio={py_result.get('topic_coverage_ratio')}",
        ],
        suggestions=[],
        evidence_snippets=[],
    )

    return {"result": result.model_dump(), "python": py_result, "gemini": gem_result, "score_0_10": final_0_10}
