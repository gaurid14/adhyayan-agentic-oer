import json
import re

from asgiref.sync import sync_to_async
from langchain.tools import tool
from langsmith import traceable

from langgraph_agents.services.gemini_service import llm


# =====================================================
# SAFE JSON EXTRACTION
# =====================================================

def safe_extract_json(text: str) -> dict:
    if not text:
        return {}

    try:
        return json.loads(text)
    except:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except:
                return {}

    return {}


# =====================================================
# ENGAGEMENT LEVEL SETTINGS (same logic as production)
# =====================================================

ENGAGEMENT_LEVELS = {
    "preschool": {"case_w": 2.5, "scenario_w": 2.0},
    "primary": {"case_w": 2.3, "scenario_w": 2.0},
    "middle": {"case_w": 2.0, "scenario_w": 1.8},
    "secondary": {"case_w": 1.8, "scenario_w": 1.6},
    "hsc": {"case_w": 1.8, "scenario_w": 1.5},
    "undergrad": {"case_w": 1.6, "scenario_w": 1.4},
    "postgrad": {"case_w": 1.4, "scenario_w": 1.2},
    "phd": {"case_w": 1.2, "scenario_w": 1.0},
    "default": {"case_w": 1.6, "scenario_w": 1.4},
}

@sync_to_async
def get_rag_context_from_selection(course_id: int, chapter_id: int):
    try:
        from accounts.models import Course, Chapter, OutcomeChapterMapping

        course = Course.objects.select_related("department__program").get(id=course_id)
        chapter = Chapter.objects.get(id=chapter_id)

        domain = course.department.program.program_name
        subject = course.course_name

        mappings = OutcomeChapterMapping.objects.filter(chapter=chapter)
        outcomes = [m.outcome.description for m in mappings]

        syllabus = "\n".join(outcomes[:2])

        return {
            "domain": domain,
            "subject": subject,
            "chapter": chapter.chapter_name,
            "syllabus": syllabus,
        }

    except Exception:
        return {}


# =====================================================
# GEMINI ENGAGEMENT ANALYSIS
# =====================================================

async def analyze_engagement(content: str, rag: dict, target_level: str):
    """
    Live Engagement Assistant

    Gives quick engagement improvements
    suitable for real-time editor feedback.
    """

    prompt = f"""
You are helping improve engagement of educational content.

Student Level: {target_level}

-------------------------
Subject: {rag.get("subject", "")}
Chapter: {rag.get("chapter", "")}
-------------------------

IMPORTANT RULES:

- Count examples ONLY if relevant to topic
- Ignore irrelevant examples
- Suggest improvements based on subject

Max 5 short suggestions (<15 words)

Return JSON:
{{
 "suggestions":[...]
}}

Content:
\"\"\"{content}\"\"\"
"""

    try:
        response = llm.invoke(prompt)
        raw = getattr(response, "content", "") or ""

        data = safe_extract_json(raw)

        if not data or "suggestions" not in data:
            return {
                "suggestions": [
                    {
                        "issue": "Low engagement",
                        "fix": "Add practical example.",
                        "example": ""
                    }
                ]
            }

        data["suggestions"] = data["suggestions"][:5]

        return data

    except Exception as e:
        print("Gemini engagement error:", e)

        return {
            "suggestions": [
                {
                    "issue": "AI evaluation failed",
                    "fix": "Add example or real-world explanation.",
                    "example": ""
                }
            ]
        }


# =====================================================
# SCORE COMPUTATION (LIVE VERSION)
# =====================================================

def compute_engagement_score(
        case_studies,
        examples,
        scenario_cues,
        target_level
):

    cfg = ENGAGEMENT_LEVELS.get(
        target_level,
        ENGAGEMENT_LEVELS["default"]
    )

    raw_score = (
            case_studies * cfg["case_w"] +
            examples * cfg["case_w"] +
            scenario_cues * cfg["scenario_w"]
    )

    return round(min(10.0, raw_score), 2)


# =====================================================
# ENGAGEMENT REVIEW AGENT (EDITOR MODE)
# =====================================================
@tool
@traceable(name="Engagement Review Agent")
async def review_engagement(state: dict) -> dict:
    """
    LIVE Engagement Review Agent

    Evaluates engagement while contributor writes.
    """

    notes = state.get("notes", "")
    target_level = state.get("target_level", "undergrad")

    if not notes.strip():
        return {
            **state,
            "engagement_review": {
                "engagement_score": 0,
                "suggestions": ["Content is empty."]
            }
        }

    course_id = state.get("course_id")
    chapter_id = state.get("chapter_id")

    rag = {}

    if course_id and chapter_id:
        rag = await get_rag_context_from_selection(course_id, chapter_id)

    gemini_result = await analyze_engagement(
        notes,
        rag,
        target_level
    )

    case_studies = gemini_result.get("case_studies", 0)
    examples = gemini_result.get("examples", 0)
    scenarios = gemini_result.get("scenario_cues", 0)

    score = compute_engagement_score(
        case_studies,
        examples,
        scenarios,
        target_level
    )

    # ===== Smart Suggestions Layer =====

    suggestions = gemini_result.get("suggestions", [])

    if case_studies == 0:
        suggestions.append(
            "Add a case study demonstrating practical application."
        )

    if examples < 2:
        suggestions.append(
            "Include more worked examples to improve understanding."
        )

    if scenarios == 0:
        suggestions.append(
            "Add real-world scenarios or 'what-if' explanations."
        )

    return {
        **state,
        "engagement_review": {
            "engagement_score": score,
            "details": {
                "case_studies": case_studies,
                "examples": examples,
                "scenario_cues": scenarios,
            },
            "suggestions": suggestions
        }
    }