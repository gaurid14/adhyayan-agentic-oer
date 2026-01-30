import json
import re
import os
from typing import List, Dict

from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from google.auth.exceptions import RefreshError

from langgraph_agents.services.drive_service import GoogleDriveAuthService, GoogleDriveFolderService
from ...models import Course, Chapter, UploadCheck
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
# 🔒 Authorization Guard
# ==============================
class ContributorAccessGuard:
    @staticmethod
    def ensure_contributor(user) -> bool:
        return user.role == "CONTRIBUTOR"


# ==============================
# 📚 Course Recommendation Service
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
    def get_chapters_by_course(courses) -> Dict[int, List[dict]]:
        return {
            course.id: list(
                course.chapters.values(
                    "id", "chapter_number", "chapter_name"
                )
            )
            for course in courses
        }


# ==============================
# 📦 Submission Query Service
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
# ☁️ Contributor Drive Facade
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
# 🧠 Topic Extraction Utility
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


# ==============================
# 🌐 Views (Thin Controllers)
# ==============================
@login_required
def contributor_dashboard_view(request):
    user = request.user

    # ✅ contributor approval flag for Submit button
    is_approved_contributor = (
        user.role == User.Role.CONTRIBUTOR
        and user.contributor_approval_status == User.ContributorApprovalStatus.APPROVED
        and user.is_active
    )

    # ✅ recommended courses based on expertise → course mapping
    # (Expertise has M2M to Course; User has M2M to Expertise)
    recommended_courses_qs = (
        Course.objects
        .filter(expertises__experts=user)
        .select_related("department", "scheme")
        .prefetch_related(
            Prefetch(
                "chapters",
                queryset=Chapter.objects.only("id", "course_id", "chapter_number", "chapter_name").order_by("chapter_number")
            )
        )
        .distinct()
        .order_by("department__dept_name", "semester", "course_name")
    )
    recommended_courses = list(recommended_courses_qs)

    # ✅ LEFT PANEL tree: dept → sem → courses
    tree_map = defaultdict(lambda: defaultdict(list))
    for c in recommended_courses:
        tree_map[c.department][c.semester].append(c)

    course_tree = []
    for dept, sem_map in tree_map.items():
        sems = []
        for sem, courses in sorted(sem_map.items(), key=lambda x: (x[0] or 0)):
            sems.append({"semester": sem, "courses": courses})
        course_tree.append({"dept": dept, "sems": sems})

    # ✅ selected course
    selected_course = None
    course_id = request.GET.get("course_id")
    if course_id:
        selected_course = next((c for c in recommended_courses if str(c.id) == str(course_id)), None)

    # ✅ Right side chapters table (IMPORTANT FIX HERE)
    chapters = []
    if selected_course:
        chapters_qs = (
            Chapter.objects
            .filter(course=selected_course)
            .select_related("policy")  # ✅ avoids N+1
            .annotate(
                contributions_total=Count("uploads", distinct=True),
                contributions_evaluated=Count(
                    "uploads",
                    filter=Q(uploads__evaluation_status=True),
                    distinct=True,
                ),
                user_submitted=Exists(
                    UploadCheck.objects.filter(
                        chapter=OuterRef("pk"),
                        contributor=user,
                    )
                )
            )
            .order_by("chapter_number")
        )

        for ch in chapters_qs:
            policy = getattr(ch, "policy", None)

            deadline_dt = None
            extensions_used = 0
            max_extensions = 0
            min_contributions = None
            is_open = True

            if policy:
                deadline_dt = getattr(policy, "current_deadline", None) or getattr(policy, "deadline", None)
                extensions_used = getattr(policy, "extensions_used", 0) or 0
                max_extensions = getattr(policy, "max_extensions", 0) or 0
                min_contributions = getattr(policy, "min_contributions", None)

                # support both property or method
                is_open_attr = getattr(policy, "is_open", True)
                is_open = is_open_attr() if callable(is_open_attr) else bool(is_open_attr)

            # ---- HARD SAFETY: stop "{{ ... }}" from ever showing in UI ----
            if isinstance(deadline_dt, str):
                parsed = parse_datetime(deadline_dt)
                deadline_dt = parsed  # becomes datetime or None

            try:
                if isinstance(min_contributions, str):
                    min_contributions = int(min_contributions)
            except (TypeError, ValueError):
                min_contributions = None

            can_submit = is_approved_contributor and is_open and (not ch.user_submitted)

            chapters.append({
                "id": ch.id,
                "chapter_number": ch.chapter_number,
                "chapter_name": ch.chapter_name,

                "deadline": deadline_dt,
                "is_open": is_open,

                # keep these for your teammates (even if you hide columns in HTML)
                "extensions_used": extensions_used,
                "max_extensions": max_extensions,
                "min_contributions": min_contributions,

                "contributions_total": ch.contributions_total,
                "contributions_evaluated": ch.contributions_evaluated,
                "user_submitted": ch.user_submitted,
                "can_submit": can_submit,
            })



    # ✅ chapters_json for your drawer ("Browse Chapters") — no extra DB hits because we prefetched
    chapters_json_dict = {}
    for c in recommended_courses:
        chapters_json_dict[str(c.id)] = [
            {"id": ch.id, "chapter_number": ch.chapter_number, "chapter_name": ch.chapter_name}
            for ch in getattr(c, "chapters").all()
        ]

    chapters_json = json.dumps(chapters_json_dict)

    # ---- keep your existing context items too if you already have them ----
    context = {
        "recommended_courses": recommended_courses,
        "course_tree": course_tree,
        "selected_course": selected_course,
        "chapters": chapters,
        "chapters_json": chapters_json,
        "is_approved_contributor": is_approved_contributor,

        # if you already use these in template, keep them:
        "uploads": getattr(user, "uploads", []).all() if hasattr(user, "uploads") else [],
        "uploads_count": UploadCheck.objects.filter(contributor=user).count(),

        # notifications placeholders (keep your real logic if you have it):
        "notifications": [],
        "unread_count": 0,
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

    return render(
        request,
        "contributor/profile.html",
        {"contributor": user}
    )

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

            # 🔥 STEP 1: Fetch topic folders
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

                # 🔥 STEP 2: Fetch actual files inside topic folder
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
                        "topic": topic_name,   # 🔥 useful in UI
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