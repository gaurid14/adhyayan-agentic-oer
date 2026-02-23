import json
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from accounts.models import Course, Chapter, ReleasedContent, EnrolledCourse
from langgraph_agents.services.drive_service import GoogleDriveAuthService

@login_required
def student_dashboard(request):
    """
    Main dashboard for students.
    Displays all available courses but differentiates buttons based on enrollment.
    """
    # 1. Fetch ALL available courses (for the "Enroll Now" section)
    all_courses = Course.objects.all()

    # 2. Fetch IDs of courses the student is ALREADY enrolled in
    enrolled_course_ids = list(EnrolledCourse.objects.filter(
        student=request.user
    ).values_list('course_id', flat=True))

    # 3. Fetch courses the student is enrolled in (for the KPI card count)
    enrolled_courses_qs = Course.objects.filter(id__in=enrolled_course_ids)

    # 4. Prepare JSON for the sidebar drawer (keeping for legacy support if needed)
    released_content = ReleasedContent.objects.filter(
        release_status=True,
        upload__chapter__course__id__in=enrolled_course_ids
    ).select_related('upload__chapter')

    chapters_dict = {}
    for item in released_content:
        course_id = item.upload.chapter.course_id
        if course_id not in chapters_dict:
            chapters_dict[course_id] = []

        chapters_dict[course_id].append({
            'id': item.upload.chapter.id,
            'chapter_number': item.upload.chapter.chapter_number,
            'chapter_name': item.upload.chapter.chapter_name,
            'drive_url': f"https://drive.google.com/drive/folders/{item.drive_folder_id}" if item.drive_folder_id else None
        })

    return render(
        request,
        "student/student_dashboard.html",
        {
            "all_courses": all_courses,           # Used to display all 6 cards
            "enrolled_course_ids": enrolled_course_ids, # Used to check button type
            "courses": enrolled_courses_qs,       # Used for KPI count
            "chapters_json": json.dumps(chapters_dict),
            "enrolled_courses": enrolled_courses_qs,
            "completed_courses": []
        }
    )

@login_required
def student_course_chapters(request, course_id):
    """
    Detailed chapter list for a specific course.
    SECURITY: Blocks access if the student is not enrolled in the course.
    """
    course = get_object_or_404(Course, id=course_id)

    # SECURITY: Enrollment Check
    is_enrolled = EnrolledCourse.objects.filter(student=request.user, course=course).exists()
    if not is_enrolled:
        return render(request, "student/locked_error.html", {
            "error_message": "Access Denied: You must be enrolled in this course to view its chapters."
        })

    # Get chapters and check which ones the Admin Agent has released
    chapters_qs = Chapter.objects.filter(course=course).order_by("chapter_number")
    released_chapter_ids = set(
        ReleasedContent.objects.filter(
            upload__chapter__course=course,
            release_status=True
        ).values_list('upload__chapter_id', flat=True)
    )

    chapters = []
    for ch in chapters_qs:
        is_released = ch.id in released_chapter_ids
        chapters.append({
            "id": ch.id,
            "order": ch.chapter_number,
            "name": ch.chapter_name,
            "description": ch.description,
            "status": "not_started" if is_released else "locked",
            "is_locked": not is_released,
            "progress_percent": 0,
        })

    context = {
        "course": {
            "id": course.id,
            "name": course.course_name,
            "code": course.course_code,
            "semester": course.semester,
        },
        "chapters": chapters,
        "course_progress_percent": 0,
        "course_progress_message": "Unlock chapters sequentially by completing prerequisites! 🔥",
    }
    return render(request, "student/course_chapters.html", context)

@login_required
def student_topic_view(request):
    """
    Embedded Topic view (NPTEL/Udemy style).
    Directly fetches PDF and Video IDs from Google Drive for <iframe> embedding.
    """
    course_id = request.GET.get("course_id")
    chapter_id = request.GET.get("chapter_id")
    selected_topic = request.GET.get("topic")

    if not course_id or not chapter_id:
        return render(request, "student/student_chapter_topics.html", {"error": "Missing Course or Chapter ID"})

    course = get_object_or_404(Course, id=course_id)
    chapter = get_object_or_404(Chapter, id=chapter_id, course=course)


    # 1. SECURITY: Enrollment Check
    is_enrolled = EnrolledCourse.objects.filter(student=request.user, course=course).exists()
    if not is_enrolled:
        return render(request, "student/locked_error.html", {"error_message": "Access Denied: Enrollment required."})

    # 2. SECURITY: Release Check (Has the Admin Agent officially released this chapter?)
    released_info = ReleasedContent.objects.filter(
        upload__chapter=chapter,
        release_status=True
    ).first()

    if not released_info:
        return render(request, "student/locked_error.html", {"chapter": chapter})

    # 3. DRIVE INTEGRATION: Fetch PDF and Video File IDs for Embedding
    service = GoogleDriveAuthService.get_service()
    pdf_embed_id = None
    video_embed_id = None

    if released_info.drive_folder_id:
        # Querying files inside the released folder identified by Admin Agent
        query = f"'{released_info.drive_folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
        drive_files = results.get('files', [])

        for f in drive_files:
            if 'pdf' in f['mimeType']:
                pdf_embed_id = f['id']
            elif 'video' in f['mimeType']:
                video_embed_id = f['id']

    # 4. TOPIC EXTRACTION: Parse comma-separated topics from chapter description
    raw_description = chapter.description or ""
    topics = [t.strip() for t in raw_description.split(",") if t.strip()]

    # context = {
    #     "course": {
    #         "id": course.id,
    #         "name": course.course_name,
    #         "code": course.course_code,
    #         "semester": course.semester,
    #     },
    #     "chapter": {
    #         "id": chapter.id,
    #         "name": chapter.chapter_name,
    #         "number": chapter.chapter_number,
    #     },
    #     "topics": topics,
    #     "selected_topic": selected_topic,
    #     "pdf_id": pdf_embed_id,
    #     "video_id": video_embed_id,
    #     "topic_resources": {"videos": [], "files": [], "assessments": []}, # Keeping for template compatibility
    # }

    context = {
        "course": course,
        "chapter": chapter,
        "topics": topics,
        "selected_topic": selected_topic,
        "pdf_id": pdf_embed_id,
        "video_id": video_embed_id,
    }
    return render(request, "student/student_chapter_topics.html", context)

from django.views.decorators.http import require_POST
from accounts.models import EnrolledCourse

@login_required
@require_POST
def enroll_course(request):
    course_id = request.POST.get("course_id")
    course = get_object_or_404(Course, id=course_id)

    # Create the enrollment record for the current student
    EnrolledCourse.objects.get_or_create(student=request.user, course=course)

    # Redirect back to the dashboard
    return redirect('student_dashboard')