"""
accounts/views/student/progress.py

Student Progress Engine
------------------------
Provides a single POST endpoint that lets a student mark a chapter
as complete. After each mark, it checks whether the whole course is
now done and creates a CourseCompletion record if so.
"""

import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import (
    Assessment, AssessmentAttempt,
    Chapter, CourseCompletion, EnrolledCourse,
    ReleasedContent, StudentChapterProgress,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mark a single chapter as complete
# ---------------------------------------------------------------------------

@login_required
@require_POST
def mark_chapter_complete(request, chapter_id):
    """
    Toggle a chapter to 'completed' for the current student.

    POST /student/chapter/<chapter_id>/mark-complete/
    Returns JSON  {"success": true, "already_done": false}
    """
    chapter = get_object_or_404(Chapter, id=chapter_id)

    # 1. Enrollment guard
    if not EnrolledCourse.objects.filter(
        student=request.user,
        course=chapter.course,
    ).exists():
        return HttpResponseForbidden("Enrollment required.")

    # 2. Chapter must be released before a student can complete it
    is_released = ReleasedContent.objects.filter(
        upload__chapter=chapter,
        release_status=True,
    ).exists()
    if not is_released:
        return JsonResponse({"error": "Chapter content not yet released."}, status=400)

    # 3. Assessment gatekeeper — must have passed all chapter assessments
    chapter_assessments = Assessment.objects.filter(chapter=chapter)
    if chapter_assessments.exists():
        # For each assessment, check if the student has at least one passing attempt
        unpassed = []
        for assessment in chapter_assessments:
            has_passed = AssessmentAttempt.objects.filter(
                student=request.user,
                assessment=assessment,
                passed=True,
            ).exists()
            if not has_passed:
                unpassed.append(assessment.topic or "General Assessment")

        if unpassed:
            return JsonResponse({
                "error": "assessment_not_passed",
                "message": (
                    f"You must pass the following assessments before marking this chapter complete: "
                    f"{', '.join(unpassed)}. (Pass mark: 70%)"
                ),
            }, status=400)

    # 4. Upsert the progress row
    progress, created = StudentChapterProgress.objects.get_or_create(
        student=request.user,
        chapter=chapter,
        defaults={"completed": False},
    )

    already_done = progress.completed

    if not already_done:
        progress.completed    = True
        progress.completed_at = timezone.now()
        progress.save(update_fields=["completed", "completed_at"])

        logger.info(
            "[Progress] student=%s marked chapter_id=%s as complete",
            request.user.username, chapter_id,
        )

        # 5. Check if the entire course is now finished
        _check_and_record_course_completion(request.user, chapter.course)

    return JsonResponse({"success": True, "already_done": already_done})


# ---------------------------------------------------------------------------
# Internal helper: course completion check
# ---------------------------------------------------------------------------

def _check_and_record_course_completion(student, course):
    """
    If every released chapter in the course has been marked complete
    by this student, create a CourseCompletion record (idempotent).
    """
    # All chapter IDs that currently have released content
    released_chapter_ids = set(
        ReleasedContent.objects.filter(
            upload__chapter__course=course,
            release_status=True,
        ).values_list("upload__chapter_id", flat=True)
    )

    if not released_chapter_ids:
        return  # nothing released yet — can't complete

    # Chapter IDs this student has actually completed
    completed_ids = set(
        StudentChapterProgress.objects.filter(
            student=student,
            chapter_id__in=released_chapter_ids,
            completed=True,
        ).values_list("chapter_id", flat=True)
    )

    if released_chapter_ids == completed_ids:
        _, created = CourseCompletion.objects.get_or_create(
            student=student,
            course=course,
        )
        if created:
            logger.info(
                "[Progress] [Graduation] student=%s completed course_id=%s",
                student.username, course.id,
            )

            # ── Student Certificate Minting ──────────────────────────────────
            try:
                from blockchain.services.certificate_service import (
                    mint_certificate, ISSUE_TYPE_STUDENT
                )
                from accounts.models import BlockchainCertificate

                result = mint_certificate(
                    recipient_name=student.get_full_name() or student.username,
                    course_name=course.course_name,
                    issue_type=ISSUE_TYPE_STUDENT,
                )
                if result.get("success"):
                    BlockchainCertificate.objects.get_or_create(
                        token_id=result["token_id"],
                        defaults={
                            "user": student,
                            "course": course,
                            "certificate_type": BlockchainCertificate.CERT_TYPE_STUDENT,
                            "tx_hash": result["tx_hash"],
                        }
                    )
                    logger.info(
                        "[Certificate] Student cert minted for %s | Course: %s | token_id=%s",
                        student.username, course.course_name, result["token_id"]
                    )
                else:
                    logger.error("[Certificate] Minting failed: %s", result.get("error"))
            except Exception as e:
                logger.error("[Certificate] Student cert exception: %s", e)
