import json
import re
import os
from typing import List, Dict

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from google.auth.exceptions import RefreshError

from langgraph_agents.services.drive_service import GoogleDriveAuthService, GoogleDriveFolderService
from .expertise_service import save_user_expertise
from ...models import Course, Chapter, UploadCheck, ChapterContributionProgress
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone

from ...models import Course, Chapter, UploadCheck


from collections import defaultdict
import json

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render, get_object_or_404
from django.utils import timezone

from accounts.models import Course, Chapter, ChapterPolicy, UploadCheck, User
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

# make sure ChapterPolicy is importable in this file (if you have it)
from accounts.models import Course, Chapter, ChapterPolicy, User

from django.db.models import Prefetch, Exists, OuterRef
from django.utils.dateparse import parse_datetime

# ==============================
# Authorization Guard
# ==============================
class ContributorAccessGuard:
    @staticmethod
    def ensure_contributor(user) -> bool:
        return user.role == "CONTRIBUTOR"


# ==============================
# Course Recommendation Service
# ==============================
class ContributorCourseService:
    @staticmethod
    def get_recommended_courses(user):
        expertises = user.domain_of_expertise.all()
        return (
            Course.objects
            .filter(expertises__in=expertises)
            .distinct()
        )

    @staticmethod
    def get_chapters_by_course(courses, user):
        chapters_map = {}

        # progress map (chapter_id -> total_uploads)
        progress_qs = ChapterContributionProgress.objects.filter(contributor=user)
        progress_map = {p.chapter_id: p.total_uploads for p in progress_qs}

        # submitted map (chapter_id -> upload_id)  (exists = submitted)
        submitted_qs = UploadCheck.objects.filter(contributor=user).order_by("-timestamp")
        submitted_map = {}
        for u in submitted_qs:
            # keep latest upload per chapter
            if u.chapter_id not in submitted_map:
                submitted_map[u.chapter_id] = u.id

        for course in courses:
            chapters = Chapter.objects.filter(course=course).select_related("course")
            chapters = chapters.select_related("course__scheme", "course__department", "course__department__program")

            chapter_list = []
            for ch in chapters:
                policy = getattr(ch, "policy", None)

                deadline = None
                is_open = True

                if policy:
                    deadline_dt = policy.current_deadline or policy.deadline
                    if deadline_dt:
                        deadline = deadline_dt.isoformat()
                        is_open = timezone.now() <= deadline_dt

                total_uploads = progress_map.get(ch.id, 0)
                upload_id = submitted_map.get(ch.id)  # if exists => submitted

                chapter_list.append({
                    "id": ch.id,
                    "chapter_number": ch.chapter_number,
                    "chapter_name": ch.chapter_name,
                    "deadline": deadline,
                    "is_open": is_open,

                    # contributor status fields
                    "total_uploads": total_uploads,
                    "submitted": bool(upload_id),
                    "upload_id": upload_id,  # optional (use later if needed)
                })

            chapters_map[str(course.id)] = chapter_list

        return chapters_map


# ==============================
# Submission Query Service
# ==============================
class ContributorSubmissionService:
    @staticmethod
    def get_user_submissions(user):
        return (
            UploadCheck.objects
            .select_related(
                "chapter",
                "chapter__course",
                "chapter__course__scheme",
                "chapter__course__department",
                "chapter__course__department__program",
            )
            .filter(contributor=user)
            .order_by("-timestamp")
        )

    @staticmethod
    def has_existing_submission(contributor_id, chapter_id) -> bool:
        return UploadCheck.objects.filter(
            contributor_id=contributor_id,
            chapter_id=chapter_id
        ).exists()


# ==============================
# Contributor Drive Facade
# ==============================
class ContributorDriveFacade:
    """
    High-level facade used ONLY by views.
    Internally uses OOP Drive services.
    """

    def __init__(self):
        self.service = GoogleDriveAuthService.get_service()
        self.folder_service = GoogleDriveFolderService(self.service)
        self.oer_root_id = self.folder_service.get_or_create_folder("oer_content")

    def get_all_files_for_chapter(
            self,
            contributor_id: int,
            course_id: int,
            chapter_number: int
    ) -> List[dict]:

        files: List[dict] = []

        for folder_type in ["drafts", "pdf", "videos", "assessments"]:
            files.extend(
                self._get_files_by_type(
                    contributor_id,
                    course_id,
                    chapter_number,
                    folder_type
                )
            )

        return files

    def _get_files_by_type(
            self,
            contributor_id: int,
            course_id: int,
            chapter_number: int,
            folder_type: str
    ) -> List[dict]:

        contributor_folder = f"{contributor_id}_{course_id}_{chapter_number}"

        category_root_id = self.folder_service.get_or_create_folder(
            settings.GOOGLE_DRIVE_FOLDERS[folder_type],
            self.oer_root_id
        )

        query = (
            "mimeType='application/vnd.google-apps.folder' "
            f"and name='{contributor_folder}' "
            f"and '{category_root_id}' in parents "
            "and trashed=false"
        )

        result = (
            self.service.files()
            .list(q=query, fields="files(id, name)")
            .execute()
        )

        folders = result.get("files", [])
        if not folders:
            return []

        folder_id = folders[0]["id"]

        result = (
            self.service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="files(id, name, mimeType)"
            )
            .execute()
        )

        return [
            {
                "id": f["id"],
                "name": f["name"],
                "mimeType": f["mimeType"],
                "type": folder_type,
            }
            for f in result.get("files", [])
        ]


# ==============================
# Topic Extraction Utility
# ==============================
class TopicExtractor:
    @staticmethod
    def extract(description: str) -> List[str]:
        if not description:
            return []
        return [
            t.strip()
            for t in re.split(r"[;,.]", description)
            if t.strip()
        ]


from datetime import timedelta
from django.utils import timezone
from accounts.models import ChapterContributionProgress, UploadCheck, ChapterPolicy
from itertools import chain
from accounts.models import ContributorNote

@login_required
def contributor_dashboard_view(request):
    user = request.user

    if not ContributorAccessGuard.ensure_contributor(user):
        return render(request, "403.html", status=403)

    courses = ContributorCourseService.get_recommended_courses(user).select_related(
        "scheme", "department", "department__program"
    )

    chapters_map = ContributorCourseService.get_chapters_by_course(courses, user)

    # Build tasks
    now = timezone.now()
    soon_deadline = now + timedelta(days=2)

    progress_qs = ChapterContributionProgress.objects.filter(contributor=user).select_related("chapter", "chapter__course")
    submitted_chapter_ids = set(
        UploadCheck.objects.filter(contributor=user).values_list("chapter_id", flat=True)
    )

    tasks = []

    # Task 1: Resume started chapters
    for p in progress_qs:
        if p.total_uploads > 0 and p.chapter_id not in submitted_chapter_ids:
            tasks.append({
                "type": "resume",
                "title": f"Resume Chapter {p.chapter.chapter_number}: {p.chapter.chapter_name}",
                "subtitle": (
                    f"{p.total_uploads} file(s) uploaded • "
                    f"PDF: {p.pdf_count} • Video: {p.video_count} • Not submitted yet"
                ),
                "chapter_id": p.chapter_id,
                "course_id": p.chapter.course_id,
                "priority": 2,
            })

    # Task 2: Deadline in 2 days
    policies = ChapterPolicy.objects.filter(
        chapter__course__in=courses,
        current_deadline__isnull=False
    ).select_related("chapter", "chapter__course")

    for policy in policies:
        deadline = policy.current_deadline or policy.deadline
        if not deadline:
            continue

        if now <= deadline:
            hours_left = (deadline - now).total_seconds() / 3600

            # only show deadlines within 2 days in tasks
            if hours_left <= 48:
                if policy.chapter_id in submitted_chapter_ids:
                    continue

                # urgency label
                if hours_left <= 24:
                    urgency = "red"      # 🟥
                    urgency_text = "URGENT • < 24h"
                elif hours_left <= 48:
                    urgency = "orange"   # 🟧
                    urgency_text = "Soon • < 2 days"
                else:
                    urgency = "green"    # 🟩
                    urgency_text = "On track"

                tasks.append({
                    "type": "deadline",
                    "title": f"Deadline soon: Chapter {policy.chapter.chapter_number}: {policy.chapter.chapter_name}",
                    "subtitle": f"Due on {deadline.strftime('%d %b %Y, %I:%M %p')}",
                    "chapter_id": policy.chapter_id,
                    "course_id": policy.chapter.course_id,
                    "priority": 1,


                    "urgency": urgency,
                    "urgency_text": urgency_text,
                })

    # sort tasks by priority (deadline first)
    tasks = sorted(tasks, key=lambda x: x["priority"])[:6]  # show max 6 tasks

    # ----------------------------
    # Recent Activity (last 3)
    # ----------------------------

    recent_activity = []

    progress_latest = (
        ChapterContributionProgress.objects
        .filter(contributor=user, has_any_upload=True)
        .select_related("chapter", "chapter__course")
        .order_by("-last_upload_at")[:3]
    )

    for p in progress_latest:
        parts = []
        if p.pdf_count > 0:
            parts.append(f"📄 {p.pdf_count} PDF(s)")
        if p.video_count > 0:
            parts.append(f"🎥 {p.video_count} Video(s)")
        if p.draft_count > 0:
            parts.append(f"📝 {p.draft_count} Draft(s)")

        recent_activity.append({
            "icon": "⚡",
            "text": f"Updated Chapter {p.chapter.chapter_number}: {p.chapter.chapter_name} • " + " • ".join(parts),
            "time": p.last_upload_at,
        })

    recent_activity = sorted(recent_activity, key=lambda x: x["time"], reverse=True)[:3]

    notes = ContributorNote.objects.filter(contributor=user)[:5]

    context = {
        "recommended_courses": courses,
        "chapters_json": json.dumps(chapters_map),
        "tasks": tasks,  # send tasks to template
        "recent_activity": recent_activity,
        "notes": notes,
    }

    return render(request, "contributor/contributor_dashboard.html", context)


@login_required
def contributor_submissions(request):
    user = request.user

    if not ContributorAccessGuard.ensure_contributor(user):
        return render(request, "403.html", status=403)

    uploads = ContributorSubmissionService.get_user_submissions(user)

    context = {
        "uploads": uploads,
        "total_uploads": uploads.count(),
        "pending_count": uploads.filter(evaluation_status=False).count(),
        "evaluated_count": uploads.filter(evaluation_status=True).count(),
    }

    return render(
        request,
        "contributor/contributor_submissions.html",
        context
    )


@login_required
def contributor_profile(request):
    user = request.user

    if not ContributorAccessGuard.ensure_contributor(user):
        return render(request, "403.html", status=403)

    if request.method == "POST":
        user.designation = request.POST.get("designation", "").strip()
        user.current_institution = request.POST.get("current_institution", "").strip()
        user.phone_number = request.POST.get("phone_number", "").strip()
        user.bio = request.POST.get("bio", "").strip()

        years = request.POST.get("years_of_experience", "").strip()
        user.years_of_experience = int(years) if years.isdigit() else None

        hq = request.POST.get("highest_qualification", "").strip()
        if hq in dict(user.HIGHEST_QUALIFICATION_CHOICES):
            user.highest_qualification = hq

        user.save()

        raw_expertise = request.POST.get("expertise", "")
        save_user_expertise(user, raw_expertise)

        messages.success(request, "Profile updated successfully!")
        return redirect("contributor_profile")

    return render(request, "contributor/profile.html", {"contributor": user})


@login_required
def contributor_submit_content_view(request, course_id=None, chapter_id=None):
    # ✅ Accept IDs from URL kwargs OR query params OR POST (safe + backward compatible)
    course_id = course_id or request.GET.get("course_id") or request.POST.get("course_id")
    chapter_id = chapter_id or request.GET.get("chapter_id") or request.POST.get("chapter_id")

    print("Course:", course_id)
    print("Chapter:", chapter_id)

    if not course_id or not chapter_id:
        messages.error(request, "Invalid access. Please select a course & chapter.")
        return redirect("contributor_dashboard")

    # ✅ Load course + chapter correctly (chapter must belong to this course)
    course = get_object_or_404(Course, id=course_id)
    chapter = get_object_or_404(Chapter, id=chapter_id, course=course)

    contributor_id = request.user.id

    # ✅ Contributor approval check (required feature)
    # If your project allows students too, keep this check strictly for contributors
    if getattr(request.user, "role", None) == User.Role.CONTRIBUTOR:
        if request.user.contributor_approval_status != User.ContributorApprovalStatus.APPROVED:
            messages.warning(request, "Your contributor account is not approved yet.")
            return redirect("contributor_dashboard")

    # ✅ Deadline check (required feature)
    policy = getattr(chapter, "policy", None)
    deadline = None
    if policy:
        deadline = policy.current_deadline or policy.deadline

    if deadline and timezone.now() > deadline:
        messages.warning(request, "Deadline has passed for this chapter.")
        return redirect("contributor_dashboard")

    # ---- Prevent duplicate submission ----
    if ContributorSubmissionService.has_existing_submission(contributor_id, chapter_id):
        return render(
            request,
            "contributor/final_submission.html",
            {
                "chapter_name": chapter.chapter_name,
                "contributor_id": contributor_id,
                "message": "You have already submitted this chapter’s content."
            }
        )

    # ---- Store context in session ----
    request.session.update({
        "contributor_id": contributor_id,
        "course_id": course.id,
        "course_name": course.course_name,
        "chapter_id": chapter.id,
        "chapter_name": chapter.chapter_name,
        "chapter_number": chapter.chapter_number,
        "description": chapter.description,
    })

    files = []

    try:
        # 🔹 Initialize Drive service (NEW AUTH CLASS)
        service = GoogleDriveAuthService.get_service()
        folder_service = GoogleDriveFolderService(service)

        oer_root_id = folder_service.get_or_create_folder("oer_content")

        # --- SAME LOGIC AS YOUR WORKING VERSION ---
        def get_files_from_folder(folder_type):
            folder_name = f"{contributor_id}_{course.id}_{chapter.chapter_number}"

            root_folder_id = folder_service.get_or_create_folder(
                settings.GOOGLE_DRIVE_FOLDERS[folder_type],
                oer_root_id
            )

            # Contributor chapter folder
            query = (
                "mimeType='application/vnd.google-apps.folder' "
                f"and name='{folder_name}' "
                f"and '{root_folder_id}' in parents "
                "and trashed=false"
            )

            folders = (
                service.files()
                .list(q=query, fields="files(id, name)")
                .execute()
                .get("files", [])
            )

            collected_files = []

            if not folders:
                return collected_files

            chapter_folder_id = folders[0]["id"]

            # STEP 1: Fetch topic folders
            topic_folders = (
                service.files()
                .list(
                    q=(
                        "mimeType='application/vnd.google-apps.folder' "
                        f"and '{chapter_folder_id}' in parents "
                        "and trashed=false"
                    ),
                    fields="files(id, name)"
                )
                .execute()
                .get("files", [])
            )

            for topic in topic_folders:
                topic_id = topic["id"]
                topic_name = topic["name"]

                print(f"📂 TOPIC FOLDER: {topic_name}")

                # STEP 2: Fetch actual files inside topic folder
                files_result = (
                    service.files()
                    .list(
                        q=f"'{topic_id}' in parents and trashed=false",
                        fields="files(id, name, mimeType)"
                    )
                    .execute()
                )

                for f in files_result.get("files", []):
                    print(f"📄 FILE FOUND: {f['name']} (inside {topic_name})")

                    collected_files.append({
                        "id": f["id"],
                        "name": f["name"],
                        "mimeType": f["mimeType"],
                        "type": folder_type,
                        "topic": topic_name,
                    })

            return collected_files


        # Aggregate files
        for folder_type in ["drafts", "pdf", "videos", "assessments"]:
            files.extend(get_files_from_folder(folder_type))

    except RefreshError as e:
        print("[ERROR] Google token invalid:", e)

        token_path = settings.GOOGLE_TOKEN_FILE
        if os.path.exists(token_path):
            os.remove(token_path)

        messages.error(
            request,
            "⚠️ Your Google Drive session has expired. Please reconnect."
        )
        return redirect("contributor_dashboard")

    except Exception as e:
        print("[ERROR] Unexpected Drive issue:", e)
        messages.error(request, f"An unexpected error occurred: {e}")
        files = []

    # ---- Topic extraction ----
    raw_desc = chapter.description or ""
    topics = [t.strip() for t in re.split(r"[;,.]", raw_desc) if t.strip()]

    context = {
        "course": course,
        "chapter": chapter,
        "files": files,
        "topics": topics,
        "deadline": deadline,  # ✅ optional: use in template if you want
    }

    return render(request, "contributor/submit_content.html", context)


from django.views.decorators.csrf import csrf_exempt
from accounts.models import ContributorNote

@csrf_exempt
@login_required
def add_note(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    title = request.POST.get("title", "").strip()
    content = request.POST.get("content", "").strip()

    if not content:
        return JsonResponse({"error": "Content required"}, status=400)

    note = ContributorNote.objects.create(
        contributor=request.user,
        title=title,
        content=content
    )
    return JsonResponse({"success": True, "id": note.id})


@csrf_exempt
@login_required
def edit_note(request, note_id):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    note = get_object_or_404(ContributorNote, id=note_id, contributor=request.user)

    note.title = request.POST.get("title", "").strip()
    note.content = request.POST.get("content", "").strip()
    note.save()

    return JsonResponse({"success": True})


@csrf_exempt
@login_required
def delete_note(request, note_id):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    note = get_object_or_404(ContributorNote, id=note_id, contributor=request.user)
    note.delete()

    return JsonResponse({"success": True})
