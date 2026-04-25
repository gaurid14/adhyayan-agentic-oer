"""
langgraph_agents/agents/accuracy.py

Adaptive Accuracy Evaluation Agent.

Six adaptive pillars:
  1. Domain / context-aware dynamic prompts   — live syllabus via get_rag_context()
  2. Multi-run self-consistency               — ParameterStats updated each run
  3. Confidence scoring                       — within-run py↔gemini agreement proxy
  4. Variance-based adaptive weights          — blend shifts toward Python when AI is unreliable
  5. Guardrail layer                          — clamp, disagreement, artefact caps
  6. Final scoring                            — guardrailed, adaptively-blended 0–10 score
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from asgiref.sync import sync_to_async
from langchain.tools import tool
from langsmith import traceable

from accounts.models import ContentScore, OutcomeChapterMapping, UploadCheck
from langgraph_agents.services.gemini_service import llm
from langgraph_agents.services.adaptive_stats import (
    apply_guardrails,
    compute_run_confidence,
    get_adaptive_blend,
    get_insight_async,
    update_parameter_stats,
)

# -----------------------------------------------------------------------
# PATH SETTINGS
# -----------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXTRACTED_JSON_DIR = os.path.join(BASE_DIR, "storage", "extracted_content")

_METRIC = "accuracy"


def load_extracted_json(upload_id: int) -> dict:
    json_path = os.path.join(EXTRACTED_JSON_DIR, f"upload_{upload_id}.json")
    if not os.path.exists(json_path):
        return {}
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


# -----------------------------------------------------------------------
# SAFE JSON EXTRACTION
# -----------------------------------------------------------------------
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


# -----------------------------------------------------------------------
# RAG CONTEXT  (live syllabus from DB)
# -----------------------------------------------------------------------
@sync_to_async
def get_rag_context(upload_id: int) -> dict:
    try:
        upload = UploadCheck.objects.select_related(
            "chapter__course__department__program"
        ).get(id=upload_id)

        chapter = upload.chapter
        course  = chapter.course

        domain  = course.department.program.program_name
        subject = course.course_name

        mappings = OutcomeChapterMapping.objects.filter(chapter=chapter)
        outcomes = [m.outcome.description for m in mappings]
        syllabus = "\n".join(outcomes[:3])

        chapter_desc = chapter.description or ""

        best_qs = ContentScore.objects.filter(
            upload__chapter=chapter, is_best=True
        )[:1]
        best_text = ""
        for b in best_qs:
            try:
                data = load_extracted_json(b.upload.id)
                txt  = data.get("content", {}).get("combined_text", "")
                if txt:
                    best_text = txt[:400]
            except Exception:
                continue

        return {
            "domain":       domain,
            "subject":      subject,
            "chapter":      chapter.chapter_name,
            "syllabus":     syllabus,
            "chapter_desc": chapter_desc,
            "best_content": best_text,
            "outcomes":     outcomes,
        }
    except Exception:
        return {}


# -----------------------------------------------------------------------
# TOPIC TERMS  (chapter + syllabus outcomes)
# -----------------------------------------------------------------------
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "onto", "over",
    "under", "about", "above", "below", "between", "within", "without", "a", "an",
    "to", "of", "in", "on", "at", "by", "is", "are", "was", "were", "be", "been",
    "being", "as", "it", "its", "or", "not", "we", "you", "your", "they", "their",
    "them", "these", "those", "will", "can", "may", "might", "should", "must", "also",
}


def _extract_topic_terms(
    chapter_name: str = "",
    chapter_description: str = "",
    syllabus_outcomes: Optional[List[str]] = None,
    max_terms: int = 25,
) -> List[str]:
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
        for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_+\-]{2,}", p):
            wl = w.lower()
            if wl not in _STOPWORDS and len(wl) >= 4:
                terms.append(wl)

    freq: Dict[str, int] = {}
    for t in terms:
        freq[t] = freq.get(t, 0) + 1

    return sorted(freq.keys(), key=lambda k: (freq[k], len(k)), reverse=True)[:max_terms]


def _term_coverage_ratio(text: str, terms: List[str]) -> float:
    if not text or not terms:
        return 0.0
    lower = text.lower()
    return sum(1 for t in terms if t and t in lower) / max(len(terms), 1)


# -----------------------------------------------------------------------
# LEVEL SETTINGS
# -----------------------------------------------------------------------
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

_ACCURACY_BLEND = {
    "preschool":  {"py": 0.30, "ai": 0.70},
    "primary":    {"py": 0.30, "ai": 0.70},
    "middle":     {"py": 0.28, "ai": 0.72},
    "secondary":  {"py": 0.25, "ai": 0.75},
    "hsc":        {"py": 0.25, "ai": 0.75},
    "undergrad":  {"py": 0.25, "ai": 0.75},
    "postgrad":   {"py": 0.22, "ai": 0.78},
    "phd":        {"py": 0.20, "ai": 0.80},
    "default":    {"py": 0.25, "ai": 0.75},
}


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))

def _count_numbers(text: str) -> int:
    return len(re.findall(r"\b\d+(?:\.\d+)?\b", text or ""))


# -----------------------------------------------------------------------
# PYTHON ACCURACY HEURISTIC (0–10)
# -----------------------------------------------------------------------
def python_accuracy_score(
    content: str,
    chapter_name: str = "",
    chapter_description: str = "",
    syllabus_outcomes: Optional[List[str]] = None,
    target_level: str = "undergrad",
) -> dict:
    cfg  = _ACCURACY_LEVELS.get(target_level, _ACCURACY_LEVELS["default"])
    text = (content or "").strip()

    words   = _count_words(text)
    numbers = _count_numbers(text)
    terms   = _extract_topic_terms(chapter_name, chapter_description, syllabus_outcomes)
    coverage = _term_coverage_ratio(text, terms)

    score = 10.0

    if words < cfg["min_words"]:
        ratio = words / max(cfg["min_words"], 1)
        score -= 3.0 * (1.0 - min(1.0, ratio))

    placeholder_hits = 0
    for p in [
        r"\b(lorem ipsum|tbd|to be (added|filled)|coming soon|placeholder)\b",
        r"\b(insert|add) (figure|diagram|image|reference)\b",
        r"\?\?\?+",
    ]:
        placeholder_hits += len(re.findall(p, text, flags=re.IGNORECASE))
    if placeholder_hits > 0:
        score -= min(3.5, 1.5 + placeholder_hits * 0.5)

    ai_hits = 0
    for p in [
        r"as an ai language model",
        r"i (can't|cannot) (provide|verify|access)",
        r"i do not have (access|browsing)",
        r"i am unable to",
    ]:
        ai_hits += len(re.findall(p, text, flags=re.IGNORECASE))
    if ai_hits > 0:
        score -= min(3.0, 1.0 + ai_hits * 0.75)

    if terms:
        floor = float(cfg.get("coverage_floor", 0.22))
        if coverage < floor:
            score -= min(2.0, (floor - coverage) * 6.0)

    if words > 0:
        if (numbers / words) > 0.04 and not re.search(
            r"\b(reference|references|source|sources|bibliography)\b", text, re.IGNORECASE
        ):
            score -= 0.75

    has_refs = bool(re.search(r"\b(reference|references|bibliography)\b", text, re.IGNORECASE)) or (
        "http://" in text or "https://" in text
    )
    if has_refs:
        score += 0.25

    return {
        "score":                 round(max(0.0, min(10.0, score)), 2),
        "word_count":            words,
        "number_count":          numbers,
        "topic_terms":           terms,
        "topic_coverage_ratio":  round(coverage, 3),
        "placeholder_hits":      placeholder_hits,
        "ai_disclaimer_hits":    ai_hits,
        "has_references":        has_refs,
        "target_level":          target_level,
    }


# -----------------------------------------------------------------------
# GEMINI ACCURACY ANALYSIS  (context-aware + adaptive insight)
# -----------------------------------------------------------------------
def analyze_accuracy_with_gemini_sync(
    content: str,
    rag: dict,
    target_level: str = "undergrad",
    insight: str = "",
) -> dict:
    prompt = f"""
You are evaluating ACCURACY of educational content.

Student Level: {target_level}

-------------------------
DOMAIN CONTEXT
Domain:  {rag.get("domain",  "")}
Subject: {rag.get("subject", "")}
Chapter: {rag.get("chapter", "")}
-------------------------

SYLLABUS (Course Outcomes for this chapter):
{rag.get("syllabus", "")}

CHAPTER DESCRIPTION:
{rag.get("chapter_desc", "")}

REFERENCE (well-scored content for this chapter):
{rag.get("best_content", "")}

-------------------------
PAST LEARNING INSIGHTS (adaptive signal):
{insight}
-------------------------

Definition of accuracy in this system:
- Content must be internally consistent (no contradictions).
- Content must align with the SYLLABUS outcomes listed above.
- Content must avoid obviously wrong or hallucinated claims.
- Technical terms are ALLOWED.
- If content is off-syllabus → reduce alignment_with_syllabus score.
- Do NOT browse the web.

Return JSON ONLY:
{{
  "accuracy":               <1-10>,
  "internal_consistency":   <0-5>,
  "alignment_with_syllabus":<0-5>,
  "factual_soundness":      <0-5>
}}

Content:
{content[:4000]}
""".strip()

    response = llm.invoke(prompt)
    raw  = getattr(response, "content", "") or ""
    data = safe_extract_json(raw)

    if not data:
        print("[ERROR] Gemini JSON parsing failed (accuracy):", raw[:300])
        return {"accuracy": 5, "internal_consistency": 2, "alignment_with_syllabus": 2, "factual_soundness": 2}

    return {
        "accuracy":                float(data.get("accuracy",                5)),
        "internal_consistency":    float(data.get("internal_consistency",    2)),
        "alignment_with_syllabus": float(data.get("alignment_with_syllabus", 2)),
        "factual_soundness":       float(data.get("factual_soundness",       2)),
    }


async def analyze_accuracy_with_gemini(
    content: str,
    rag: dict,
    target_level: str = "undergrad",
    insight: str = "",
) -> dict:
    return analyze_accuracy_with_gemini_sync(content=content, rag=rag, target_level=target_level, insight=insight)


# -----------------------------------------------------------------------
# ADAPTIVE COMBINE
# -----------------------------------------------------------------------
def combine_accuracy_adaptive(py: dict, ai: dict, target_level: str = "undergrad") -> float:
    base = _ACCURACY_BLEND.get(target_level, _ACCURACY_BLEND["default"])
    py_w, ai_w = get_adaptive_blend(parameter=_METRIC, base_py=base["py"], base_ai=base["ai"])

    py_score    = float(py.get("score", 5))
    ai_main     = float(ai.get("accuracy", 5))
    consistency = float(ai.get("internal_consistency",    2)) * 2
    alignment   = float(ai.get("alignment_with_syllabus", 2)) * 2
    factual     = float(ai.get("factual_soundness",       2)) * 2

    ai_internal = (0.40 * ai_main) + (0.20 * consistency) + (0.20 * alignment) + (0.20 * factual)
    final       = (py_w * py_score) + (ai_w * ai_internal)

    return round(min(10.0, max(0.0, final)), 2)


# -----------------------------------------------------------------------
# ACCURACY AGENT TOOL
# -----------------------------------------------------------------------
@tool
@traceable(name="Accuracy Agent")
async def evaluate_accuracy(state: dict) -> dict:
    """Adaptive Accuracy Agent — syllabus-aware, multi-run adaptive, guardrailed."""

    upload_id    = state.get("upload_id")
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
    chapter_name    = (chapter_details.get("chapter_name")        or "").strip()
    chapter_desc    = (chapter_details.get("chapter_description") or "").strip()

    # ── Pillar 1: live RAG context ──
    rag               = await get_rag_context(upload_id)
    syllabus_outcomes = rag.get("outcomes", [])
    print(f"\n{'='*60}")
    print(f"🎯 [ACCURACY] upload_id={upload_id} level={target_level}")
    print(f"📚 [ACCURACY] Domain={rag.get('domain','-')} | Subject={rag.get('subject','-')} | Chapter={rag.get('chapter','-')}")
    print(f"📋 [ACCURACY] Syllabus outcomes loaded: {len(syllabus_outcomes)}")

    # ── Pillar 2: adaptive insight ──
    insight = await get_insight_async(_METRIC)
    print(f"🧠 [ACCURACY] Adaptive insight: {insight}")

    # ── Pillars 3/4: scoring ──
    py_result  = python_accuracy_score(
        combined_text, chapter_name=chapter_name, chapter_description=chapter_desc,
        syllabus_outcomes=syllabus_outcomes, target_level=target_level,
    )
    print(f"🐍 [ACCURACY] Python score={py_result['score']} | words={py_result['word_count']} | coverage={py_result['topic_coverage_ratio']} | placeholders={py_result['placeholder_hits']}")

    gem_result = await analyze_accuracy_with_gemini(
        combined_text, rag=rag, target_level=target_level, insight=insight,
    )
    print(f"🤖 [ACCURACY] Gemini score={gem_result['accuracy']} | consistency={gem_result['internal_consistency']} | syllabus_align={gem_result['alignment_with_syllabus']} | factual={gem_result['factual_soundness']}")

    raw_score = combine_accuracy_adaptive(py_result, gem_result, target_level)
    print(f"🔀 [ACCURACY] Combined raw score={raw_score}")

    # ── Pillar 5: guardrail ──
    final_score, guard_warnings = apply_guardrails(raw_score, py_result, gem_result, metric_name=_METRIC)
    if guard_warnings:
        for w in guard_warnings:
            print(w)
    print(f"✅ [ACCURACY] FINAL SCORE = {final_score}")

    # ── Update stats for next run ──
    run_confidence = compute_run_confidence(float(py_result["score"]), float(gem_result["accuracy"]))
    print(f"📊 [ACCURACY] Run confidence={run_confidence}")
    await update_parameter_stats(_METRIC, final_score, run_confidence)

    # ── Save ──
    session = state.get("mcp_session")
    if session:
        save_resp = await session.call_tool(
            "db_save_scores_generic",
            {"upload_id": upload_id, "scores": {"accuracy": final_score}},
        )
        try:
            print("💾 [ACCURACY] MCP saved:", save_resp.content[0].text)
        except Exception:
            print(f"💾 [ACCURACY] MCP saved (score={final_score})")
    else:
        print("⚠️ [ACCURACY] mcp_session missing in state")
    print(f"{'='*60}\n")

    return {
        **state,
        "status":         "accuracy_evaluated",
        "accuracy_score": final_score,
        "python":         py_result,
        "gemini":         gem_result,
        "guardrails":     guard_warnings,
        "run_confidence": run_confidence,
    }


# -----------------------------------------------------------------------
# MCP TOOL REGISTRATION  (optional)
# -----------------------------------------------------------------------
def mcp_register(mcp) -> None:
    @mcp.tool(
        name="evaluate_accuracy",
        description="Adaptive accuracy evaluation (0-10) with live syllabus and guardrails.",
    )
    async def evaluate_accuracy_tool(
        upload_id: int, target_level: str = "undergrad", save: bool = True,
    ) -> Dict[str, Any]:
        extracted_data = load_extracted_json(upload_id)
        if not extracted_data:
            return {"status": "error", "reason": "json missing", "upload_id": upload_id}

        combined_text = (extracted_data.get("content", {}).get("combined_text") or "").strip()
        if not combined_text:
            return {"status": "error", "reason": "combined_text empty", "upload_id": upload_id}

        chapter_details = extracted_data.get("chapter_details", {}) or {}
        chapter_name    = (chapter_details.get("chapter_name")        or "").strip()
        chapter_desc    = (chapter_details.get("chapter_description") or "").strip()

        rag               = await get_rag_context(upload_id)
        syllabus_outcomes = rag.get("outcomes", [])
        insight           = await get_insight_async(_METRIC)

        py_result  = python_accuracy_score(
            combined_text, chapter_name=chapter_name, chapter_description=chapter_desc,
            syllabus_outcomes=syllabus_outcomes, target_level=target_level,
        )
        gem_result = analyze_accuracy_with_gemini_sync(
            combined_text, rag=rag, target_level=target_level, insight=insight,
        )
        raw_score         = combine_accuracy_adaptive(py_result, gem_result, target_level)
        final_score, _    = apply_guardrails(raw_score, py_result, gem_result, _METRIC)

        run_confidence = compute_run_confidence(float(py_result["score"]), float(gem_result["accuracy"]))
        await update_parameter_stats(_METRIC, final_score, run_confidence)

        if save:
            from accounts.models import ContentScore
            obj, _ = ContentScore.objects.get_or_create(upload_id=upload_id)
            obj.accuracy = float(final_score)
            obj.save(update_fields=["accuracy"])

        return {
            "status": "ok", "upload_id": upload_id, "accuracy": final_score,
            "python": py_result, "gemini": gem_result, "saved": bool(save),
        }
