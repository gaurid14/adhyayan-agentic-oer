import json
import re
import textstat
from langchain.tools import tool
from langgraph_agents.services.gemini_service import llm


# ============================================================
# EDUCATION LEVEL SETTINGS (Same philosophy as workflow agent)
# ============================================================

LEVELS = {
    "preschool": {"fre_good": 85, "fre_ok": 70, "sent_good": 8, "sent_ok": 12},
    "primary": {"fre_good": 75, "fre_ok": 60, "sent_good": 10, "sent_ok": 14},
    "middle": {"fre_good": 65, "fre_ok": 50, "sent_good": 12, "sent_ok": 16},
    "secondary": {"fre_good": 55, "fre_ok": 40, "sent_good": 14, "sent_ok": 18},
    "hsc": {"fre_good": 50, "fre_ok": 35, "sent_good": 16, "sent_ok": 20},
    "undergrad": {"fre_good": 45, "fre_ok": 25, "sent_good": 18, "sent_ok": 24},
    "postgrad": {"fre_good": 40, "fre_ok": 20, "sent_good": 20, "sent_ok": 28},
    "phd": {"fre_good": 35, "fre_ok": 15, "sent_good": 22, "sent_ok": 30},
    "default": {"fre_good": 45, "fre_ok": 25, "sent_good": 18, "sent_ok": 24},
}


# ============================================================
# NORMALIZATION FOR FAIR READABILITY (technical safe)
# ============================================================

def normalize_for_readability(text: str) -> str:
    if not text:
        return ""

    t = text

    t = re.sub(r"\b(CNN|ANN|RNN|LSTM|GRU|DNN)\b", "model", t)
    t = re.sub(r"\b(MNIST|CIFAR-?10|ImageNet)\b", "dataset", t, flags=re.I)
    t = re.sub(r"\b(TensorFlow|Keras|PyTorch|NumPy|Pandas)\b", "library", t, flags=re.I)

    t = re.sub(r"\b\d+\s*[x×]\s*\d+\b", "dimension", t)
    t = re.sub(r"\s+", " ", t)

    return t.strip()


# ============================================================
# PYTHON HEURISTIC CLARITY
# ============================================================

def count_passive_voice(text: str) -> int:
    patterns = [
        r"\bwas\b \w+ed",
        r"\bwere\b \w+ed",
        r"\bis\b \w+ed",
        r"\bare\b \w+ed",
    ]
    return sum(len(re.findall(p, text.lower())) for p in patterns)


def avg_sentence_length(text: str) -> float:
    sentences = re.split(r"[.!?]", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return 0
    words = text.split()
    return len(words) / max(len(sentences), 1)


def python_clarity_score(text: str, target_level: str) -> dict:
    cfg = LEVELS.get(target_level, LEVELS["default"])

    normalized = normalize_for_readability(text)

    readability = textstat.flesch_reading_ease(normalized)
    avg_len = avg_sentence_length(normalized)
    passive = count_passive_voice(normalized)

    score = 0

    # Readability scoring
    if readability >= cfg["fre_good"]:
        score += 4
    elif readability >= cfg["fre_ok"]:
        score += 3
    elif readability >= (cfg["fre_ok"] - 15):
        score += 2
    else:
        score += 1

    # Sentence length scoring
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
        "passive_voice": passive,
    }


# ============================================================
# SAFE JSON EXTRACTION
# ============================================================

def safe_extract_json(text: str) -> dict:
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


# ============================================================
# GEMINI CLARITY ANALYSIS
# ============================================================

async def analyze_with_gemini(content: str, target_level: str) -> dict:
    """
    Live Writing Assistant (Editor Mode)

    Generates short, actionable writing improvements
    suitable for real-time contributor editing.
    """

    prompt = f"""
You are reviewing educational learning notes written by a contributor.

Student Level: {target_level}

FIRST decide:
Is the writing already grammatically correct and clear?

IMPORTANT RULES:

✅ If sentences are already correct:
- DO NOT report grammar errors.
- DO NOT rewrite sentences unnecessarily.

✅ Only report issues when:
- grammar is actually incorrect
- meaning is confusing
- sentence is hard to understand

DO NOT suggest stylistic rewrites.

Maximum 5 suggestions.

If content quality is good,
return:

{{
 "suggestions":[]
}}

Otherwise return:

{{
 "suggestions":[
   {{
     "issue":"Grammar error",
     "fix":"Explain improvement briefly",
     "example":"Corrected sentence"
   }}
 ]
}}

Content:
\"\"\"{content}\"\"\"
"""

    try:
        response = llm.invoke(prompt)
        raw = getattr(response, "content", "") or ""

        data = safe_extract_json(raw)

        # fallback safety
        if not data or "suggestions" not in data:
            return {
                "suggestions": [
                    {
                        "issue": "Clarity unclear",
                        "fix": "Rewrite sentence using simpler academic English.",
                        "example": ""
                    }
                ]
            }

        # enforce max 5 suggestions (UI safety)
        cleaned = []

        for s in data.get("suggestions", []):
            issue = (s.get("issue") or "").strip()
            fix = (s.get("fix") or "").strip()
            example = (s.get("example") or "").strip()

            # skip empty AI garbage
            if not issue and not fix:
                continue

            cleaned.append({
                "issue": issue or "Writing improvement",
                "fix": fix or "Rewrite sentence clearly.",
                "example": example
            })

        data["suggestions"] = cleaned[:5]

        return data

    except Exception as e:
        print("Gemini clarity assistant error:", e)

        return {
            "suggestions": [
                {
                    "issue": "AI evaluation failed",
                    "fix": "Try simplifying or rewriting the sentence.",
                    "example": ""
                }
            ]
        }


# ============================================================
# FINAL COMBINATION
# ============================================================

def combine_scores(py: dict, gem: dict) -> float:

    python_score = float(py.get("score", 5))
    gem_clarity = float(gem.get("clarity", 5))

    defq = float(gem.get("definition_quality", 2)) * 2
    instr = float(gem.get("instruction_clarity", 2)) * 2
    termx = float(gem.get("term_explanation", 2)) * 2

    gem_internal = (
            0.4 * gem_clarity +
            0.2 * defq +
            0.2 * instr +
            0.2 * termx
    )

    final = (0.3 * python_score) + (0.7 * gem_internal)

    return round(min(10, final), 2)


# ============================================================
# LIVE EDITOR CLARITY TOOL
# ============================================================

@tool
async def review_clarity(state: dict) -> dict:
    """
    LIVE Clarity Review Agent

    Evaluates educational content clarity while contributor writes.
    Generates mentor-style improvement suggestions without exposing scores.
    """

    notes = state.get("notes", "")
    target_level = state.get("target_level", "undergrad")

    if not notes.strip():
        return {
            **state,
            "clarity_review": {
                "clarity_score": 0,
                "suggestions": ["Content is empty."]
            }
        }

    py_result = python_clarity_score(notes, target_level)
    gem_result = await analyze_with_gemini(notes, target_level)

    final_score = combine_scores(py_result, gem_result)

    raw_suggestions = gem_result.get("suggestions", [])

    normalized = []

    for s in raw_suggestions:

        # if Gemini returned string
        if isinstance(s, str):
            normalized.append({
                "issue": "Writing suggestion",
                "fix": s,
                "example": ""
            })

        # proper object
        elif isinstance(s, dict):
            normalized.append({
                "issue": s.get("issue", "Writing improvement"),
                "fix": s.get("fix", ""),
                "example": s.get("example", "")
            })

    return {
        **state,
        "clarity_review": {
            "clarity_score": final_score,
            "python_details": py_result,
            "gemini_details": gem_result,
            "suggestions": normalized
        }
    }