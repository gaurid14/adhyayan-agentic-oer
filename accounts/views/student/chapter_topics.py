from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from accounts.models import Course, Chapter, ReleasedContent, EnrolledCourse
from langgraph_agents.services.drive_service import GoogleDriveAuthService

@login_required
def chapter_topics(request, course_id):
    course = get_object_or_404(Course, id=course_id)

    # 1. Security: Ensure student is enrolled
    if not EnrolledCourse.objects.filter(student=request.user, course=course).exists():
        return render(request, "student/locked_error.html", {"error_message": "Enrollment required."})

    # 2. Fetch all chapters for the sidebar
    chapters = Chapter.objects.filter(course=course).order_by("chapter_number")

    # 3. Handle selection (Default to first chapter if none selected)
    selected_chapter_id = request.GET.get("chapter_id")
    if selected_chapter_id:
        current_chapter = get_object_or_404(Chapter, id=selected_chapter_id, course=course)
    else:
        current_chapter = chapters.first()

    # 4. Extract Topics from Description (Comma-separated string in DB)
    topics = []
    if current_chapter and current_chapter.description:
        topics = [t.strip() for t in current_chapter.description.split(",") if t.strip()]

    # 5. Fetch Drive Content for the selected chapter
    pdf_id = None
    video_id = None
    released_info = ReleasedContent.objects.filter(upload__chapter=current_chapter, release_status=True).first()

    if released_info and released_info.drive_folder_id:
        service = GoogleDriveAuthService.get_service()
        query = f"'{released_info.drive_folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(id, mimeType)").execute()
        drive_files = results.get('files', [])

        for f in drive_files:
            if 'pdf' in f['mimeType']:
                pdf_id = f['id']
            elif 'video' in f['mimeType']:
                video_id = f['id']

    context = {
        "course": course,
        "chapters": chapters,
        "chapter": current_chapter,
        "topics": topics,
        "selected_topic": request.GET.get("topic"),
        "pdf_id": pdf_id,
        "video_id": video_id,
    }
    return render(request, "student/chapter_topics.html", context)