"""
Minimal, safe Admin Agent service.

Placement: accounts/services/admin_agent.py

Behavior:
- Computes which chapters/uploads should be released for courses that have recent DecisionRun activity.
- Enforces two rules:
  1) 80% threshold (uses course.release_policy.threshold_percentage if present, else defaults to 80).
     Uses floor(percent * total_chapters / 100) and ensures at least 1 required chapter when course has >=1 chapter
     (matches example 6 -> 4 at 80%).
  2) Sequential completion: only continuous chapters from chapter_number=1 until first missing.
- Updates ReleasedContent.release_status accordingly (True for allowed continuous prefix, False for others).
- Stores Google Drive folder IDs in ReleasedContent.drive_folder_id as a JSON string:
    {"pdf": "<folder_id>", "videos": "<folder_id>"}
  pulled from storage/extracted_content/upload_<upload_id>.json if available.
- Lightweight by default: auto_release_recent(window_seconds=3600) processes only courses with DecisionRun in the last window.
- Safe: avoids deleting/renaming models and uses transactions for per-course updates.

Notes:
- A chapter is "complete" only if there exists an UploadCheck for that chapter whose related ContentScore.is_best == True.
- This module is designed to be triggered from request time (middleware) with throttling.

"""

from __future__ import annotations

from datetime import timedelta
import json
import logging
import math
import os
from typing import Dict, Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

from accounts.models import Course, Chapter, UploadCheck, ReleasedContent, DecisionRun


class AdminAgentService:
    DEFAULT_THRESHOLD_PERCENT = 80

    def _required_chapters(self, total_chapters: int, threshold_percentage: int) -> int:
        """Use floor(percent*total/100) to match the user's example (6 -> 4 at 80%)."""
        if total_chapters <= 0:
            return 0
        if threshold_percentage <= 0:
            return 0
        required = math.floor((threshold_percentage * total_chapters) / 100)
        return max(1, required)

    def _chapter_is_complete(self, chapter: Chapter) -> bool:
        """Complete means at least one upload for the chapter has content_score.is_best=True."""
        return UploadCheck.objects.filter(chapter=chapter, content_score__is_best=True).exists()

    def _best_upload_for_chapter(self, chapter: Chapter) -> Optional[UploadCheck]:
        """Return the latest upload with content_score.is_best=True."""
        return (
            UploadCheck.objects.filter(chapter=chapter, content_score__is_best=True)
            .order_by("-timestamp")
            .first()
        )

    # -----------------------------
    # Drive folder id helpers
    # -----------------------------
    def _extracted_json_path(self, upload_id: int) -> str:
        # project_root/storage/extracted_content/upload_<id>.json
        return os.path.join(settings.BASE_DIR, "storage", "extracted_content", f"upload_{upload_id}.json")

    def _drive_folders_for_upload(self, upload_id: int) -> Optional[Dict[str, str]]:
        """Try to read drive_folders from extracted json; return None if missing."""
        try:
            path = self._extracted_json_path(upload_id)
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            folders = data.get("drive_folders") or {}
            pdf = folders.get("pdf")
            vids = folders.get("videos") or folders.get("video")
            if pdf or vids:
                out: Dict[str, str] = {}
                if pdf:
                    out["pdf"] = str(pdf)
                if vids:
                    out["videos"] = str(vids)
                return out or None
        except Exception:
            logger.exception("AdminAgent: failed to read extracted json for upload_id=%s", upload_id)
        return None

    def _encode_drive_folder_id(self, upload_id: int, existing_value: Optional[str] = None) -> Optional[str]:
        """Return JSON string for drive folders, preferring extracted json, else keep existing."""
        folders = self._drive_folders_for_upload(upload_id)
        if folders:
            return json.dumps(folders, ensure_ascii=False)
        return existing_value

    # -----------------------------
    # Main logic
    # -----------------------------
    def process_course(self, course: Course) -> Dict:
        """Compute release statuses for a course and update ReleasedContent."""
        chapters = list(Chapter.objects.filter(course=course).order_by("chapter_number"))
        total = len(chapters)
        if total == 0:
            return {"status": "no_chapters"}

        # threshold from ReleasePolicy if exists, else default 80
        threshold = self.DEFAULT_THRESHOLD_PERCENT
        try:
            policy = getattr(course, "release_policy", None)
            if policy and policy.auto_release_enabled is False:
                return {"status": "disabled"}
            if policy and policy.threshold_percentage is not None:
                threshold = int(policy.threshold_percentage)
        except Exception:
            threshold = self.DEFAULT_THRESHOLD_PERCENT

        required = self._required_chapters(total, threshold)

        complete_flags = [self._chapter_is_complete(ch) for ch in chapters]
        completed_count = sum(1 for v in complete_flags if v)

        # If not meeting threshold, unrelease everything for this course
        if completed_count < required:
            with transaction.atomic():
                uploads = UploadCheck.objects.filter(chapter__course=course)
                ReleasedContent.objects.filter(upload__in=uploads).update(release_status=False)
            return {
                "status": "skipped_threshold",
                "completed": completed_count,
                "required": required,
                "threshold": threshold,
            }

        # Sequential rule: release only continuous prefix from chapter 1 until first missing
        prefix_len = 0
        for flag in complete_flags:
            if flag:
                prefix_len += 1
            else:
                break

        with transaction.atomic():
            for idx, ch in enumerate(chapters):
                allowed = idx < prefix_len

                # Always set all existing releases for this chapter to False first (prevents stale True on old best uploads)
                chapter_uploads = UploadCheck.objects.filter(chapter=ch)
                ReleasedContent.objects.filter(upload__in=chapter_uploads).update(release_status=False)

                best_upload = self._best_upload_for_chapter(ch)
                if not best_upload:
                    continue

                rc, _ = ReleasedContent.objects.get_or_create(upload=best_upload)
                rc.release_status = allowed
                rc.drive_folder_id = self._encode_drive_folder_id(best_upload.id, rc.drive_folder_id)
                rc.save(update_fields=["release_status", "drive_folder_id"])

        return {
            "status": "processed",
            "prefix_len": prefix_len,
            "completed": completed_count,
            "required": required,
            "threshold": threshold,
        }
    def run_for_course(self, course_or_id):
        """Compatibility helper: accept either Course instance or course id."""
        if isinstance(course_or_id, Course):
            course = course_or_id
        else:
            course = Course.objects.get(pk=int(course_or_id))
        return self.process_course(course)



    def auto_release_recent(self, window_seconds: int = 3600) -> Dict[int, Dict]:
        """Process courses that had DecisionRun activity in the last window_seconds."""
        cutoff = timezone.now() - timedelta(seconds=window_seconds)
        recent_courses = Course.objects.filter(chapters__decision_runs__created_at__gte=cutoff).distinct()

        results: Dict[int, Dict] = {}
        for course in recent_courses:
            try:
                results[course.id] = self.process_course(course)
            except Exception as e:
                logger.exception("AdminAgent failed for course_id=%s", course.id)
                results[course.id] = {"status": "error", "error": str(e)}
        return results
