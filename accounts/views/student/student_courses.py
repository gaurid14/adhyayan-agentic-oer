from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from datetime import date, timedelta

# ✅ Import your models
from ...models import EnrolledCourse, CourseCompletion, ChapterCompletion


@login_required
def student_courses(request):
    user = request.user

    # =========================
    # ✅ ENROLLED COURSES
    # =========================
    enrollments = user.enrollments.select_related('course')
    enrolled_courses = [e.course for e in enrollments]

    total_courses = len(enrolled_courses)

    # =========================
    # ✅ INITIAL COUNTERS
    # =========================
    completed_courses = 0
    in_progress_courses = 0
    total_chapters_completed = 0

    course_progress_data = []

    # =========================
    # ✅ LOOP THROUGH COURSES
    # =========================
    for course in enrolled_courses:

        total_chapters = course.chapters.count()

        # ✅ Get completed chapters for this course
        completed_chapters = ChapterCompletion.objects.filter(
            student=user,
            chapter__course=course,
            completed=True
        ).count()

        # ✅ Calculate progress %
        progress = 0
        if total_chapters > 0:
            progress = int((completed_chapters / total_chapters) * 100)

        # =========================
        # ✅ COURSE COMPLETION LOGIC
        # =========================
        if progress == 100:
            completed_courses += 1

            # ensure CourseCompletion exists
            CourseCompletion.objects.get_or_create(
                student=user,
                course=course
            )
        elif progress > 0:
            in_progress_courses += 1

        total_chapters_completed += completed_chapters

        # =========================
        # ✅ STORE DATA FOR TEMPLATE
        # =========================
        course_progress_data.append({
            'course': course,
            'progress': progress,
        })

    # =========================
    # ✅ NOT STARTED (for chart)
    # =========================
    not_started = total_courses - (completed_courses + in_progress_courses)

    # =========================
    # 🔥 SIMPLE STREAK (TEMP)
    # =========================
 
    # ✅ Get all activity dates
    activity_dates = ChapterCompletion.objects.filter(
    student=user,
    completed=True
    ).values_list('completed_at__date', flat=True).distinct()

    activity_dates = sorted(activity_dates, reverse=True)

    streak = 0

    if activity_dates:
        today = date.today()
        first_day = activity_dates[0]

        if first_day == today or first_day == today - timedelta(days=1):

            for i, activity_date in enumerate(activity_dates):
                expected_date = first_day - timedelta(days=i)

                if activity_date == expected_date:
                    streak += 1
                else:
                    break
    # =========================
    # ✅ CONTEXT
    # =========================
    context = {
        'courses': course_progress_data,

        'total_courses': total_courses,
        'completed_courses': completed_courses,
        'in_progress_courses': in_progress_courses,
        'not_started': not_started,
        'chapters_completed': total_chapters_completed,

        'streak': streak,
    }

    return render(request, 'student/student_courses.html', context)