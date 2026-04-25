from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, List, Tuple

from django.utils import timezone

from accounts.models import (
    AssessmentAttempt,
    BlockchainCertificate,
    Chapter,
    Course,
    CourseCompletion,
    EnrolledCourse,
    ReleasedContent,
    StudentChapterProgress,
)


def _to_local_date(value):
    if not value:
        return None
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.date()


def _streak_from_dates(activity_dates):
    if not activity_dates:
        return 0, None

    today = timezone.localdate()
    streak = 0
    cursor = today
    while cursor in activity_dates:
        streak += 1
        cursor -= timedelta(days=1)

    if streak > 0:
        return streak, today

    latest = max(activity_dates)
    streak = 1
    cursor = latest - timedelta(days=1)
    while cursor in activity_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak, latest


def get_student_activity_dates(student) -> set:
    dates = set()

    dates.update(
        d for d in (
            _to_local_date(value)
            for value in StudentChapterProgress.objects.filter(
                student=student,
                completed=True,
            ).values_list("completed_at", flat=True)
        ) if d
    )

    dates.update(
        d for d in (
            _to_local_date(value)
            for value in AssessmentAttempt.objects.filter(student=student).values_list("created_at", flat=True)
        ) if d
    )

    dates.update(
        d for d in (
            _to_local_date(value)
            for value in CourseCompletion.objects.filter(student=student).values_list("completed_at", flat=True)
        ) if d
    )

    return dates


def build_student_learning_summary(student) -> Dict[str, Any]:
    enrollments = (
        EnrolledCourse.objects
        .filter(student=student)
        .select_related("course", "course__scheme", "course__department", "course__department__program")
        .order_by("course__course_name")
    )

    enrolled_courses: List[Course] = [enrollment.course for enrollment in enrollments]
    enrolled_course_ids = [course.id for course in enrolled_courses]

    completed_course_ids = set(
        CourseCompletion.objects.filter(student=student).values_list("course_id", flat=True)
    )

    released_qs = (
        ReleasedContent.objects
        .filter(release_status=True, upload__chapter__course_id__in=enrolled_course_ids)
        .select_related("upload__chapter", "upload__chapter__course")
    )

    released_by_course = defaultdict(int)
    released_chapter_ids_by_course = defaultdict(set)
    for item in released_qs:
        course_id = item.upload.chapter.course_id
        released_by_course[course_id] += 1
        released_chapter_ids_by_course[course_id].add(item.upload.chapter_id)

    completed_progress_qs = (
        StudentChapterProgress.objects
        .filter(student=student, completed=True, chapter__course_id__in=enrolled_course_ids)
        .select_related("chapter", "chapter__course")
    )

    completed_by_course = defaultdict(int)
    chapter_completion_rows = []
    for progress in completed_progress_qs:
        course_id = progress.chapter.course_id
        if progress.chapter_id in released_chapter_ids_by_course.get(course_id, set()):
            completed_by_course[course_id] += 1
            chapter_completion_rows.append(progress)

    total_enrolled = len(enrolled_courses)
    completed_courses = sum(1 for course_id in enrolled_course_ids if course_id in completed_course_ids)
    in_progress_courses = max(total_enrolled - completed_courses, 0)
    chapters_completed = sum(completed_by_course.values())
    total_released_chapters = sum(released_by_course.values())
    overall_progress_percent = round((chapters_completed / total_released_chapters) * 100) if total_released_chapters else 0

    activity_dates = get_student_activity_dates(student)
    streak_days, streak_anchor_date = _streak_from_dates(activity_dates)
    last_activity_date = max(activity_dates) if activity_dates else None

    course_cards: List[Dict[str, Any]] = []
    for course in enrolled_courses:
        released_count = released_by_course.get(course.id, 0)
        completed_count = completed_by_course.get(course.id, 0)
        progress_percent = round((completed_count / released_count) * 100) if released_count else 0
        is_completed = course.id in completed_course_ids

        if is_completed:
            status = "Completed"
        elif released_count == 0:
            status = "Waiting for release"
        else:
            status = "In progress"

        course_cards.append({
            "course": course,
            "released_count": released_count,
            "completed_count": completed_count,
            "progress_percent": progress_percent,
            "status": status,
            "is_completed": is_completed,
        })

    return {
        "enrollments": enrollments,
        "enrolled_courses": enrolled_courses,
        "enrolled_course_ids": enrolled_course_ids,
        "completed_course_ids": completed_course_ids,
        "completed_courses_count": completed_courses,
        "in_progress_courses_count": in_progress_courses,
        "total_enrolled_courses": total_enrolled,
        "chapters_completed_count": chapters_completed,
        "total_released_chapters": total_released_chapters,
        "overall_progress_percent": overall_progress_percent,
        "streak_days": streak_days,
        "streak_anchor_date": streak_anchor_date,
        "last_activity_date": last_activity_date,
        "course_cards": course_cards,
        "released_by_course": released_by_course,
        "completed_by_course": completed_by_course,
        "chapter_completion_rows": chapter_completion_rows,
        "certificates": BlockchainCertificate.objects.filter(
            user=student,
            certificate_type=BlockchainCertificate.CERT_TYPE_STUDENT,
        ),
    }
