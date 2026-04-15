from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse

from accounts.models import (
    Course, Chapter, ReleasedContent, EnrolledCourse,
    Assessment, AssessmentAttempt, UploadCheck,
    StudentChapterProgress,
)
from langgraph_agents.services.drive_service import GoogleDriveAuthService

import json
import ast
import logging

logger = logging.getLogger(__name__)


def _parse_drive_ids(raw: str):
    """
    ReleasedContent.drive_folder_id may be stored as:
      - plain id: "1AbC..."
      - JSON string: {"pdf":"...","videos":"..."}
      - Python dict string: {'pdf': '...', 'videos': '...'}
    Return list of ids.
    """
    if not raw:
        return []
    raw = raw.strip()

    # plain id
    if raw and not (raw.startswith("{") and raw.endswith("}")):
        return [raw]

    # try JSON
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return [v for v in obj.values() if isinstance(v, str) and v.strip()]
        if isinstance(obj, list):
            return [v for v in obj if isinstance(v, str) and v.strip()]
    except Exception:
        pass

    # try Python literal dict
    try:
        obj = ast.literal_eval(raw)
        if isinstance(obj, dict):
            return [v for v in obj.values() if isinstance(v, str) and v.strip()]
        if isinstance(obj, list):
            return [v for v in obj if isinstance(v, str) and v.strip()]
    except Exception:
        pass

    return []


def _is_folder(mime_type: str) -> bool:
    return (mime_type or "") == "application/vnd.google-apps.folder"



@login_required
def chapter_topics(request, course_id):
    course = get_object_or_404(Course, id=course_id)

    # Ensure student is enrolled
    if not EnrolledCourse.objects.filter(student=request.user, course=course).exists():
        return render(request, "student/locked_error.html", {
            "error_message": "Enrollment required."
        })

    chapters = Chapter.objects.filter(course=course).order_by("chapter_number")

    selected_chapter_id = request.GET.get("chapter_id")
    if selected_chapter_id:
        current_chapter = get_object_or_404(
            Chapter,
            id=selected_chapter_id,
            course=course
        )
    else:
        current_chapter = chapters.first()

    # Topics from chapter description
    topics = []
    if current_chapter and current_chapter.description:
        topics = [
            t.strip()
            for t in current_chapter.description.split(",")
            if t.strip()
        ]

    selected_topic = request.GET.get("topic")

    pdf_id = None
    video_id = None
    files_for_ui = []

    released_info = ReleasedContent.objects.filter(
        upload__chapter=current_chapter,
        release_status=True
    ).order_by("-id").first()

    if released_info and released_info.drive_folder_id:
        ids = _parse_drive_ids(released_info.drive_folder_id)

        if ids:
            try:
                service = GoogleDriveAuthService.get_service()

                def add_file(file_obj):
                    nonlocal pdf_id, video_id, files_for_ui

                    fid = file_obj.get("id")
                    name = file_obj.get("name") or "Untitled"
                    mt = file_obj.get("mimeType") or ""

                    stream_url = reverse(
                        "drive_stream",
                        args=[course.id, fid]
                    )

                    files_for_ui.append({
                        "id": fid,
                        "name": name,
                        "mimeType": mt,
                        "stream_url": stream_url,
                    })

                    mt_l = mt.lower()
                    if (not pdf_id) and ("pdf" in mt_l):
                        pdf_id = fid
                    if (not video_id) and ("video" in mt_l):
                        video_id = fid

                for any_id in ids:
                    meta = service.files().get(
                        fileId=any_id,
                        fields="id,name,mimeType"
                    ).execute()

                    # If it's a folder (like 11_30_2)
                    if _is_folder(meta.get("mimeType")):

                        # LEVEL 1 → topic folders
                        query = f"'{any_id}' in parents and trashed=false"
                        results = service.files().list(
                            q=query,
                            fields="files(id,name,mimeType)",
                            pageSize=200
                        ).execute()

                        topic_folders = results.get("files", [])

                        for topic_folder in topic_folders:

                            # We only care about folders at this level
                            if not _is_folder(topic_folder.get("mimeType")):
                                continue

                            topic_name = topic_folder.get("name")

                            # If topic is selected → filter
                            if selected_topic and topic_name != selected_topic:
                                continue

                            # LEVEL 2 → actual files inside topic folder
                            sub_query = f"'{topic_folder.get('id')}' in parents and trashed=false"
                            sub_results = service.files().list(
                                q=sub_query,
                                fields="files(id,name,mimeType)",
                                pageSize=200
                            ).execute()

                            for file_obj in sub_results.get("files", []):
                                if _is_folder(file_obj.get("mimeType")):
                                    continue
                                add_file(file_obj)

                    else:
                        # If directly file
                        add_file(meta)

            except Exception as e:
                logger.exception(
                    "Failed to load Drive content for chapter: %s",
                    e
                )

    # -----------------------------------------------------------------------
    # Assessment Engine: serve quiz from the winning contributor only
    # -----------------------------------------------------------------------
    official_assessments = []
    student_attempts_by_assessment = {}

    if current_chapter:
        best_upload = (
            UploadCheck.objects
            .filter(chapter=current_chapter, content_score__is_best=True)
            .order_by("-timestamp")
            .first()
        )

        if best_upload:
            qs = Assessment.objects.filter(
                chapter=current_chapter,
                contributor_id=best_upload.contributor,
            )
            if selected_topic:
                qs = qs.filter(topic=selected_topic)

            official_assessments = list(qs)

            # Build a quick lookup: assessment_id → best attempt for this student
            if official_assessments:
                attempts = AssessmentAttempt.objects.filter(
                    student=request.user,
                    assessment__in=official_assessments,
                ).order_by("-score")

                for att in attempts:
                    aid = att.assessment_id
                    if aid not in student_attempts_by_assessment:
                        student_attempts_by_assessment[aid] = att

    is_chapter_completed = False
    course_progress_percent = 0

    if current_chapter:
        is_chapter_completed = StudentChapterProgress.objects.filter(
            student=request.user,
            chapter=current_chapter,
            completed=True
        ).exists()

        # Calculate overall course progress
        released_chapter_ids = set(
            ReleasedContent.objects.filter(
                upload__chapter__course=course,
                release_status=True
            ).values_list('upload__chapter_id', flat=True)
        )
        completed_chapter_ids = set(
            StudentChapterProgress.objects.filter(
                student=request.user,
                chapter__course=course,
                completed=True,
            ).values_list('chapter_id', flat=True)
        )
        total_released = len(released_chapter_ids)
        total_completed = len(completed_chapter_ids & released_chapter_ids)
        if total_released > 0:
            course_progress_percent = round((total_completed / total_released) * 100)

    context = {
        "course": course,
        "chapters": chapters,
        "chapter": current_chapter,
        "topics": topics,
        "selected_topic": selected_topic,
        "pdf_id": pdf_id,
        "video_id": video_id,
        "files_for_ui": files_for_ui,
        "is_released": bool(released_info and released_info.release_status),
        # Assessment engine
        "official_assessments": official_assessments,
        "student_attempts": student_attempts_by_assessment,
        # Progress engine
        "is_chapter_completed": is_chapter_completed,
        "course_progress_percent": course_progress_percent,
    }

    return render(request, "student/chapter_topics.html", context)