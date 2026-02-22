from __future__ import annotations
"""
Perspective (Google/Jigsaw) moderation utilities for the forum.

Goals:
- Fail-soft: if moderation fails, don't break posting.
- Category-aware thresholds.
- Language hint to avoid unsupported auto detections.
- Cache + throttle to respect ~1 QPS.
"""

from dataclasses import dataclass
import json
import os
import re
import time
from typing import Any, Dict, Tuple, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


@dataclass(frozen=True)
class ModerationDecision:
    action: str  # "allow" | "hide" | "block"
    provider: str
    model: str
    flagged: bool
    max_score: float
    scores: Dict[str, float]
    raw: Dict[str, Any]


def _enabled() -> bool:
    v = getattr(settings, "FORUM_MODERATION_ENABLED", None)
    if v is not None:
        return bool(v)
    ev = os.getenv("FORUM_MODERATION_ENABLED", "")
    return ev.strip().lower() in {"1", "true", "yes", "on"}


def _api_key() -> str:
    return (
        getattr(settings, "PERSPECTIVE_API_KEY", "")
        or os.getenv("PERSPECTIVE_API_KEY", "")
        or ""
    )


def _normalize(text: str) -> str:
    t = (text or "").strip()
    t = t.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")  # zero-width chars
    t = re.sub(r"\s+", " ", t)
    return t


def _cache_key(text: str) -> str:
    return "forum:perspective:" + str(hash(text))


def _throttle_key() -> str:
    return "forum:perspective:last_call_ts"


def _has_devanagari(text: str) -> bool:
    return bool(re.search(r"[\u0900-\u097F]", text or ""))


def _language_hint(text: str) -> list[str]:
    # Devanagari -> hi; else -> en for Hinglish/Latin-typed.
    return ["hi"] if _has_devanagari(text) else ["en"]


def _post_analyze(url: str, payload: dict) -> dict:
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def moderate_text(text: str) -> Tuple[Optional[ModerationDecision], Optional[str]]:
    """
    Returns (decision, error_message).
    If disabled/misconfigured -> (None, None).
    """
    if not _enabled() or not text:
        return None, None

    key = _api_key()
    if not key:
        return None, None

    text_n = _normalize(text)

    cached = cache.get(_cache_key(text_n))
    if cached:
        return cached, None

    # throttle ~1 QPS
    last_ts = cache.get(_throttle_key())
    now = time.time()
    if last_ts and (now - float(last_ts)) < 1.05:
        return None, "moderation_error: perspective_throttled"
    cache.set(_throttle_key(), now, timeout=10)

    # thresholds
    hide_th = float(getattr(settings, "PERSPECTIVE_HIDE_THRESHOLD", 0.60))
    block_th = float(getattr(settings, "PERSPECTIVE_BLOCK_THRESHOLD", 0.75))

    profanity_hide = float(getattr(settings, "PERSPECTIVE_PROFANITY_HIDE_THRESHOLD", 0.50))
    insult_hide = float(getattr(settings, "PERSPECTIVE_INSULT_HIDE_THRESHOLD", 0.30))
    threat_block = float(getattr(settings, "PERSPECTIVE_THREAT_BLOCK_THRESHOLD", 0.65))
    identity_block = float(getattr(settings, "PERSPECTIVE_IDENTITY_BLOCK_THRESHOLD", block_th))

    do_not_store = bool(getattr(settings, "PERSPECTIVE_DO_NOT_STORE", True))

    url = f"https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze?key={key}"

    attrs = ["TOXICITY", "SEVERE_TOXICITY", "INSULT", "PROFANITY", "THREAT", "IDENTITY_ATTACK"]
    requested_attributes = {a: {} for a in attrs}

    lang_hint = _language_hint(text_n)

    payload = {
        "comment": {"text": text_n},
        "requestedAttributes": requested_attributes,
        "doNotStore": do_not_store,
        "languages": lang_hint,
    }

    try:
        data = _post_analyze(url, payload)

    except HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = ""

        # retry once with ["en"] if language hint rejected
        if "INVALID_ARGUMENT" in err_body and "does not support request languages" in err_body:
            try:
                payload["languages"] = ["en"]
                data = _post_analyze(url, payload)
            except Exception:
                return None, f"moderation_error: http_{getattr(e,'code',0)} {err_body[:200]}"
        else:
            return None, f"moderation_error: http_{getattr(e,'code',0)} {err_body[:200]}"

    except (URLError, TimeoutError) as e:
        return None, f"moderation_error: network {type(e).__name__}"

    except Exception as e:
        return None, f"moderation_error: {type(e).__name__}"

    # parse
    scores: Dict[str, float] = {}
    attr_scores = (data or {}).get("attributeScores", {}) or {}
    for a, v in attr_scores.items():
        try:
            scores[a] = float(v["summaryScore"]["value"])
        except Exception:
            continue

    max_score = max(scores.values()) if scores else 0.0

    sev = float(scores.get("SEVERE_TOXICITY", 0.0))
    ins = float(scores.get("INSULT", 0.0))
    prof = float(scores.get("PROFANITY", 0.0))
    thr = float(scores.get("THREAT", 0.0))
    ident = float(scores.get("IDENTITY_ATTACK", 0.0))

    flagged = (
        max_score >= hide_th
        or prof >= profanity_hide
        or ins >= insult_hide
        or thr >= threat_block
        or ident >= identity_block
    )

    if thr >= threat_block or ident >= identity_block or max_score >= block_th or sev >= block_th:
        action = "block"
    elif prof >= profanity_hide or ins >= insult_hide:
        action = "hide"
    elif max_score >= hide_th:
        action = "hide"
    else:
        action = "allow"

    decision = ModerationDecision(
        action=action,
        provider="perspective",
        model="attributeScores",
        flagged=bool(flagged),
        max_score=float(max_score),
        scores={k: float(v) for k, v in scores.items()},
        raw={
            "languages": (data or {}).get("languages"),
            "clientToken": (data or {}).get("clientToken"),
            "used_hint": lang_hint,
        },
    )

    cache.set(_cache_key(text_n), decision, timeout=900)
    return decision, None


def apply_decision_to_instance(instance, decision: Optional[ModerationDecision], *, kind: str) -> None:
    if not decision:
        return

    instance.moderation_model = f"{decision.provider}:{decision.model}"
    instance.moderation_details = {
        "kind": kind,
        "flagged": decision.flagged,
        "max_score": decision.max_score,
        "scores": decision.scores,
        "raw": decision.raw,
    }
    instance.moderated_at = timezone.now()

    if decision.action == "hide":
        instance.is_hidden = True
        instance.moderation_status = "pending_review"
    elif decision.action == "block":
        instance.is_hidden = True
        instance.moderation_status = "rejected"
    else:
        instance.is_hidden = False
        instance.moderation_status = "approved"
