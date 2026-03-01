from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse

from accounts.models import Course, Chapter, ReleasedContent, EnrolledCourse
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
        return render(request, "student/locked_error.html", {"error_message": "Enrollment required."})

    chapters = Chapter.objects.filter(course=course).order_by("chapter_number")

    selected_chapter_id = request.GET.get("chapter_id")
    if selected_chapter_id:
        current_chapter = get_object_or_404(Chapter, id=selected_chapter_id, course=course)
    else:
        current_chapter = chapters.first()

    topics = []
    if current_chapter and current_chapter.description:
        topics = [t.strip() for t in current_chapter.description.split(",") if t.strip()]

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

                    stream_url = reverse("drive_stream", args=[course.id, fid])

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
                    # First: check what this id is (folder or file)
                    meta = service.files().get(
                        fileId=any_id,
                        fields="id,name,mimeType"
                    ).execute()

                    if _is_folder(meta.get("mimeType")):
                        # List children of folder
                        query = f"'{any_id}' in parents and trashed=false"
                        results = service.files().list(
                            q=query,
                            fields="files(id,name,mimeType)",
                            pageSize=200
                        ).execute()

                        for f in results.get("files", []):
                            # ignore nested folders in UI (optional)
                            if _is_folder(f.get("mimeType")):
                                continue
                            add_file(f)
                    else:
                        # It's a file id, use directly
                        add_file(meta)

            except Exception as e:
                logger.exception("Failed to load Drive content for chapter: %s", e)

    context = {
        "course": course,
        "chapters": chapters,
        "chapter": current_chapter,
        "topics": topics,
        "selected_topic": request.GET.get("topic"),
        "pdf_id": pdf_id,
        "video_id": video_id,
        "files_for_ui": files_for_ui,
        "is_released": bool(released_info and released_info.release_status),
    }
    return render(request, "student/chapter_topics.html", context)