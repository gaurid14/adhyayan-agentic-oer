import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from asgiref.sync import sync_to_async
from langchain.tools import tool

from accounts.models import ContentScore, OutcomeChapterMapping, UploadCheck
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
# RAG CONTEXT (live syllabus from DB)
# ------------------------------
@sync_to_async
def get_rag_context(upload_id: int) -> dict:
    try:
        upload = UploadCheck.objects.select_related(
            "chapter__course__department__program"
        ).get(id=upload_id)

        chapter = upload.chapter
        course = chapter.course

        domain = course.department.program.program_name
        subject = course.course_name

        # Live syllabus: outcomes mapped to this chapter
        mappings = OutcomeChapterMapping.objects.filter(chapter=chapter)
        outcomes = [m.outcome.description for m in mappings]
        syllabus = "\n".join(outcomes[:3])  # keep focused

        chapter_desc = chapter.description or ""

        # Best reference content (optional)
        best_qs = ContentScore.objects.filter(
            upload__chapter=chapter,
            is_best=True
        )[:1]

        best_text = ""
        for b in best_qs:
            try:
                data = load_extracted_json(b.upload.id)
                txt = data.get("content", {}).get("combined_text", "")
                if txt:
                    best_text = txt[:400]
            except Exception:
                continue

        return {
            "domain": domain,
            "subject": subject,
            "chapter": chapter.chapter_name,
            "syllabus": syllabus,
            "chapter_desc": chapter_desc,
            "best_content": best_text,
            # raw outcomes list for python heuristic term extraction
            "outcomes": outcomes,
        }

    except Exception:
        return {}


# ------------------------------
# TOPIC EXTRACTION (from syllabus outcomes, not just description)
# ------------------------------
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "onto", "over", "under",
    "about", "above", "below", "between", "within", "without", "a", "an", "to", "of", "in",
    "on", "at", "by", "is", "are", "was", "were", "be", "been", "being", "as", "it", "its",
    "or", "not", "we", "you", "your", "they", "their", "them", "these", "those", "will", "can",
    "may", "might", "should", "must", "also",
}


def _extract_topic_terms(
    chapter_name: str = "",
    chapter_description: str = "",
    syllabus_outcomes: Optional[List[str]] = None,
    max_terms: int = 25,
) -> List[str]:
    """
    Extract topic terms from chapter name, description AND live syllabus outcomes.
    This ensures coverage is measured against the actual syllabus, not just
    predefined words in the chapter description.
    """
    outcomes_text = "\n".join(syllabus_outcomes or [])
    text = f"{chapter_name}\n{chapter_description}\n{outcomes_text}".strip().lower()
    if not text:
        return []

    raw_parts = re.split(r"[\n\r\t•\-–—:;,.()[\]{}<>/\\|]+", text)
    terms: List[str] = []

    for p in raw_parts:
        p = p.strip()
        if not p:
            continue
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9_+\-]{2,}", p)
        for w in words:
            wl = w.lower()
            if wl in _STOPWORDS or len(wl) < 4:
                continue
            terms.append(wl)

    freq: Dict[str, int] = {}
    for t in terms:
        freq[t] = freq.get(t, 0) + 1

    sorted_terms = sorted(freq.keys(), key=lambda k: (freq[k], len(k)), reverse=True)
    return sorted_terms[:max_terms]


def _term_coverage_ratio(text: str, terms: List[str]) -> float:
    """How many terms appear at least once in text."""
    if not text or not terms:
        return 0.0
    lower = text.lower()
    hit = sum(1 for t in terms if t and t in lower)
    return hit / max(len(terms), 1)


# ------------------------------
# PYTHON ACCURACY HEURISTICS (0–10)
# ------------------------------
_ACCURACY_LEVELS = {
    "preschool":  {"min_words": 200,  "coverage_floor": 0.20},
    "primary":    {"min_words": 300,  "coverage_floor": 0.22},
    "middle":     {"min_words": 450,  "coverage_floor": 0.25},
    "secondary":  {"min_words": 650,  "coverage_floor": 0.25},
    "hsc":        {"min_words": 800,  "coverage_floor": 0.25},
    "undergrad":  {"min_words": 900,  "coverage_floor": 0.22},
    "postgrad":   {"min_words": 1100, "coverage_floor": 0.20},
    "phd":        {"min_words": 1300, "coverage_floor": 0.18},
    "default":    {"min_words": 900,  "coverage_floor": 0.22},
}


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _count_numbers(text: str) -> int:
    return len(re.findall(r"\b\d+(?:\.\d+)?\b", text or ""))


def python_accuracy_score(
    content: str,
    chapter_name: str = "",
    chapter_description: str = "",
    syllabus_outcomes: Optional[List[str]] = None,
    target_level: str = "undergrad",
) -> dict:
    """
    Heuristic accuracy signal (0–10).
    Coverage is measured against live syllabus outcomes + chapter context,
    not just predefined topic words.
    """
    cfg = _ACCURACY_LEVELS.get(target_level, _ACCURACY_LEVELS["default"])

    text = (content or "").strip()
    words = _count_words(text)
    numbers = _count_numbers(text)

    terms = _extract_topic_terms(
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        syllabus_outcomes=syllabus_outcomes,
    )
    coverage = _term_coverage_ratio(text, terms)

    score = 10.0

    # 1) Too short
    if words < cfg["min_words"]:
        ratio = words / max(cfg["min_words"], 1)
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

    # 3) AI-disclaimer / refusal markers
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

    # 4) Syllabus alignment (coverage against actual outcomes)
    if terms:
        floor = float(cfg.get("coverage_floor", 0.22))
        if coverage < floor:
            score -= min(2.0, (floor - coverage) * 6.0)

    # 5) Numeric density without references
    if words > 0:
        numeric_ratio = numbers / words
        if numeric_ratio > 0.04 and not re.search(
            r"\b(reference|references|source|sources|bibliography)\b", text, re.IGNORECASE
        ):
            score -= 0.75

    # 6) References are a mild positive signal
    has_refs = bool(re.search(r"\b(reference|references|bibliography)\b", text, re.IGNORECASE)) or (
        "http://" in text or "https://" in text
    )
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
    rag: dict,
    target_level: str = "undergrad",
) -> dict:
    """
    Gemini accuracy judge — uses live syllabus context (domain, subject, outcomes)
    instead of only a chapter description block.
    """
    prompt = f"""
You are evaluating ACCURACY of educational content.

Student Level: {target_level}

-------------------------
DOMAIN CONTEXT
Domain: {rag.get("domain", "")}
Subject: {rag.get("subject", "")}
Chapter: {rag.get("chapter", "")}
-------------------------

SYLLABUS (Course Outcomes for this chapter):
{rag.get("syllabus", "")}

CHAPTER DESCRIPTION:
{rag.get("chapter_desc", "")}

REFERENCE (GOOD CONTENT):
{rag.get("best_content", "")}

-------------------------
Definition of accuracy in this system:
- The content should be internally consistent (no contradictions).
- The content must align with the SYLLABUS outcomes listed above.
- The content should avoid obviously wrong or hallucinated claims.

Important rules:
- Do NOT browse the web.
- Technical terms are allowed (do NOT penalize technical vocabulary).
- If content is NOT relevant to the syllabus → reduce accuracy score.
- Penalize content that contradicts the expected syllabus topics.

Return JSON ONLY:
{{
  "accuracy": <1-10>,
  "internal_consistency": <0-5>,
  "alignment_with_syllabus": <0-5>,
  "factual_soundness": <0-5>
}}

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
            "alignment_with_syllabus": 2,
            "factual_soundness": 2,
        }

    return {
        "accuracy": float(data.get("accuracy", 5)),
        "internal_consistency": float(data.get("internal_consistency", 2)),
        "alignment_with_syllabus": float(data.get("alignment_with_syllabus", 2)),
        "factual_soundness": float(data.get("factual_soundness", 2)),
    }


async def analyze_accuracy_with_gemini(
    content: str,
    rag: dict,
    target_level: str = "undergrad",
) -> dict:
    return analyze_accuracy_with_gemini_sync(content=content, rag=rag, target_level=target_level)


# ------------------------------
# FINAL ACCURACY COMBINE
# ------------------------------
_ACCURACY_BLEND = {
    "preschool":  {"py_weight": 0.30, "ai_weight": 0.70},
    "primary":    {"py_weight": 0.30, "ai_weight": 0.70},
    "middle":     {"py_weight": 0.28, "ai_weight": 0.72},
    "secondary":  {"py_weight": 0.25, "ai_weight": 0.75},
    "hsc":        {"py_weight": 0.25, "ai_weight": 0.75},
    "undergrad":  {"py_weight": 0.25, "ai_weight": 0.75},
    "postgrad":   {"py_weight": 0.22, "ai_weight": 0.78},
    "phd":        {"py_weight": 0.20, "ai_weight": 0.80},
    "default":    {"py_weight": 0.25, "ai_weight": 0.75},
}


def combine_accuracy(py: dict, ai: dict, target_level: str = "undergrad") -> float:
    cfg = _ACCURACY_BLEND.get(target_level, _ACCURACY_BLEND["default"])

    py_score = float(py.get("score", 5))
    ai_main = float(ai.get("accuracy", 5))

    # subscores 0–5 -> scale to 0–10
    consistency = float(ai.get("internal_consistency", 2)) * 2
    alignment   = float(ai.get("alignment_with_syllabus", 2)) * 2
    factual     = float(ai.get("factual_soundness", 2)) * 2

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
    - Fetches live syllabus (course outcomes) from DB via get_rag_context()
    - Computes Python heuristic accuracy score (0–10) using syllabus terms
    - Computes Gemini accuracy + subscores against the syllabus
    - Combines into final score (0–10)
    - Saves via MCP tool: db_save_scores_generic
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

    # Fetch live syllabus from DB
    rag = await get_rag_context(upload_id)
    syllabus_outcomes = rag.get("outcomes", [])

    py_result = python_accuracy_score(
        combined_text,
        chapter_name=chapter_name,
        chapter_description=chapter_description,
        syllabus_outcomes=syllabus_outcomes,
        target_level=target_level,
    )

    gem_result = await analyze_accuracy_with_gemini(
        combined_text,
        rag=rag,
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
    """Register a direct MCP tool for accuracy."""

    @mcp.tool(
        name="evaluate_accuracy",
        description="Evaluate accuracy (0-10) for an upload using extracted content and live syllabus.",
    )
    async def evaluate_accuracy_tool(upload_id: int, target_level: str = "undergrad", save: bool = True) -> Dict[str, Any]:
        from asgiref.sync import sync_to_async as _s2a

        extracted_data = load_extracted_json(upload_id)
        if not extracted_data:
            return {"status": "error", "reason": "json missing", "upload_id": upload_id}

        combined_text = (extracted_data.get("content", {}).get("combined_text") or "").strip()
        if not combined_text:
            return {"status": "error", "reason": "combined_text empty", "upload_id": upload_id}

        chapter_details = extracted_data.get("chapter_details", {}) or {}
        chapter_name = (chapter_details.get("chapter_name") or "").strip()
        chapter_description = (chapter_details.get("chapter_description") or "").strip()

        rag = await get_rag_context(upload_id)
        syllabus_outcomes = rag.get("outcomes", [])

        py_result = python_accuracy_score(
            combined_text,
            chapter_name=chapter_name,
            chapter_description=chapter_description,
            syllabus_outcomes=syllabus_outcomes,
            target_level=target_level,
        )
        gem_result = analyze_accuracy_with_gemini_sync(
            combined_text,
            rag=rag,
            target_level=target_level,
        )
        final_score = combine_accuracy(py_result, gem_result, target_level=target_level)

        if save:
            from accounts.models import ContentScore
            obj, _ = ContentScore.objects.get_or_create(upload_id=upload_id)
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
