import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

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

    # 1) exact JSON parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2) JSON embedded inside additional text
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


def _extract_topic_terms(chapter_name: str = "", chapter_description: str = "", max_terms: int = 25) -> List[str]:
    """Extract a lightweight set of topic terms for rough alignment checks."""
    text = f"{chapter_name}\n{chapter_description}".strip().lower()
    if not text:
        return []

    # Split on common separators first to preserve multiword phrases
    raw_parts = re.split(r"[\n\r\t•\-–—:;,.()\[\]{}<>/\\|]+", text)
    parts: List[str] = []
    for p in raw_parts:
        p = p.strip()
        if not p:
            continue
        parts.append(p)

    # Tokenize words and keep meaningful ones
    terms: List[str] = []
    for p in parts:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9_+\-]{2,}", p)
        for w in words:
            wl = w.lower()
            if wl in _STOPWORDS:
                continue
            if len(wl) < 4:
                continue
            terms.append(wl)

    # Frequency-based selection
    freq: Dict[str, int] = {}
    for t in terms:
        freq[t] = freq.get(t, 0) + 1

    # Sort by (frequency desc, length desc)
    sorted_terms = sorted(freq.keys(), key=lambda k: (freq[k], len(k)), reverse=True)

    # Keep unique
    return sorted_terms[:max_terms]


def _term_coverage_ratio(text: str, terms: List[str]) -> float:
    """How many terms appear at least once in text."""
    if not text or not terms:
        return 0.0

    lower = text.lower()
    hit = 0
    for t in terms:
        if t and t in lower:
            hit += 1
    return hit / max(len(terms), 1)


# ------------------------------
# PYTHON ACCURACY HEURISTICS (0–10)
# ------------------------------
_ACCURACY_LEVELS = {
    # Younger levels: language must be simple; heavy hallucination signals should penalize more
    "preschool": {"min_words": 200, "coverage_floor": 0.20},
    "primary": {"min_words": 300, "coverage_floor": 0.22},
    "middle": {"min_words": 450, "coverage_floor": 0.25},
    "secondary": {"min_words": 650, "coverage_floor": 0.25},
    "hsc": {"min_words": 800, "coverage_floor": 0.25},

    # Higher education: technical content is expected; allow density
    "undergrad": {"min_words": 900, "coverage_floor": 0.22},
    "postgrad": {"min_words": 1100, "coverage_floor": 0.20},
    "phd": {"min_words": 1300, "coverage_floor": 0.18},

    "default": {"min_words": 900, "coverage_floor": 0.22},
}


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _count_numbers(text: str) -> int:
    return len(re.findall(r"\b\d+(?:\.\d+)?\b", text or ""))


def python_accuracy_score(
    content: str,
    chapter_name: str = "",
    chapter_description: str = "",
    target_level: str = "undergrad",
) -> dict:
    """Lightweight heuristic score.

    IMPORTANT: This is NOT external fact-checking. It's a consistency + reliability signal:
    - penalize placeholders / AI disclaimers / obvious low-quality artifacts
    - encourage alignment with chapter context terms
    - keep output in 0–10 (consistent with other metrics)
    """

    cfg = _ACCURACY_LEVELS.get(target_level, _ACCURACY_LEVELS["default"])

    text = (content or "").strip()
    words = _count_words(text)
    numbers = _count_numbers(text)

    terms = _extract_topic_terms(chapter_name=chapter_name, chapter_description=chapter_description)
    coverage = _term_coverage_ratio(text, terms)

    # Start with a neutral high score
    score = 10.0

    # 1) Too short: not enough substance to judge, usually lower reliability
    if words < cfg["min_words"]:
        # scale penalty smoothly
        ratio = words / max(cfg["min_words"], 1)
        # if ratio=0.5 -> penalty ~1.5 ; if 0.2 -> penalty ~2.5
        penalty = 3.0 * (1.0 - min(1.0, ratio))
        score -= penalty

    # 2) Placeholder / draft markers
    placeholder_hits = 0
    placeholder_patterns = [
        r"\b(lorem ipsum|tbd|to be (added|filled)|coming soon|placeholder)\b",
        r"\b(insert|add) (figure|diagram|image|reference)\b",
        r"\?\?\?+",
    ]
    for p in placeholder_patterns:
        placeholder_hits += len(re.findall(p, text, flags=re.IGNORECASE))
    if placeholder_hits > 0:
        score -= min(3.5, 1.5 + (placeholder_hits * 0.5))

    # 3) AI-disclaimer / refusal style language (often indicates copied chat output)
    ai_disclaimer_patterns = [
        r"as an ai language model",
        r"i (can't|cannot) (provide|verify|access)",
        r"i do not have (access|browsing)",
        r"i am unable to",
    ]
    ai_hits = 0
    for p in ai_disclaimer_patterns:
        ai_hits += len(re.findall(p, text, flags=re.IGNORECASE))
    if ai_hits > 0:
        score -= min(3.0, 1.0 + (ai_hits * 0.75))

    # 4) Alignment with chapter context (very rough)
    # If coverage is extremely low, the content might be off-topic or loosely connected.
    if terms:
        floor = float(cfg.get("coverage_floor", 0.22))
        if coverage < floor:
            # penalize up to ~2
            score -= min(2.0, (floor - coverage) * 6.0)

    # 5) Numeric density without sources can be a mild risk signal
    # (technical docs can have numbers; keep small penalty only)
    if words > 0:
        numeric_ratio = numbers / words
        if numeric_ratio > 0.04 and not re.search(r"\b(reference|references|source|sources|bibliography)\b", text, re.IGNORECASE):
            score -= 0.75

    # 6) Presence of references section / citations is a mild positive signal
    has_refs = bool(re.search(r"\b(reference|references|bibliography)\b", text, re.IGNORECASE)) or ("http://" in text or "https://" in text)
    if has_refs:
        score += 0.25

    final = max(0.0, min(10.0, score))

    return {
        "score": round(final, 2),
        "word_count": words,
        "number_count": numbers,
        "topic_terms": terms,
        "topic_coverage_ratio": round(coverage, 3),
        "placeholder_hits": placeholder_hits,
        "ai_disclaimer_hits": ai_hits,
        "has_references": has_refs,
        "target_level": target_level,
    }


# ------------------------------
# GEMINI ACCURACY ANALYSIS
# ------------------------------
def analyze_accuracy_with_gemini_sync(
    content: str,
    chapter_name: str = "",
    chapter_description: str = "",
    target_level: str = "undergrad",
) -> dict:
    """Gemini-based accuracy judgement (no external browsing): internal consistency + alignment."""

    context_block = ""
    if chapter_name or chapter_description:
        context_block = f"\nCHAPTER CONTEXT:\n- Title: {chapter_name}\n- Description: {chapter_description}\n"

    prompt = f"""
You are evaluating ACCURACY of educational content for student level: {target_level}.

Definition of accuracy in this system:
- The content should be internally consistent (no contradictions).
- The content should align with the chapter topic/description.
- The content should avoid obviously wrong or hallucinated claims.

Important rules:
- Do NOT browse the web.
- If you cannot verify a specific fact, judge whether the explanation is plausible and consistent.
- Technical terms are allowed (do NOT penalize technical vocabulary).

Return JSON ONLY:
{{
  "accuracy": <1-10>,
  "internal_consistency": <0-5>,
  "alignment_with_chapter": <0-5>,
  "factual_soundness": <0-5>
}}

{context_block}

Content:
{content}
""".strip()

    response = llm.invoke(prompt)
    raw = getattr(response, "content", "") or ""

    data = safe_extract_json(raw)

    if not data:
        print("[ERROR] Gemini JSON parsing failed (accuracy):", raw[:300])
        return {
            "accuracy": 5,
            "internal_consistency": 2,
            "alignment_with_chapter": 2,
            "factual_soundness": 2,
        }

    return {
        "accuracy": float(data.get("accuracy", 5)),
        "internal_consistency": float(data.get("internal_consistency", 2)),
        "alignment_with_chapter": float(data.get("alignment_with_chapter", 2)),
        "factual_soundness": float(data.get("factual_soundness", 2)),
    }


async def analyze_accuracy_with_gemini(
    content: str,
    chapter_name: str = "",
    chapter_description: str = "",
    target_level: str = "undergrad",
) -> dict:
    # keep async signature consistent with other agents
    return analyze_accuracy_with_gemini_sync(
        content=content,
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        target_level=target_level,
    )


# ------------------------------
# FINAL ACCURACY COMBINE
# ------------------------------
_ACCURACY_BLEND = {
    "preschool": {"py_weight": 0.30, "ai_weight": 0.70},
    "primary": {"py_weight": 0.30, "ai_weight": 0.70},
    "middle": {"py_weight": 0.28, "ai_weight": 0.72},
    "secondary": {"py_weight": 0.25, "ai_weight": 0.75},
    "hsc": {"py_weight": 0.25, "ai_weight": 0.75},
    "undergrad": {"py_weight": 0.25, "ai_weight": 0.75},
    "postgrad": {"py_weight": 0.22, "ai_weight": 0.78},
    "phd": {"py_weight": 0.20, "ai_weight": 0.80},
    "default": {"py_weight": 0.25, "ai_weight": 0.75},
}


def combine_accuracy(py: dict, ai: dict, target_level: str = "undergrad") -> float:
    cfg = _ACCURACY_BLEND.get(target_level, _ACCURACY_BLEND["default"])

    py_score = float(py.get("score", 5))
    ai_main = float(ai.get("accuracy", 5))

    # subscores 0–5 -> scale to 0–10
    consistency = float(ai.get("internal_consistency", 2)) * 2
    alignment = float(ai.get("alignment_with_chapter", 2)) * 2
    factual = float(ai.get("factual_soundness", 2)) * 2

    ai_internal = (0.4 * ai_main) + (0.2 * consistency) + (0.2 * alignment) + (0.2 * factual)

    final = (cfg["py_weight"] * py_score) + (cfg["ai_weight"] * ai_internal)

    return round(min(10.0, max(0.0, final)), 2)


# ------------------------------
# ACCURACY AGENT TOOL (STATE IN / STATE OUT)
# ------------------------------
@tool
async def evaluate_accuracy(state: dict) -> dict:
    """Accuracy Agent:

    - Reads extracted JSON from: storage/extracted_content/upload_{upload_id}.json
    - Computes a Python heuristic accuracy signal (0–10)
    - Computes Gemini accuracy + subscores
    - Combines into final score (0–10)
    - Saves via MCP tool: db_save_scores_generic

    Note: This is NOT external fact-checking; it evaluates internal consistency and alignment.
    """

    upload_id = state.get("upload_id")
    target_level = state.get("target_level", "undergrad")

    if not upload_id:
        return {**state, "status": "accuracy_failed", "reason": "upload_id missing"}

    extracted_data = load_extracted_json(upload_id)
    if not extracted_data:
        return {**state, "status": "accuracy_failed", "reason": "json missing"}

    combined_text = (extracted_data.get("content", {}).get("combined_text") or "").strip()
    if not combined_text:
        return {**state, "status": "accuracy_failed", "reason": "combined_text empty"}

    chapter_details = extracted_data.get("chapter_details", {}) or {}
    chapter_name = (chapter_details.get("chapter_name") or "").strip()
    chapter_description = (chapter_details.get("chapter_description") or "").strip()

    py_result = python_accuracy_score(
        combined_text,
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        target_level=target_level,
    )

    gem_result = await analyze_accuracy_with_gemini(
        combined_text,
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        target_level=target_level,
    )

    final_score = combine_accuracy(py_result, gem_result, target_level=target_level)

    # Save using MCP session from graph state
    session = state.get("mcp_session")
    if session:
        save_resp = await session.call_tool(
            "db_save_scores_generic",
            {"upload_id": upload_id, "scores": {"accuracy": final_score}},
        )
        try:
            print("✅ MCP saved accuracy:", save_resp.content[0].text)
        except Exception:
            print("✅ MCP saved accuracy")
    else:
        print("⚠️ mcp_session missing in state")

    return {
        **state,
        "status": "accuracy_evaluated",
        "accuracy_score": final_score,
        "python": py_result,
        "gemini": gem_result,
    }


# ------------------------------
# OPTIONAL: MCP tool registration (for direct calls)
# ------------------------------
def mcp_register(mcp) -> None:
    """Register a direct MCP tool for accuracy.

    This does NOT affect your LangGraph workflow. It's an optional entrypoint.
    """

    @mcp.tool(
        name="evaluate_accuracy",
        description="Evaluate accuracy (0-10) for an upload using extracted content. Optionally saves to ContentScore.",
    )
    async def evaluate_accuracy_tool(upload_id: int, target_level: str = "undergrad", save: bool = True) -> Dict[str, Any]:
        extracted_data = load_extracted_json(upload_id)
        if not extracted_data:
            return {"status": "error", "reason": "json missing", "upload_id": upload_id}

        combined_text = (extracted_data.get("content", {}).get("combined_text") or "").strip()
        if not combined_text:
            return {"status": "error", "reason": "combined_text empty", "upload_id": upload_id}

        chapter_details = extracted_data.get("chapter_details", {}) or {}
        chapter_name = (chapter_details.get("chapter_name") or "").strip()
        chapter_description = (chapter_details.get("chapter_description") or "").strip()

        py_result = python_accuracy_score(
            combined_text,
            chapter_name=chapter_name,
            chapter_description=chapter_description,
            target_level=target_level,
        )
        gem_result = analyze_accuracy_with_gemini_sync(
            combined_text,
            chapter_name=chapter_name,
            chapter_description=chapter_description,
            target_level=target_level,
        )
        final_score = combine_accuracy(py_result, gem_result, target_level=target_level)

        if save:
            # Import lazily so this module can be imported outside Django contexts too.
            from accounts.models import ContentScore

            obj, _ = ContentScore.objects.get_or_create(upload_id=upload_id)
            # Accuracy field must exist on model (migration provided in this PR)
            setattr(obj, "accuracy", float(final_score))
            obj.save(update_fields=["accuracy"])

        return {
            "status": "ok",
            "upload_id": upload_id,
            "accuracy": final_score,
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


def _a2a_accuracy_from_input(inp: "EvalInput") -> Tuple[float, Dict[str, Any], Dict[str, Any]]:
    chapter_name = (inp.chapter_title or "").strip()
    chapter_description = (inp.chapter_description or "").strip()

    py_result = python_accuracy_score(
        inp.content_text,
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        target_level="undergrad",
    )

    gem_result = analyze_accuracy_with_gemini_sync(
        inp.content_text,
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        target_level="undergrad",
    )

    final_0_10 = combine_accuracy(py_result, gem_result, target_level="undergrad")
    return final_0_10, py_result, gem_result


def a2a_handle(envelope: "A2AEnvelope") -> Dict[str, Any]:
    """A2A handler: takes A2AEnvelope, returns JSON dict.

    Returns score 0-100 for backward compatibility.
    """

    if A2AEnvelope is None or EvalInput is None or MetricResult is None:  # pragma: no cover
        return {"error": "A2A types not available"}

    inp = EvalInput(**envelope.payload)
    final_0_10, py_result, gem_result = _a2a_accuracy_from_input(inp)

    score_0_100 = int(round(final_0_10 * 10))

    result = MetricResult(
        metric="accuracy",
        score=score_0_100,
        confidence=0.6,
        summary=f"Accuracy (internal consistency + alignment) score: {final_0_10}/10",
        key_issues=[
            f"ai_disclaimer_hits={py_result.get('ai_disclaimer_hits')}",
            f"placeholder_hits={py_result.get('placeholder_hits')}",
        ],
        suggestions=[],
        evidence_snippets=[],
    )

    return {"result": result.model_dump(), "python": py_result, "gemini": gem_result, "score_0_10": final_0_10}
