from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from django.conf import settings
from django.db.models import Prefetch

from accounts.models import Chapter, ChapterPolicy, ContentScore, UploadCheck, ReleasedContent

# Optional: DecisionRun may or may not exist yet in your project.
try:
    from accounts.models import DecisionRun  # type: ignore
except Exception:  # pragma: no cover
    DecisionRun = None  # type: ignore


DEFAULT_PRIORITY: List[str] = ["accuracy", "completeness", "coherence", "clarity", "engagement"]
DEFAULT_PRIMARY_STRATEGY: str = "weighted_average"  # future: "simple_average"
DEFAULT_MISSING_STRATEGY: str = "ignore"  # ignore in average; still used in tie-break
ALGORITHM_VERSION: str = "decision-v1"


def _p(msg: str) -> None:
    # Prints immediately to terminal (useful while running commands/cron/management commands)
    print(f"[DECISION] {msg}", flush=True)


@dataclass(frozen=True)
class RankedCandidate:
    upload_id: int
    chapter_id: int
    contributor_id: int
    timestamp: Any
    composite_score: float
    scores: Dict[str, Optional[float]]  # per-metric raw scores


def _get_config() -> Dict[str, Any]:
    cfg = getattr(settings, "DECISION_MAKER", None)
    if isinstance(cfg, dict):
        return cfg
    return {}


def _float_or_none(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _available_score_fields() -> List[str]:
    fields: List[str] = []
    for f in ContentScore._meta.get_fields():
        if not getattr(f, "concrete", False):
            continue
        if getattr(f, "many_to_one", False) or getattr(f, "one_to_one", False):
            continue
        name = getattr(f, "name", "")
        if name in {"id", "upload", "upload_id", "is_best"}:
            continue
        internal_type = getattr(f, "get_internal_type", lambda: "")()
        if internal_type in {"FloatField", "IntegerField", "DecimalField"}:
            fields.append(name)
    return sorted(set(fields))


def _resolve_priority(available_fields: Sequence[str]) -> List[str]:
    cfg = _get_config()
    priority = cfg.get("priority") or cfg.get("score_priority") or cfg.get("tiebreak_priority")
    if not priority:
        priority = DEFAULT_PRIORITY
    priority_clean = [p for p in priority if p in available_fields]
    tail = [f for f in available_fields if f not in priority_clean]
    return priority_clean + tail


def _resolve_weights(available_fields: Sequence[str]) -> Dict[str, float]:
    cfg = _get_config()
    weights = cfg.get("weights") or {}
    out: Dict[str, float] = {}
    for f in available_fields:
        w = weights.get(f, 1.0)
        try:
            w = float(w)
        except Exception:
            w = 1.0
        out[f] = w
    return out


def _weighted_average(scores: Mapping[str, Optional[float]], weights: Mapping[str, float], missing: str) -> float:
    num = 0.0
    den = 0.0
    for k, w in weights.items():
        if w == 0:
            continue
        v = scores.get(k, None)
        if v is None:
            if missing == "zero":
                den += w
            continue
        num += float(v) * w
        den += w
    if den <= 0:
        return float("-inf")
    return num / den


def _simple_average(scores: Mapping[str, Optional[float]], fields: Sequence[str], missing: str) -> float:
    vals: List[float] = []
    for f in fields:
        v = scores.get(f)
        if v is None:
            if missing == "zero":
                vals.append(0.0)
            continue
        vals.append(float(v))
    if not vals:
        return float("-inf")
    return sum(vals) / len(vals)


class DecisionMakerService:
    """
    Select the "best" upload for a chapter after deadline using a composite score + deterministic tie-break.
    """

    def __init__(self):
        cfg = _get_config()
        self.primary_strategy: str = cfg.get("primary_strategy", DEFAULT_PRIMARY_STRATEGY)
        self.missing_strategy: str = cfg.get("missing_strategy", DEFAULT_MISSING_STRATEGY)  # ignore | zero
        self.algorithm_version: str = cfg.get("algorithm_version", ALGORITHM_VERSION)
        _p(
            f"version={self.algorithm_version}"
        )

    def decide_for_chapter(
        self,
        chapter_id: int,
        *,
        force: bool = False,
        only_evaluated_uploads: bool = True,
        min_contributions_policy: str = "respect",  # respect | ignore
        auto_release: bool = False,
        persist: bool = True,
        top_k_audit: int = 10,
    ):
        
        chapter = Chapter.objects.get(id=chapter_id)
        policy = ChapterPolicy.objects.filter(chapter_id=chapter_id).first()

        _p(f"Loaded | chapter={chapter_id} | policy={'yes' if policy else 'no'}")            

        # deadline gate
        if policy and (not force) and policy.is_open:
            _p("Gate: deadline_not_passed -> returning None (not_due)")
            return self._persist_or_return_none(
                chapter=chapter,
                policy=policy,
                status="not_due",
                reason="deadline_not_passed",
                persist=persist,
            )

        uploads_qs = UploadCheck.objects.filter(chapter_id=chapter_id).order_by("-timestamp")
        if only_evaluated_uploads:
            uploads_qs = uploads_qs.filter(evaluation_status=True)

        uploads_qs = uploads_qs.prefetch_related(
            Prefetch("content_score", queryset=ContentScore.objects.all())
        )
        uploads = list(uploads_qs)

        if policy and min_contributions_policy == "respect":
            need = int(policy.min_contributions or 0)
            _p(f"Min contributions check | have={len(uploads)} need={need}")
            if len(uploads) < need:
                _p("Gate: min_contributions_not_met -> returning None (not_ready)")
                return self._persist_or_return_none(
                    chapter=chapter,
                    policy=policy,
                    status="not_ready",
                    reason=f"min_contributions_not_met: {len(uploads)}/{policy.min_contributions}",
                    persist=persist,
                )

        _p("Ranking uploads...")
        ranked = self.rank_uploads(chapter_id=chapter_id, uploads=uploads)

        _p(f"Ranking done | ranked_count={len(ranked)}")
        if ranked:
            _p(f"Top candidate | upload_id={ranked[0].upload_id} composite={ranked[0].composite_score:.4f}")

        if not ranked:
            _p("No ranked candidates -> returning None (no_candidates)")
            return self._persist_or_return_none(
                chapter=chapter,
                policy=policy,
                status="no_candidates",
                reason="no_scored_uploads",
                persist=persist,
            )

        winner = ranked[0]
        top_k = max(1, int(top_k_audit or 3))
        _p(f"Winner selected | upload_id={winner.upload_id} composite={winner.composite_score:.4f} top_k={top_k}")

        run_obj = None
        _p("Persisting decision run..." if persist else "Persist disabled -> skipping DecisionRun")
        if persist:
            run_obj = self._persist_decision(
                chapter=chapter,
                policy=policy,
                winner=winner,
                ranked=ranked[:top_k],
                top_k=top_k,
            )
            _p(f"Persist complete | run_obj={'yes' if run_obj else 'none'}")

        _p(f"Marking best upload | chapter_id={chapter_id} upload_id={winner.upload_id}")
        self._mark_best_upload(chapter_id=chapter_id, upload_id=winner.upload_id)

        _p("Auto release enabled -> releasing winner" if auto_release else "Auto release disabled")
        if auto_release:
            self._auto_release(chapter_id=chapter_id, winner_upload_id=winner.upload_id)
            _p("Auto release complete")

        _p("Decision complete -> returning result")
        return run_obj or {
            "status": "ok",
            "chapter_id": chapter_id,
            "selected_upload_id": winner.upload_id,
            "composite_score": winner.composite_score,
            "leaderboard": [c.__dict__ for c in ranked[:top_k]],
        }

    def rank_uploads(self, *, chapter_id: int, uploads: Sequence[UploadCheck]) -> List[RankedCandidate]:
        _p(f"rank_uploads start | chapter_id={chapter_id} uploads_in={len(uploads)}")

        available = _available_score_fields()
        _p(f"Available score fields = {available}")
        if not available:
            _p("No numeric score fields found in ContentScore -> returning []")
            return []

        priority = _resolve_priority(available)
        weights = _resolve_weights(available)

        _p(f"Priority order = {priority}")
        _p(f"Weights = {weights}")
        _p(f"Primary strategy = {self.primary_strategy} | missing strategy = {self.missing_strategy}")

        candidates: List[RankedCandidate] = []
        for u in uploads:
            _p(f"Scoring upload_id={u.id} contributor_id={u.contributor_id} ts={u.timestamp}")
            try:
                score_obj = u.content_score
            except Exception:
                _p(f"Skip upload_id={u.id} (no content_score attached)")
                continue

            scores: Dict[str, Optional[float]] = {f: _float_or_none(getattr(score_obj, f, None)) for f in available}
            _p(f"Scores upload_id={u.id} => {scores}")

            if self.primary_strategy == "simple_average":
                composite = _simple_average(scores, available, self.missing_strategy)
                _p(f"Composite strategy=simple_average upload_id={u.id} => {composite}")
            else:
                composite = _weighted_average(scores, weights, self.missing_strategy)
                _p(f"Composite strategy=weighted_average upload_id={u.id} => {composite}")

            if composite == float("-inf"):
                _p(f"Skip upload_id={u.id} (composite=-inf)")
                continue

            candidates.append(
                RankedCandidate(
                    upload_id=u.id,
                    chapter_id=chapter_id,
                    contributor_id=u.contributor_id,
                    timestamp=u.timestamp,
                    composite_score=float(composite),
                    scores=scores,
                )
            )

        if not candidates:
            _p("No candidates after scoring -> returning []")
            return []

        def sort_key(c: RankedCandidate) -> Tuple:
            tiebreak_vals = []
            for p in priority:
                v = c.scores.get(p)
                tiebreak_vals.append(float(v) if v is not None else float("-inf"))
            non_null = sum(1 for v in c.scores.values() if v is not None)
            return (
                c.composite_score,
                *tiebreak_vals,
                non_null,
                c.timestamp,
                c.upload_id,
            )

        _p(f"Sorting candidates | count={len(candidates)}")
        candidates.sort(key=sort_key, reverse=True)

        _p("Sorted. Top 3 candidates:")
        for i, cc in enumerate(candidates[:3], start=1):
            _p(f" #{i} upload_id={cc.upload_id} composite={cc.composite_score:.4f} ts={cc.timestamp}")

        return candidates

    # ---------------------------
    # persistence helpers
    # ---------------------------
    def _persist_or_return_none(
        self,
        *,
        chapter: Chapter,
        policy: Optional[ChapterPolicy],
        status: str,
        reason: str,
        persist: bool
    ):
        _p(f"_persist_or_return_none | status={status} reason={reason} persist={persist} DecisionRun={'yes' if DecisionRun else 'no'}")
        if persist and DecisionRun:
            self._create_decision_run(
                chapter=chapter,
                policy=policy,
                status=status,
                reason=reason,
                winner=None,
                ranked=None,
                top_k=3,
            )
        return None

    def _persist_decision(
        self,
        *,
        chapter: Chapter,
        policy: Optional[ChapterPolicy],
        winner: RankedCandidate,
        ranked: Sequence[RankedCandidate],
        top_k: int = 3,
    ):
        _p(f"_persist_decision | chapter_id={chapter.id} winner_upload_id={winner.upload_id} top_k={top_k}")
        return self._create_decision_run(
            chapter=chapter,
            policy=policy,
            status="ok",
            reason="selected",
            winner=winner,
            ranked=ranked,
            top_k=top_k,
        )

    def _create_decision_run(
        self,
        *,
        chapter: Chapter,
        policy: Optional[ChapterPolicy],
        status: str,
        reason: str,
        winner: Optional[RankedCandidate],
        ranked: Optional[Sequence[RankedCandidate]],
        top_k: int = 3,
    ):
        _p(f"Creating DecisionRun | chapter_id={chapter.id} status={status} reason={reason}")
        if not DecisionRun:
            _p("DecisionRun model not available -> skipping persistence")
            return None

        DecisionRun.objects.filter(chapter=chapter, is_latest=True).update(is_latest=False)
        _p("Unset previous DecisionRun.is_latest (if existed)")

        release_threshold = float(getattr(policy, "release_threshold", 0.0) or 0.0) if policy else 0.0

        leaderboard: List[dict] = []
        if ranked:
            for c in list(ranked)[: max(1, int(top_k))]:
                leaderboard.append(
                    {
                        "upload_id": c.upload_id,
                        "composite_score": c.composite_score,
                        "scores": c.scores,
                    }
                )

        _p(f"Leaderboard built | entries={len(leaderboard)} top_k={top_k}")

        available_scores: List[str] = []
        if ranked:
            seen = set()
            for c in ranked:
                seen.update(c.scores.keys())
            available_scores = sorted(seen)

        weights = _resolve_weights(available_scores)
        thresholds = {"release_threshold": release_threshold}

        _p(f"Saving DecisionRun | selected_upload_id={winner.upload_id if winner else None}")
        obj = DecisionRun.objects.create(
            chapter=chapter,
            selected_upload_id=winner.upload_id if winner else None,
            status=status,
            strategy=self.primary_strategy,
            weights=weights,
            thresholds=thresholds,
            composite_score=winner.composite_score if winner else None,
            ranking=leaderboard,
            explanation=reason,
            is_latest=True,
        )
        _p("DecisionRun saved")
        return obj

    def _mark_best_upload(self, *, chapter_id: int, upload_id: int) -> None:
        _p(f"_mark_best_upload | chapter_id={chapter_id} upload_id={upload_id}")
        field_names = {f.name for f in ContentScore._meta.get_fields() if getattr(f, "concrete", False)}
        if "is_best" not in field_names:
            _p("ContentScore has no is_best field -> skipping")
            return
        ContentScore.objects.filter(upload__chapter_id=chapter_id).update(is_best=False)
        ContentScore.objects.filter(upload_id=upload_id).update(is_best=True)
        _p("ContentScore.is_best updated")

    def _auto_release(self, *, chapter_id: int, winner_upload_id: int) -> None:
        _p(f"_auto_release | chapter_id={chapter_id} winner_upload_id={winner_upload_id}")
        ReleasedContent.objects.filter(upload__chapter_id=chapter_id).update(release_status=False)
        ReleasedContent.objects.update_or_create(
            upload_id=winner_upload_id,
            defaults={"release_status": True},
        )
        _p("ReleasedContent updated (winner True, others False)")