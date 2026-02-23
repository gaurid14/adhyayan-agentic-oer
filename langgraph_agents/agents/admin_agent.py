# # accounts/services/admin_agent.py

# from __future__ import annotations

# from dataclasses import dataclass
# from math import floor
# from typing import Dict, Optional, Tuple

# from django.db import transaction

# from accounts.models import (
#     Chapter,
#     ContentScore,
#     DecisionRun,
#     ReleasedContent,
#     ReleasePolicy,
#     UploadCheck,
# )


# def _required_chapters(total: int, threshold_percentage: int) -> int:
#     """
#     User requirement:
#     - 6 chapters @ 80% => 4 (floor)
#     - but never require less than 1 if there are chapters (unless threshold <= 0)
#     """
#     if total <= 0:
#         return 0
#     if threshold_percentage <= 0:
#         return 0

#     required = floor((threshold_percentage / 100.0) * total)
#     return max(1, required)


# def _best_upload_by_decisionrun(course) -> Dict[int, int]:
#     """
#     Returns {chapter_id: upload_id} using DecisionRun as primary source.
#     """
#     best: Dict[int, int] = {}
#     qs = (
#         DecisionRun.objects.filter(
#             chapter__course=course,
#             is_latest=True,
#             status="ok",
#             selected_upload__isnull=False,
#         )
#         .select_related("chapter")
#         .order_by("-created_at")
#     )
#     for dr in qs:
#         # Keep first occurrence per chapter (latest due to ordering)
#         if dr.chapter_id not in best and dr.selected_upload_id:
#             best[dr.chapter_id] = dr.selected_upload_id
#     return best


# def _best_upload_by_is_best(course) -> Dict[int, int]:
#     """
#     Fallback: returns {chapter_id: upload_id} based on ContentScore.is_best.
#     If somehow multiple are_best exist, picks the most recent upload.
#     """
#     best: Dict[int, int] = {}
#     qs = (
#         ContentScore.objects.filter(
#             upload__chapter__course=course,
#             is_best=True,
#         )
#         .select_related("upload", "upload__chapter")
#         .order_by("-upload__timestamp")
#     )
#     for cs in qs:
#         ch_id = cs.upload.chapter_id
#         if ch_id not in best:
#             best[ch_id] = cs.upload_id
#     return best


# def run_admin_release_for_course(course):
#     """
#     Backward-compatible function wrapper (your shell already uses this).
#     Returns a string message.
#     """
#     result = AdminAgentService().run_for_course(course)
#     if result.get("status") == "released":
#         return f"Released ({result['released_now']} new), eligible={result['total_eligible']}, required={result['required']}"
#     if result.get("status") == "pending":
#         return f"Threshold not met ({result['completed']}/{result['required']})"
#     return result.get("message", "Unknown result")


# class AdminAgentService:
#     """
#     Releases course content when enough chapters have a 'best' selected upload.

#     Selection priority:
#       1) DecisionRun (latest ok) -> selected_upload
#       2) ContentScore.is_best == True -> upload
#     """

#     def run_for_course(self, course) -> Dict:
#         chapters_qs = Chapter.objects.filter(course=course)
#         total_chapters = chapters_qs.count()

#         if total_chapters == 0:
#             return {"status": "error", "message": "No chapters found for this course."}

#         policy, _ = ReleasePolicy.objects.get_or_create(course=course)

#         if not policy.auto_release_enabled:
#             return {"status": "skipped", "message": "Auto release disabled."}

#         required = _required_chapters(total_chapters, int(policy.threshold_percentage or 0))

#         # Build mapping chapter -> best upload
#         best_map = _best_upload_by_decisionrun(course)
#         if len(best_map) < total_chapters:
#             fallback = _best_upload_by_is_best(course)
#             # only fill missing chapters from fallback
#             for ch_id, up_id in fallback.items():
#                 best_map.setdefault(ch_id, up_id)

#         completed = len(best_map)

#         if completed < required:
#             return {
#                 "status": "pending",
#                 "completed": completed,
#                 "required": required,
#                 "total_chapters": total_chapters,
#                 "message": "Threshold not met.",
#             }

#         released_now = 0

#         with transaction.atomic():
#             for chapter_id, upload_id in best_map.items():
#                 # 1) ensure only THIS upload is released for that chapter
#                 ReleasedContent.objects.filter(
#                     upload__chapter_id=chapter_id
#                 ).exclude(upload_id=upload_id).update(release_status=False)

#                 # 2) upsert ReleasedContent for the best upload
#                 prev = ReleasedContent.objects.filter(upload_id=upload_id).first()
#                 prev_status = prev.release_status if prev else None

#                 obj, _created = ReleasedContent.objects.update_or_create(
#                     upload_id=upload_id,
#                     defaults={"release_status": True},
#                 )

#                 # count newly released (created OR flipped False->True)
#                 if prev_status is not True:
#                     released_now += 1

#         return {
#             "status": "released",
#             "released_now": released_now,
#             "total_eligible": completed,
#             "required": required,
#             "total_chapters": total_chapters,
#         }

# from langgraph_agents.services.drive_service import GoogleDriveAuthService, GoogleDriveFolderService
# from accounts.models import ReleasedContent

# class AdminAgentService:
#     # ... existing run_for_course logic ...

#     def pickup_released_files(self, course):
#         """
#         Uses MCP-aligned services to move released files
#         into a 'Final OER' folder structure on Drive.
#         """
#         service = GoogleDriveAuthService.get_service()
#         folder_service = GoogleDriveFolderService(service)

#         # 1. Ensure Top-Level 'Final OER' Folder exists
#         root_folder_id = folder_service.get_or_create_folder("Final OER")

#         # 2. Ensure Course-specific folder exists
#         course_folder_id = folder_service.get_or_create_folder(course.course_name, root_folder_id)

#         # 3. Get all released content for this course
#         released_items = ReleasedContent.objects.filter(
#             upload__chapter__course=course,
#             release_status=True
#         )

#         transfer_results = []
#         for item in released_items:
#             # Note: We use the 'upload' field which maps to 'UploadCheck' in your MCP server
#             file_id = item.upload.id  # In a real scenario, this would be the actual Drive file_id
#             chapter_name = item.upload.chapter.chapter_name

#             # 4. Create Chapter Subfolder
#             chapter_folder_id = folder_service.get_or_create_folder(chapter_name, course_folder_id)

#             # Logic to move/copy file via Drive API goes here
#             transfer_results.append(f"Organized {chapter_name} into Drive folder: {chapter_folder_id}")

#         return transfer_results

from __future__ import annotations
from math import floor
from typing import Dict, List
from django.db import transaction

from accounts.models import (
    Chapter,
    ContentScore,
    DecisionRun,
    ReleasedContent,
    ReleasePolicy,
    UploadCheck,
)
from langgraph_agents.services.drive_service import GoogleDriveAuthService, GoogleDriveFolderService

def _required_chapters(total: int, threshold_percentage: int) -> int:
    if total <= 0 or threshold_percentage <= 0: return 0
    return max(1, floor((threshold_percentage / 100.0) * total))

def _get_best_map(course) -> Dict[int, int]:
    """Helper to merge DecisionRun and ContentScore sources."""
    best: Dict[int, int] = {}
    # 1. DecisionRun (Primary)
    dr_qs = DecisionRun.objects.filter(
        chapter__course=course, is_latest=True, status="ok", selected_upload__isnull=False
    ).order_by("-created_at")
    for dr in dr_qs:
        if dr.chapter_id not in best:
            best[dr.chapter_id] = dr.selected_upload_id

    # 2. ContentScore (Fallback)
    cs_qs = ContentScore.objects.filter(
        upload__chapter__course=course, is_best=True
    ).order_by("-upload__timestamp")
    for cs in cs_qs:
        if cs.upload.chapter_id not in best:
            best[cs.upload.chapter_id] = cs.upload_id
    return best

class AdminAgentService:
    def run_for_course(self, course) -> Dict:
        chapters_qs = Chapter.objects.filter(course=course).order_by('chapter_number')
        total_chapters = chapters_qs.count()

        if total_chapters == 0:
            return {"status": "error", "message": "No chapters found."}

        policy, _ = ReleasePolicy.objects.get_or_create(course=course)
        if not policy.auto_release_enabled:
            return {"status": "skipped", "message": "Auto release disabled."}

        # 1. Get all ready chapters
        all_ready_map = _get_best_map(course)

        # 2. Filter for unbroken sequence starting from Chapter 1
        sequential_ready_map = {}
        for ch in chapters_qs:
            if ch.id in all_ready_map:
                sequential_ready_map[ch.id] = all_ready_map[ch.id]
            else:
                break # Sequence broken!

        sequential_count = len(sequential_ready_map)
        required = _required_chapters(total_chapters, int(policy.threshold_percentage or 0))

        # 3. Final Validation
        if sequential_count < required:
            return {
                "status": "pending",
                "completed": sequential_count,
                "required": required,
                "total_chapters": total_chapters,
                "message": f"Sequence broken at Chapter {sequential_count + 1}."
            }

        # 4. Release ONLY the sequential chapters
        released_now = 0
        with transaction.atomic():
            for chapter_id, upload_id in sequential_ready_map.items():
                ReleasedContent.objects.filter(upload__chapter_id=chapter_id).exclude(upload_id=upload_id).update(release_status=False)
                obj, created = ReleasedContent.objects.update_or_create(
                    upload_id=upload_id, defaults={"release_status": True}
                )
                if created: released_now += 1

        return {
            "status": "released",
            "released_now": released_now,
            "completed": sequential_count,
            "required": required,
            "total_chapters": total_chapters,
            "message": "Sequence maintained and threshold met."
        }

    def pickup_released_files(self, course) -> List[str]:
        """Locates the existing contributor folders in Drive and saves their IDs."""
        service = GoogleDriveAuthService.get_service()

        released_items = ReleasedContent.objects.filter(
            upload__chapter__course=course,
            release_status=True
        ).select_related('upload__chapter', 'upload__contributor')

        results = []
        for item in released_items:
            # Matches your format: contributorid_subjectid_chapterno
            # e.g., 1884_ITD05015_1
            folder_name = f"{item.upload.contributor.id}_{course.course_code}_{item.upload.chapter.chapter_number}"

            # Search for the folder by name
            query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            drive_result = service.files().list(q=query, fields="files(id, name)").execute()
            folders = drive_result.get('files', [])

            if folders:
                folder_id = folders[0]['id']
                # Save the found ID to the database
                item.drive_folder_id = folder_id
                item.save()
                results.append(f"✅ Linked Chapter {item.upload.chapter.chapter_number} to existing folder: {folder_id}")
            else:
                results.append(f"⚠️ Folder {folder_name} not found on Drive.")

        return results