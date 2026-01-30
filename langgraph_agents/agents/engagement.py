import json
import os
import re
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
# SAFE JSON EXTRACTION (Gemini)
# ------------------------------
def safe_extract_json(text: str) -> dict:
    """
    Gemini sometimes returns extra lines.
    Extract first JSON object safely.
    """
    if not text:
        return {}

    # 1) exact json parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2) json inside text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}

    return {}


# ------------------------------
# ENGAGEMENT LEVEL SETTINGS (Scaled)
# ------------------------------
ENGAGEMENT_LEVELS = {
    # small kids need more cues and activities to be engaging
    "preschool": {"case_w": 2.5, "scenario_w": 2.0, "assessment_w": 2.5, "bonus_assessment": 3.0},
    "primary": {"case_w": 2.3, "scenario_w": 2.0, "assessment_w": 2.2, "bonus_assessment": 2.5},
    "middle": {"case_w": 2.0, "scenario_w": 1.8, "assessment_w": 2.0, "bonus_assessment": 2.0},
    "secondary": {"case_w": 1.8, "scenario_w": 1.6, "assessment_w": 1.8, "bonus_assessment": 1.8},
    "hsc": {"case_w": 1.8, "scenario_w": 1.5, "assessment_w": 1.8, "bonus_assessment": 1.6},

    # college students: fewer case studies are okay, engagement can be technical too
    "undergrad": {"case_w": 1.6, "scenario_w": 1.4, "assessment_w": 1.6, "bonus_assessment": 1.5},
    "postgrad": {"case_w": 1.4, "scenario_w": 1.2, "assessment_w": 1.4, "bonus_assessment": 1.3},
    "phd": {"case_w": 1.2, "scenario_w": 1.0, "assessment_w": 1.2, "bonus_assessment": 1.0},

    "default": {"case_w": 1.6, "scenario_w": 1.4, "assessment_w": 1.6, "bonus_assessment": 1.5},
}


# ------------------------------
# GEMINI ENGAGEMENT ANALYSIS
# ------------------------------
async def analyze_engagement_with_gemini(content: str, target_level: str = "undergrad") -> dict:
    """
    Uses Gemini to identify engagement elements.
    """
    prompt = f"""
You are an educational content evaluator for student level: {target_level}.

Count engagement signals inside the content:
- Case studies (explicit OR implied)
- Assessments/exercises/questions/activities
- Scenario cues/examples/real-world what-if situations

IMPORTANT RULES:
- Technical content is allowed.
- Do NOT reduce counts because of technical words.
- Only count meaningful engagement items.

Return JSON ONLY:
{{
  "case_studies": <int>,
  "assessments": <int>,
  "scenario_cues": <int>
}}

Content:
{content}
"""

    response = llm.invoke(prompt)
    raw = getattr(response, "content", "") or ""

    data = safe_extract_json(raw)

    if not data:
        print("[ERROR] Gemini JSON parsing failed:", raw[:300])
        return {"case_studies": 0, "assessments": 0, "scenario_cues": 0}

    return {
        "case_studies": int(data.get("case_studies", 0)),
        "assessments": int(data.get("assessments", 0)),
        "scenario_cues": int(data.get("scenario_cues", 0)),
    }


# ------------------------------
# SCORING FUNCTION (0–10)
# ------------------------------
def compute_engagement_score(
        case_studies: int,
        assessments: int,
        scenario_cues: int,
        has_assessment_upload: bool,
        target_level: str = "undergrad"
) -> float:
    """
    Converts counts → engagement score (0–10)
    Scaled based on education level.
    """
    cfg = ENGAGEMENT_LEVELS.get(target_level, ENGAGEMENT_LEVELS["default"])

    raw_score = (
            (case_studies * cfg["case_w"]) +
            (scenario_cues * cfg["scenario_w"]) +
            (assessments * cfg["assessment_w"]) +
            (cfg["bonus_assessment"] if has_assessment_upload else 0)
    )

    # compress large raw score to max 10 safely
    return round(min(10.0, raw_score), 2)


# ------------------------------
# ENGAGEMENT AGENT TOOL (STATE IN / STATE OUT)
# ------------------------------
@tool
async def evaluate_engagement(state: dict) -> dict:
    """
    Engagement Agent:
    - Reads extracted JSON from: storage/extracted_content/upload_{upload_id}.json
    - Uses Gemini to count case_studies, assessments, scenario_cues
    - Computes engagement score (0–10), scaled by target_level
    - Saves via MCP tool db_save_scores_generic
    """

    upload_id = state.get("upload_id")
    target_level = state.get("target_level", "undergrad")

    if not upload_id:
        return {**state, "status": "engagement_failed", "reason": "upload_id missing"}

    extracted_data = load_extracted_json(upload_id)
    if not extracted_data:
        return {**state, "status": "engagement_failed", "reason": "json missing"}

    combined_text = (extracted_data.get("content", {}).get("combined_text") or "").strip()
    if not combined_text:
        return {**state, "status": "engagement_failed", "reason": "combined_text empty"}

    # find if assessment file uploaded (from extracted json only)
    # NOTE: if your extracted_json has "drive_folders" and you store assessments folder
    # you can later add assessment extraction here, but for now:
    has_assessment_upload = bool(extracted_data.get("drive_folders", {}).get("assessments"))

    # gemini
    gemini_result = await analyze_engagement_with_gemini(combined_text, target_level=target_level)

    case_studies = gemini_result.get("case_studies", 0)
    assessments = gemini_result.get("assessments", 0)
    scenario_cues = gemini_result.get("scenario_cues", 0)

    engagement_score = compute_engagement_score(
        case_studies=case_studies,
        assessments=assessments,
        scenario_cues=scenario_cues,
        has_assessment_upload=has_assessment_upload,
        target_level=target_level,
    )

    # Save using MCP session from graph state
    session = state.get("mcp_session")
    if session:
        save_resp = await session.call_tool(
            "db_save_scores_generic",
            {"upload_id": upload_id, "scores": {"engagement": engagement_score}}
        )
        print("✅ MCP saved engagement:", save_resp.content[0].text)
    else:
        print("⚠️ mcp_session missing in state")

    return {
        **state,
        "status": "engagement_evaluated",
        "engagement_score": engagement_score,
        "details": {
            "case_studies": case_studies,
            "assessments_found": assessments,
            "scenario_cues": scenario_cues,
            "assessment_uploaded": has_assessment_upload,
        },
        "gemini": gemini_result,
    }
