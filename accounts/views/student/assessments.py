"""
accounts/views/student/assessments.py

Assessment Taking Engine — Student Side
----------------------------------------
Views:
  take_assessment     : renders quiz form for a student
  submit_assessment   : grades the submitted form, creates AssessmentAttempt
  assessment_result   : shows score, pass/fail, and per-question review
"""

import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Assessment, AssessmentAttempt, EnrolledCourse

logger = logging.getLogger(__name__)

# Passing threshold (70%)
PASS_THRESHOLD = 0.70


# ---------------------------------------------------------------------------
# 1. Take Assessment
# ---------------------------------------------------------------------------

@login_required
def take_assessment(request, assessment_id):
    """
    Render the quiz form for a student.
    Security: student must be enrolled in the course that contains this assessment.
    """
    assessment = get_object_or_404(Assessment, id=assessment_id)

    # Enrollment guard
    if not EnrolledCourse.objects.filter(
        student=request.user,
        course=assessment.course
    ).exists():
        return HttpResponseForbidden("You must be enrolled in this course to take this quiz.")

    questions = assessment.questions.prefetch_related('options').all()

    # Fetch the student's previous best attempt (if any) for display
    best_attempt = (
        AssessmentAttempt.objects
        .filter(student=request.user, assessment=assessment)
        .order_by('-score')
        .first()
    )

    return render(request, 'student/take_assessment.html', {
        'assessment': assessment,
        'questions': questions,
        'best_attempt': best_attempt,
    })


# ---------------------------------------------------------------------------
# 2. Submit Assessment
# ---------------------------------------------------------------------------

@login_required
@require_POST
def submit_assessment(request, assessment_id):
    """
    Grade the submitted quiz and store the attempt.
    Expects POST data in the format: q_<question_id> = <chosen_option_index>
    """
    assessment = get_object_or_404(Assessment, id=assessment_id)

    # Enrollment guard
    if not EnrolledCourse.objects.filter(
        student=request.user,
        course=assessment.course
    ).exists():
        return HttpResponseForbidden("You must be enrolled in this course.")

    questions = list(assessment.questions.prefetch_related('options').all())

    score = 0
    total = len(questions)
    answers_snapshot = {}

    for q in questions:
        raw = request.POST.get(f'q_{q.id}')
        if raw is None:
            answers_snapshot[str(q.id)] = None
            continue
        try:
            chosen_index = int(raw)
        except (ValueError, TypeError):
            answers_snapshot[str(q.id)] = None
            continue

        answers_snapshot[str(q.id)] = chosen_index
        if chosen_index == q.correct_option:
            score += 1

    passed = (score / total) >= PASS_THRESHOLD if total > 0 else False

    attempt = AssessmentAttempt.objects.create(
        student=request.user,
        assessment=assessment,
        score=score,
        total_questions=total,
        passed=passed,
        answers_snapshot=answers_snapshot,
    )

    logger.info(
        "[AssessmentAttempt] student=%s assessment=%s score=%s/%s passed=%s",
        request.user.username, assessment_id, score, total, passed
    )

    return redirect('assessment_result', attempt_id=attempt.id)


# ---------------------------------------------------------------------------
# 3. Assessment Result
# ---------------------------------------------------------------------------

@login_required
def assessment_result(request, attempt_id):
    """
    Show the student their detailed score and per-question review.
    Only the student who made the attempt can view their result.
    """
    attempt = get_object_or_404(AssessmentAttempt, id=attempt_id)

    if attempt.student != request.user:
        return HttpResponseForbidden("You can only view your own results.")

    assessment = attempt.assessment
    questions = list(assessment.questions.prefetch_related('options').all())

    # Build per-question review data
    review = []
    snapshot = attempt.answers_snapshot or {}

    for q in questions:
        chosen = snapshot.get(str(q.id))   # may be None if skipped
        options = list(q.options.all())
        review.append({
            'text': q.text,
            'options': options,
            'correct_index': q.correct_option,
            'chosen_index': chosen,
            'is_correct': (chosen == q.correct_option) if chosen is not None else False,
            'was_skipped': chosen is None,
        })

    return render(request, 'student/result_assessment.html', {
        'attempt': attempt,
        'assessment': assessment,
        'review': review,
    })
