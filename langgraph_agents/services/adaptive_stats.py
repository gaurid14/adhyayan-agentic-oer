"""
langgraph_agents/services/adaptive_stats.py

Shared adaptive evaluation utilities used by accuracy and completeness agents
(and available to clarity/coherence/engagement for future use).

Responsibilities:
  - update_parameter_stats     : rolling EMA update of ParameterStats after each run
  - get_adaptive_blend         : shift py/ai weights based on historical confidence + variance
  - get_insight_sync           : build Gemini prompt-injection string from ParameterStats
  - apply_guardrails           : post-combine guardrail: clamp, disagreement flag, artefact cap
  - should_include_metric      : per-upload metric inclusion/exclusion for Decision Agent
"""

from __future__ import annotations

import logging
from typing import Optional

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# DEFAULT THRESHOLDS (used when ParameterConfig row is missing)
# -----------------------------------------------------------------------
_DEFAULT_LOW_CONF  = 0.5
_DEFAULT_HIGH_VAR  = 1.0
_EMA_ALPHA         = 0.20   # EMA smoothing: recent runs carry 20% weight


# -----------------------------------------------------------------------
# INTERNAL: load ParameterStats + ParameterConfig (synchronous, DB-safe)
# -----------------------------------------------------------------------
def _load_stats(parameter: str):
    """Returns (ParameterStats | None, ParameterConfig | None). Never raises."""
    try:
        from accounts.models import ParameterStats, ParameterConfig
        try:
            stats = ParameterStats.objects.get(parameter=parameter)
        except Exception:
            stats = None
        try:
            cfg = ParameterConfig.objects.get(parameter=parameter)
        except Exception:
            cfg = None
        return stats, cfg
    except Exception:
        return None, None


# -----------------------------------------------------------------------
# 1. UPDATE PARAMETER STATS  (called once per graph run per agent)
# -----------------------------------------------------------------------
def update_parameter_stats_sync(
    parameter: str,
    score: float,
    run_confidence: float,
) -> None:
    """
    Rolling EMA update of ParameterStats for *parameter*.

    Called at the end of each agent run so Run N+1 can learn from Run N.
    Uses Exponential Moving Average to weight recent runs higher.

    run_confidence : proxy confidence for this single run
                     e.g.  1 - |py_score - gem_score| / 10
                     range  0.0 → 1.0
    """
    try:
        from accounts.models import ParameterStats

        stats, _ = ParameterStats.objects.get_or_create(parameter=parameter)

        if stats.usage_count == 0:
            # First run ever: initialise directly
            stats.avg_confidence = run_confidence
            stats.avg_variance   = 0.0
        else:
            # Exponential Moving Average
            prev_conf = float(stats.avg_confidence or 0.0)
            prev_var  = float(stats.avg_variance  or 0.0)

            new_conf = _EMA_ALPHA * run_confidence + (1 - _EMA_ALPHA) * prev_conf
            # Variance proxy: how much does conf deviate from the rolling mean?
            deviation = abs(run_confidence - prev_conf)
            new_var   = _EMA_ALPHA * deviation + (1 - _EMA_ALPHA) * prev_var

            stats.avg_confidence = round(new_conf, 6)
            stats.avg_variance   = round(new_var,  6)

        stats.usage_count += 1
        stats.save(update_fields=["avg_confidence", "avg_variance", "usage_count", "updated_at"])
        print(f"📊 [ADAPTIVE] {parameter} stats updated → conf={stats.avg_confidence:.3f} var={stats.avg_variance:.3f} runs={stats.usage_count}")

    except Exception as exc:
        print(f"⚠️ [ADAPTIVE] update_parameter_stats failed for '{parameter}': {exc}")


# Async wrapper for use inside async agent functions
update_parameter_stats = sync_to_async(update_parameter_stats_sync)


# -----------------------------------------------------------------------
# 2. COMPUTE WITHIN-RUN CONFIDENCE PROXY
# -----------------------------------------------------------------------
def compute_run_confidence(py_score: float, gem_score: float) -> float:
    """
    Within-run confidence proxy: agreement between Python heuristic and Gemini.

    Perfect agreement (py == gem)  → confidence = 1.0
    Max disagreement (10 pts apart) → confidence = 0.0

    This is used to feed update_parameter_stats after each run.
    """
    agreement = 1.0 - abs(py_score - gem_score) / 10.0
    return round(max(0.0, min(1.0, agreement)), 4)


# -----------------------------------------------------------------------
# 3. ADAPTIVE BLEND WEIGHTS
# -----------------------------------------------------------------------
def get_adaptive_blend(
    parameter: str,
    base_py: float,
    base_ai: float,
) -> tuple[float, float]:
    """
    Dynamically adjust Python/Gemini blend weights based on ParameterStats.

    Logic
    -----
    - Low avg_confidence  → AI has been unreliable → shift weight toward Python
    - High avg_variance   → Further instability    → shift weight further
    - Stable              → Use base weights unchanged

    Returns (py_weight, ai_weight) that sum to 1.0.
    Never lets py_weight exceed 0.60 (Gemini always carries at least 40%).
    """
    stats, cfg = _load_stats(parameter)

    if stats is None or stats.usage_count == 0:
        return round(base_py, 4), round(base_ai, 4)

    low_conf = float(cfg.low_conf_threshold if cfg else _DEFAULT_LOW_CONF)
    high_var  = float(cfg.high_var_threshold  if cfg else _DEFAULT_HIGH_VAR)

    avg_conf = float(stats.avg_confidence or 0.0)
    avg_var  = float(stats.avg_variance   or 0.0)

    adjustment = 0.0
    if avg_conf < low_conf:
        adjustment += 0.10
        print(f"🔄 [ADAPTIVE] {parameter}: low confidence ({avg_conf:.3f} < {low_conf}) → shifting +10% to Python")
    if avg_var > high_var:
        adjustment += 0.07
        print(f"🔄 [ADAPTIVE] {parameter}: high variance ({avg_var:.3f} > {high_var}) → shifting +7% to Python")

    final_py = min(0.60, base_py + adjustment)
    final_ai = round(1.0 - final_py, 4)
    final_py  = round(final_py, 4)

    if adjustment > 0:
        print(f"⚖️  [ADAPTIVE] {parameter} blend SHIFTED → py={final_py:.0%} ai={final_ai:.0%} (was py={base_py:.0%} ai={base_ai:.0%})")
    else:
        print(f"⚖️  [ADAPTIVE] {parameter} blend STABLE → py={final_py:.0%} ai={final_ai:.0%}")

    return final_py, final_ai


# -----------------------------------------------------------------------
# 4. INSIGHT MESSAGE (injected into Gemini prompt)
# -----------------------------------------------------------------------
def get_insight_sync(parameter: str) -> str:
    """
    Build an adaptive directive string to inject into Gemini's prompt.
    Based on ParameterStats history — same pattern as clarity.py.
    """
    stats, cfg = _load_stats(parameter)

    if stats is None or stats.usage_count == 0:
        return f"Evaluate {parameter} normally."

    low_conf = float(cfg.low_conf_threshold if cfg else _DEFAULT_LOW_CONF)
    high_var  = float(cfg.high_var_threshold  if cfg else _DEFAULT_HIGH_VAR)

    avg_conf = float(stats.avg_confidence or 0.0)
    avg_var  = float(stats.avg_variance   or 0.0)

    insights: list[str] = []

    if avg_conf < low_conf:
        insights.append(
            f"{parameter.title()} scores have been inconsistent across past runs. "
            "Apply stricter, more conservative criteria."
        )
    if avg_var > high_var:
        insights.append(
            f"High variance detected in past {parameter} evaluations. "
            "Penalise vague, off-topic, or poorly structured content more heavily."
        )

    if not insights:
        return f"{parameter.title()} is generally stable. Maintain balanced evaluation."

    return "\n".join(insights)


# Async wrapper
get_insight_async = sync_to_async(get_insight_sync)


# -----------------------------------------------------------------------
# 5. GUARDRAIL LAYER
# -----------------------------------------------------------------------
def apply_guardrails(
    score: float,
    py_result: dict,
    gem_result: dict,
    metric_name: str,
    *,
    min_words_cap: Optional[int] = None,   # if set: word_count < this → cap at 4.0
    word_count: Optional[int] = None,
) -> tuple[float, list[str]]:
    """
    Post-combine guardrail applied BEFORE saving to DB.

    Rules (in order):
      G1  |py - gem_main| > 3.5  → conservative midpoint blend
      G2  placeholder_hits > 2 OR ai_disclaimer_hits > 0  → cap at 5.0
      G3  word_count < min_words_cap  → cap at 4.0          (completeness only)
      G4  Hard clamp [1.0, 10.0]

    Returns
    -------
    (guardrailed_score : float, warnings : list[str])
    """
    warnings: list[str] = []

    py_score  = float(py_result.get("score", 5))
    gem_score = float(gem_result.get(metric_name, 5))

    # G1 — large py↔gemini disagreement
    disagreement = abs(py_score - gem_score)
    if disagreement > 3.5:
        midpoint = (py_score + gem_score) / 2.0
        cap = round(midpoint + 0.5, 2)
        if score > cap:
            msg = f"🛡️ [GUARDRAIL-G1] {metric_name}: py={py_score:.1f} gem={gem_score:.1f} gap={disagreement:.1f} → capped to {cap}"
            print(msg)
            warnings.append(msg)
            score = cap

    # G2 — artefact signals (placeholder text, AI refusals)
    placeholders = int(py_result.get("placeholder_hits", 0))
    ai_hits      = int(py_result.get("ai_disclaimer_hits", 0))
    if placeholders > 2 or ai_hits > 0:
        artefact_cap = 5.0
        if score > artefact_cap:
            msg = f"🛡️ [GUARDRAIL-G2] {metric_name}: artefact detected (placeholders={placeholders}, ai_hits={ai_hits}) → capped to {artefact_cap}"
            print(msg)
            warnings.append(msg)
            score = artefact_cap

    # G3 — minimum content length (completeness-specific)
    if min_words_cap is not None and word_count is not None:
        if word_count < min_words_cap:
            min_content_cap = 4.0
            if score > min_content_cap:
                msg = f"🛡️ [GUARDRAIL-G3] {metric_name}: word_count={word_count} < {min_words_cap} → capped to {min_content_cap}"
                print(msg)
                warnings.append(msg)
                score = min_content_cap

    # G4 — hard clamp
    score = max(1.0, min(10.0, score))

    return round(score, 2), warnings


# -----------------------------------------------------------------------
# 6. PER-UPLOAD METRIC INCLUSION SIGNAL  (used by Decision Agent)
# -----------------------------------------------------------------------
def should_include_metric(
    metric: str,
    content_score,              # ContentScore ORM instance
) -> tuple[bool, str]:
    """
    Determines whether a metric should be included in composite scoring
    for a specific upload, based on that upload's own confidence + variance.

    A metric is excluded when:
      - Its per-upload confidence is below the configured threshold, AND
      - Its per-upload variance is above the configured threshold.
    Both conditions must hold (AND logic) to exclude — conservative approach.

    Returns
    -------
    (should_include : bool, reason : str)
    """
    conf_field = f"{metric}_confidence"
    var_field  = f"{metric}_variance"

    conf = getattr(content_score, conf_field, None)
    var  = getattr(content_score, var_field,  None)

    # No multi-run data available → include unconditionally
    if conf is None and var is None:
        return True, "no_multirun_data"

    stats, cfg = _load_stats(metric)
    low_conf = float(cfg.low_conf_threshold if cfg else _DEFAULT_LOW_CONF)
    high_var  = float(cfg.high_var_threshold  if cfg else _DEFAULT_HIGH_VAR)

    conf_too_low = (conf is not None) and (float(conf) < low_conf)
    var_too_high = (var  is not None) and (float(var)  > high_var)

    if conf_too_low and var_too_high:
        reason = (
            f"excluded: conf={conf:.3f} < {low_conf} "
            f"AND var={var:.3f} > {high_var}"
        )
        return False, reason

    return True, "reliable"
