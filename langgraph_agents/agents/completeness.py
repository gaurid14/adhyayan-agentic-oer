"""
langgraph_agents/agents/completeness.py

Adaptive Completeness Evaluation Agent.

Six adaptive pillars:
  1. Domain / context-aware dynamic prompts   — live syllabus via get_rag_context()
  2. Multi-run self-consistency               — ParameterStats updated each run
  3. Confidence scoring                       — within-run py↔gemini agreement proxy
  4. Variance-based adaptive weights          — blend shifts toward Python when AI is unreliable
  5. Guardrail layer                          — clamp, disagreement, minimum-content cap
  6. Final scoring                            — guardrailed, adaptively-blended 0–10 score
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from asgiref.sync import sync_to_async
from langchain.tools import tool

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

_METRIC = "completeness"
_MIN_WORDS_GUARDRAIL = 300


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
    max_terms: int = 30,
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
_COMPLETENESS_TARGET_WORDS = {
    "preschool":  250,  "primary":   400, "middle":   650,
    "secondary":  900,  "hsc":       1100, "undergrad":1400,
    "postgrad":   1700, "phd":       2000, "default":  1400,
}

_COMPLETENESS_BLEND = {
    "preschool":  {"py": 0.35, "ai": 0.65},
    "primary":    {"py": 0.35, "ai": 0.65},
    "middle":     {"py": 0.32, "ai": 0.68},
    "secondary":  {"py": 0.30, "ai": 0.70},
    "hsc":        {"py": 0.30, "ai": 0.70},
    "undergrad":  {"py": 0.30, "ai": 0.70},
    "postgrad":   {"py": 0.28, "ai": 0.72},
    "phd":        {"py": 0.25, "ai": 0.75},
    "default":    {"py": 0.30, "ai": 0.70},
}


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))

def _section_cues(text: str) -> Dict[str, bool]:
    lower = (text or "").lower()
    return {
        "has_intro":    bool(re.search(r"\b(introduction|overview|objective|aim)\b",           lower)),
        "has_core":     bool(re.search(r"\b(concept|theory|definition|explain|method|procedure)\b", lower)),
        "has_examples": bool(re.search(r"\b(example|illustration|case study|scenario)\b",     lower)),
        "has_summary":  bool(re.search(r"\b(summary|conclusion|recap|key takeaways)\b",       lower)),
        "has_practice": bool(re.search(r"\b(exercise|question|quiz|activity|practice)\b",     lower)),
    }


# -----------------------------------------------------------------------
# PYTHON COMPLETENESS HEURISTIC (0–10)
# -----------------------------------------------------------------------
def python_completeness_score(
    content: str,
    chapter_name: str = "",
    chapter_description: str = "",
    syllabus_outcomes: Optional[List[str]] = None,
    target_level: str = "undergrad",
) -> dict:
    text         = (content or "").strip()
    words        = _count_words(text)
    target_words = _COMPLETENESS_TARGET_WORDS.get(target_level, _COMPLETENESS_TARGET_WORDS["default"])

    length_ratio    = min(1.0, words / max(target_words, 1))
    length_score    = 4.0 * length_ratio

    cues            = _section_cues(text)
    cue_count       = sum(1 for v in cues.values() if v)
    structure_score = min(3.0, (cue_count / 5.0) * 3.0)

    terms           = _extract_topic_terms(chapter_name, chapter_description, syllabus_outcomes)
    coverage        = _term_coverage_ratio(text, terms)
    topic_score     = min(3.0, coverage * 3.0)

    final = length_score + structure_score + topic_score

    return {
        "score":                round(min(10.0, final), 2),
        "word_count":           words,
        "target_word_count":    target_words,
        "length_ratio":         round(length_ratio, 3),
        "section_cues":         cues,
        "section_cue_count":    cue_count,
        "topic_terms":          terms,
        "topic_coverage_ratio": round(coverage, 3),
        "target_level":         target_level,
        "placeholder_hits":     0,
        "ai_disclaimer_hits":   0,
    }


# -----------------------------------------------------------------------
# GEMINI COMPLETENESS ANALYSIS  (context-aware + adaptive insight)
# -----------------------------------------------------------------------
def analyze_completeness_with_gemini_sync(
    content: str,
    rag: dict,
    target_level: str = "undergrad",
    insight: str = "",
) -> dict:
    prompt = f"""
You are evaluating COMPLETENESS of educational content.

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

Definition of completeness:
- Covers the main ideas defined by the SYLLABUS outcomes above.
- Has enough depth for {target_level} level students.
- Has a usable learning flow: introduction → core concepts → examples → summary/practice.
- If content does NOT cover the syllabus outcomes → reduce topic_coverage score.
- Technical terms are ALLOWED.
- Do NOT browse the web.

Return JSON ONLY:
{{
  "completeness":  <1-10>,
  "topic_coverage":<0-5>,
  "depth":         <0-5>,
  "learning_flow": <0-5>
}}

Content:
{content[:4000]}
""".strip()

    response = llm.invoke(prompt)
    raw  = getattr(response, "content", "") or ""
    data = safe_extract_json(raw)

    if not data:
        print("[ERROR] Gemini JSON parsing failed (completeness):", raw[:300])
        return {"completeness": 5, "topic_coverage": 2, "depth": 2, "learning_flow": 2}

    return {
        "completeness":  float(data.get("completeness",  5)),
        "topic_coverage":float(data.get("topic_coverage",2)),
        "depth":         float(data.get("depth",         2)),
        "learning_flow": float(data.get("learning_flow", 2)),
    }


async def analyze_completeness_with_gemini(
    content: str,
    rag: dict,
    target_level: str = "undergrad",
    insight: str = "",
) -> dict:
    return analyze_completeness_with_gemini_sync(content=content, rag=rag, target_level=target_level, insight=insight)


# -----------------------------------------------------------------------
# ADAPTIVE COMBINE
# -----------------------------------------------------------------------
def combine_completeness_adaptive(py: dict, ai: dict, target_level: str = "undergrad") -> float:
    base = _COMPLETENESS_BLEND.get(target_level, _COMPLETENESS_BLEND["default"])
    py_w, ai_w = get_adaptive_blend(parameter=_METRIC, base_py=base["py"], base_ai=base["ai"])

    py_score = float(py.get("score", 5))
    ai_main  = float(ai.get("completeness", 5))
    coverage = float(ai.get("topic_coverage", 2)) * 2
    depth    = float(ai.get("depth",          2)) * 2
    flow     = float(ai.get("learning_flow",  2)) * 2

    ai_internal = (0.40 * ai_main) + (0.20 * coverage) + (0.20 * depth) + (0.20 * flow)
    final       = (py_w * py_score) + (ai_w * ai_internal)

    return round(min(10.0, max(0.0, final)), 2)


# -----------------------------------------------------------------------
# COMPLETENESS AGENT TOOL
# -----------------------------------------------------------------------
@tool
async def evaluate_completeness(state: dict) -> dict:
    """Adaptive Completeness Agent — syllabus-aware, multi-run adaptive, guardrailed."""

    upload_id    = state.get("upload_id")
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
    chapter_name    = (chapter_details.get("chapter_name")        or "").strip()
    chapter_desc    = (chapter_details.get("chapter_description") or "").strip()

    # ── Pillar 1: live RAG context ──
    rag               = await get_rag_context(upload_id)
    syllabus_outcomes = rag.get("outcomes", [])
    print(f"\n{'='*60}")
    print(f"📦 [COMPLETENESS] upload_id={upload_id} level={target_level}")
    print(f"📚 [COMPLETENESS] Domain={rag.get('domain','-')} | Subject={rag.get('subject','-')} | Chapter={rag.get('chapter','-')}")
    print(f"📋 [COMPLETENESS] Syllabus outcomes loaded: {len(syllabus_outcomes)}")

    # ── Pillar 2: adaptive insight ──
    insight = await get_insight_async(_METRIC)
    print(f"🧠 [COMPLETENESS] Adaptive insight: {insight}")

    # ── Pillars 3/4: scoring ──
    py_result = python_completeness_score(
        combined_text, chapter_name=chapter_name, chapter_description=chapter_desc,
        syllabus_outcomes=syllabus_outcomes, target_level=target_level,
    )
    print(f"🐍 [COMPLETENESS] Python score={py_result['score']} | words={py_result['word_count']}/{py_result['target_word_count']} | sections={py_result['section_cue_count']}/5 | coverage={py_result['topic_coverage_ratio']}")

    gem_result = await analyze_completeness_with_gemini(
        combined_text, rag=rag, target_level=target_level, insight=insight,
    )
    print(f"🤖 [COMPLETENESS] Gemini score={gem_result['completeness']} | coverage={gem_result['topic_coverage']} | depth={gem_result['depth']} | flow={gem_result['learning_flow']}")

    raw_score = combine_completeness_adaptive(py_result, gem_result, target_level)
    print(f"🔀 [COMPLETENESS] Combined raw score={raw_score}")

    # ── Pillar 5: guardrail ──
    word_count = py_result.get("word_count", 0)
    final_score, guard_warnings = apply_guardrails(
        raw_score, py_result, gem_result, metric_name=_METRIC,
        min_words_cap=_MIN_WORDS_GUARDRAIL, word_count=word_count,
    )
    if guard_warnings:
        for w in guard_warnings:
            print(w)
    print(f"✅ [COMPLETENESS] FINAL SCORE = {final_score}")

    # ── Update stats for next run ──
    run_confidence = compute_run_confidence(float(py_result["score"]), float(gem_result["completeness"]))
    print(f"📊 [COMPLETENESS] Run confidence={run_confidence}")
    await update_parameter_stats(_METRIC, final_score, run_confidence)

    # ── Save ──
    session = state.get("mcp_session")
    if session:
        save_resp = await session.call_tool(
            "db_save_scores_generic",
            {"upload_id": upload_id, "scores": {"completeness": final_score}},
        )
        try:
            print("💾 [COMPLETENESS] MCP saved:", save_resp.content[0].text)
        except Exception:
            print(f"💾 [COMPLETENESS] MCP saved (score={final_score})")
    else:
        print("⚠️ [COMPLETENESS] mcp_session missing in state")
    print(f"{'='*60}\n")

    return {
        **state,
        "status":             "completeness_evaluated",
        "completeness_score": final_score,
        "python":             py_result,
        "gemini":             gem_result,
        "guardrails":         guard_warnings,
        "run_confidence":     run_confidence,
    }


# -----------------------------------------------------------------------
# MCP TOOL REGISTRATION  (optional)
# -----------------------------------------------------------------------
def mcp_register(mcp) -> None:
    @mcp.tool(
        name="evaluate_completeness",
        description="Adaptive completeness evaluation (0-10) with live syllabus and guardrails.",
    )
    async def evaluate_completeness_tool(
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

        py_result  = python_completeness_score(
            combined_text, chapter_name=chapter_name, chapter_description=chapter_desc,
            syllabus_outcomes=syllabus_outcomes, target_level=target_level,
        )
        gem_result = analyze_completeness_with_gemini_sync(
            combined_text, rag=rag, target_level=target_level, insight=insight,
        )
        raw_score         = combine_completeness_adaptive(py_result, gem_result, target_level)
        final_score, _    = apply_guardrails(
            raw_score, py_result, gem_result, _METRIC,
            min_words_cap=_MIN_WORDS_GUARDRAIL, word_count=py_result.get("word_count", 0),
        )

        run_confidence = compute_run_confidence(float(py_result["score"]), float(gem_result["completeness"]))
        await update_parameter_stats(_METRIC, final_score, run_confidence)

        if save:
            from accounts.models import ContentScore
            obj, _ = ContentScore.objects.get_or_create(upload_id=upload_id)
            obj.completeness = float(final_score)
            obj.save(update_fields=["completeness"])

        return {
            "status": "ok", "upload_id": upload_id, "completeness": final_score,
            "python": py_result, "gemini": gem_result, "saved": bool(save),
        }
