# accounts/services/admin_agent.py

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Dict, Optional, Tuple

from django.db import transaction

from accounts.models import (
    Chapter,
    ContentScore,
    DecisionRun,
    ReleasedContent,
    ReleasePolicy,
    UploadCheck,
)


def _required_chapters(total: int, threshold_percentage: int) -> int:
    """
    User requirement:
    - 6 chapters @ 80% => 4 (floor)
    - but never require less than 1 if there are chapters (unless threshold <= 0)
    """
    if total <= 0:
        return 0
    if threshold_percentage <= 0:
        return 0

    required = floor((threshold_percentage / 100.0) * total)
    return max(1, required)


def _best_upload_by_decisionrun(course) -> Dict[int, int]:
    """
    Returns {chapter_id: upload_id} using DecisionRun as primary source.
    """
    best: Dict[int, int] = {}
    qs = (
        DecisionRun.objects.filter(
            chapter__course=course,
            is_latest=True,
            status="ok",
            selected_upload__isnull=False,
        )
        .select_related("chapter")
        .order_by("-created_at")
    )
    for dr in qs:
        # Keep first occurrence per chapter (latest due to ordering)
        if dr.chapter_id not in best and dr.selected_upload_id:
            best[dr.chapter_id] = dr.selected_upload_id
    return best


def _best_upload_by_is_best(course) -> Dict[int, int]:
    """
    Fallback: returns {chapter_id: upload_id} based on ContentScore.is_best.
    If somehow multiple are_best exist, picks the most recent upload.
    """
    best: Dict[int, int] = {}
    qs = (
        ContentScore.objects.filter(
            upload__chapter__course=course,
            is_best=True,
        )
        .select_related("upload", "upload__chapter")
        .order_by("-upload__timestamp")
    )
    for cs in qs:
        ch_id = cs.upload.chapter_id
        if ch_id not in best:
            best[ch_id] = cs.upload_id
    return best


def run_admin_release_for_course(course):
    """
    Backward-compatible function wrapper (your shell already uses this).
    Returns a string message.
    """
    result = AdminAgentService().run_for_course(course)
    if result.get("status") == "released":
        return f"Released ({result['released_now']} new), eligible={result['total_eligible']}, required={result['required']}"
    if result.get("status") == "pending":
        return f"Threshold not met ({result['completed']}/{result['required']})"
    return result.get("message", "Unknown result")


class AdminAgentService:
    """
    Releases course content when enough chapters have a 'best' selected upload.

    Selection priority:
      1) DecisionRun (latest ok) -> selected_upload
      2) ContentScore.is_best == True -> upload
    """

    def run_for_course(self, course) -> Dict:
        chapters_qs = Chapter.objects.filter(course=course)
        total_chapters = chapters_qs.count()

        if total_chapters == 0:
            return {"status": "error", "message": "No chapters found for this course."}

        policy, _ = ReleasePolicy.objects.get_or_create(course=course)

        if not policy.auto_release_enabled:
            return {"status": "skipped", "message": "Auto release disabled."}

        required = _required_chapters(total_chapters, int(policy.threshold_percentage or 0))

        # Build mapping chapter -> best upload
        best_map = _best_upload_by_decisionrun(course)
        if len(best_map) < total_chapters:
            fallback = _best_upload_by_is_best(course)
            # only fill missing chapters from fallback
            for ch_id, up_id in fallback.items():
                best_map.setdefault(ch_id, up_id)

        completed = len(best_map)

        if completed < required:
            return {
                "status": "pending",
                "completed": completed,
                "required": required,
                "total_chapters": total_chapters,
                "message": "Threshold not met.",
            }

        released_now = 0

        with transaction.atomic():
            for chapter_id, upload_id in best_map.items():
                # 1) ensure only THIS upload is released for that chapter
                ReleasedContent.objects.filter(
                    upload__chapter_id=chapter_id
                ).exclude(upload_id=upload_id).update(release_status=False)

                # 2) upsert ReleasedContent for the best upload
                prev = ReleasedContent.objects.filter(upload_id=upload_id).first()
                prev_status = prev.release_status if prev else None

                obj, _created = ReleasedContent.objects.update_or_create(
                    upload_id=upload_id,
                    defaults={"release_status": True},
                )

                # count newly released (created OR flipped False->True)
                if prev_status is not True:
                    released_now += 1

        return {
            "status": "released",
            "released_now": released_now,
            "total_eligible": completed,
            "required": required,
            "total_chapters": total_chapters,
        }
