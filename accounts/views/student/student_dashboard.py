from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required

from ...models import Course, Chapter

from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def student_dashboard(request):
    courses = [
        {"id": 1, "name": "Microcontroller Embedded Programming", "code": "ITD05011"},
        {"id": 2, "name": "Advance Data Management Technologies", "code": "ITD05012"},
        {"id": 3, "name": "Computer Graphics & Multimedia", "code": "ITD05013"},
        {"id": 4, "name": "Advanced Data Structure", "code": "ITD05014"},
        {"id": 5, "name": "Internet Programming", "code": "ITD05015"},
        {"id": 6, "name": "Computer Network Security", "code": "ITD05016"},
    ]

    return render(
        request,
        "student/student_dashboard.html",
        {
            "courses": courses
        }
    )



# @login_required
def student_course_chapters(request, course_id):
    # ✅ Get course
    course = get_object_or_404(Course, id=course_id)

    # ✅ Get all chapters only for this course
    chapters_qs = Chapter.objects.filter(course=course).order_by("chapter_number")

    chapters = []
    completed_count = 0

    for ch in chapters_qs:
        # 🔥 dummy for now (later student progress table se aayega)
        status = "not_started"     # completed | in_progress | not_started
        progress_percent = 0

        chapters.append({
            "id": ch.id,
            "order": ch.chapter_number,          # ✅ for template "Chapter {{ chapter.order }}"
            "name": ch.chapter_name,             # ✅ for template "{{ chapter.name }}"
            "description": ch.description,        # ✅ already exists
            "status": status,
            "progress_percent": progress_percent,
        })

        if status == "completed":
            completed_count += 1

    total = len(chapters)
    course_progress_percent = int((completed_count / total) * 100) if total > 0 else 0

    context = {
        "course": {
            "id": course.id,
            "name": course.course_name,          # ✅ mapped for template
            "code": course.course_code,          # ✅ mapped for template
            "semester": course.semester,         # ✅ mapped for template
        },
        "chapters": chapters,
        "course_progress_percent": course_progress_percent,
        "course_progress_message": "You're doing great! Keep going 🔥",
    }

    return render(request, "student/course_chapters.html", context)


# @login_required
def student_topic_view(request):
    course_id = request.GET.get("course_id")
    chapter_id = request.GET.get("chapter_id")
    selected_topic = request.GET.get("topic")  # optional

    # ✅ Safety check
    if not course_id or not chapter_id:
        return render(request, "student/student_chapter_topics.html", {
            "error": "course_id and chapter_id are required."
        })

    # ✅ Fetch course & chapter using your models
    course = get_object_or_404(Course, id=course_id)
    chapter = get_object_or_404(Chapter, id=chapter_id, course=course)

    # ✅ Extract topics from chapter.description (comma separated)
    raw_description = chapter.description or ""
    topics = [t.strip() for t in raw_description.split(",") if t.strip()]

    # ✅ If topic param is present, validate it
    if selected_topic and selected_topic not in topics:
        selected_topic = None

    # ✅ For now resources are empty (later you will fill released videos/files/assessment)
    topic_resources = {
        "videos": [],
        "files": [],
        "assessments": []
    }

    context = {
        "course": {
            "id": course.id,
            "name": course.course_name,
            "code": course.course_code,
            "semester": course.semester,
        },
        "chapter": {
            "id": chapter.id,
            "name": chapter.chapter_name,
            "number": chapter.chapter_number,
        },
        "topics": topics,
        "selected_topic": selected_topic,
        "topic_resources": topic_resources,
    }

    return render(request, "student/student_chapter_topics.html", context)

    # Inside student_dashboard.py -> student_course_chapters view

    from accounts.models import ReleasedContent

    # ... inside the loop for ch in chapters_qs:
        # Check if the Admin Agent has released any content for this chapter
    is_released = ReleasedContent.objects.filter(
            upload__chapter=ch, 
            release_status=True
        ).exists()

    status = "not_started"
    if is_released:
            # If released, we can mark it as 'not_started' (available) 
            # instead of potentially 'locked' if you add that status.
            status = "not_started" 
    else:
            status = "locked" # You can add a 'locked' style to your CSS