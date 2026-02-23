from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.models import ForumQuestion, ForumAnswer, ReportCase
from django.contrib.auth.decorators import login_required
from django.db import transaction, IntegrityError
from django.contrib.auth import get_user_model
from django.utils import timezone
from accounts.models import DmThread, DmMessage

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.views.decorators.http import require_POST

from accounts.models import User

@staff_member_required
@require_POST
def unsuspend_user(request, user_id):
    u = get_object_or_404(User, pk=user_id)
    u.forum_is_suspended = False
    u.forum_suspended_at = None
    u.forum_suspension_reason = ""
    u.save(update_fields=["forum_is_suspended", "forum_suspended_at", "forum_suspension_reason"])
    messages.success(request, f"Unsuspended {u.username}.")
    return redirect("forum_moderation_queue")

def _get_moderator_user():
    """
    Create/get a system user used only for sending moderation messages.
    It won't reveal real staff identity in DMs.
    """
    User = get_user_model()
    moderator, created = User.objects.get_or_create(
        username="moderator",
        defaults={"first_name": "Moderator", "is_active": True},
    )
    if created:
        moderator.set_unusable_password()
        moderator.save(update_fields=["password"])
    return moderator


def _send_moderation_message(to_user, text: str):
    moderator = _get_moderator_user()
    if moderator.id == to_user.id:
        return

    a, b = (moderator, to_user) if moderator.id < to_user.id else (to_user, moderator)

    try:
        with transaction.atomic():
            thread, _ = DmThread.objects.get_or_create(user_a=a, user_b=b)
    except IntegrityError:
        thread = DmThread.objects.get(user_a=a, user_b=b)

    DmMessage.objects.create(thread=thread, sender=moderator, content=text)



@require_GET
@login_required

def forum_moderation_queue(request):
    if not request.user.is_staff:
        raise Http404()
    pending_questions = (
        ForumQuestion.objects
        .filter(is_hidden=True, moderation_status="pending_review")
        .select_related("author", "course", "chapter")
        .order_by("-created_at")[:100]
    )

    pending_answers = (
        ForumAnswer.objects
        .filter(is_hidden=True, moderation_status="pending_review")
        .select_related("author", "question")
        .order_by("-created_at")[:200]
    )


    reported_cases = (
        ReportCase.objects
        .filter(needs_review=True, status="open")
        .select_related("question", "answer", "dm_message", "target_user")
        .order_by("-updated_at")[:200]
    )


    return render(request, "forum/moderation_queue.html", {
        "pending_questions": pending_questions,
        "pending_answers": pending_answers,
        "reported_cases": reported_cases,
    })


@login_required
@require_POST
def forum_moderation_action(request):
    if not request.user.is_staff:
        raise Http404()
    kind = request.POST.get("kind")  # "question" | "answer"
    obj_id = request.POST.get("id")
    action = request.POST.get("action")  # "approve" | "reject"
    note = (request.POST.get("note") or "").strip()

    if kind not in {"question", "answer"}:
        raise Http404("Invalid kind")

    Model = ForumQuestion if kind == "question" else ForumAnswer
    obj = Model.objects.filter(pk=obj_id).first()
    if not obj:
        raise Http404("Not found")

    details = obj.moderation_details or {}
    if note:
        details["staff_note"] = note

    if action == "approve":
        obj.is_hidden = False
        obj.moderation_status = "approved"
        obj.moderation_details = details
        obj.save(update_fields=["is_hidden", "moderation_status", "moderation_details"])
        messages.success(request, f"{kind.title()} approved.")

    elif action == "reject":
        # store note in details (optional, useful for DM)
        obj.moderation_details = details
        obj.save(update_fields=["moderation_details"])

        # Send DM to the author (from Moderator)
# Build context BEFORE deleting anything
        if kind == "question":
            context = f"Question: {obj.title}"
        else:
            context = f"On question: {obj.question.title}"

        note_text = note if note else "(No note provided)"

        dm_text = (
            "SYSTEM MESSAGE (auto-generated)\n"
            "============================\n"
            "Status: REJECTED\n"
            f"{context}\n\n"
            "Moderator note:\n"
            f"{note_text}\n\n"
            "What you can do next:\n"
            "• Edit your content and post again.\n"
            "• Avoid threats/abuse/personal attacks.\n\n"
            "Replies are disabled for this system message."
        )
        _send_moderation_message(obj.author, dm_text)
        obj.delete()
        messages.success(request, f"{kind.title()} rejected and removed.")
        return redirect("forum_moderation_queue")



        # ✅ Delete from everywhere (no space, no leaks)
        obj.delete()

        messages.success(request, f"{kind.title()} rejected and removed.")

    else:
        messages.error(request, "Invalid action.")

    return redirect("forum_moderation_queue")


@require_POST
@login_required
def forum_reportcase_action(request):
    if not request.user.is_staff:
        raise Http404()

    case_id = request.POST.get("case_id")
    action = request.POST.get("action")  # dismiss | actioned | suspend_user
    note = (request.POST.get("note") or "").strip()

    case = ReportCase.objects.filter(pk=case_id).select_related("question", "answer", "dm_message", "target_user").first()
    if not case:
        raise Http404("Report case not found")

    if action == "dismiss":
        case.status = "dismissed"
        case.needs_review = False
        case.save(update_fields=["status", "needs_review", "updated_at"])
        messages.success(request, "Report dismissed.")
        return redirect("forum_moderation_queue")

    if action == "actioned":
        case.status = "actioned"
        case.needs_review = False
        case.save(update_fields=["status", "needs_review", "updated_at"])
        messages.success(request, "Marked as action taken.")
        return redirect("forum_moderation_queue")

    if action == "suspend_user":
        # Determine user to suspend based on target
        u = case.target_user
        if u is None and case.question_id:
            u = case.question.author
        elif u is None and case.answer_id:
            u = case.answer.author
        elif u is None and case.dm_message_id:
            u = case.dm_message.sender

        if not u:
            messages.error(request, "No user found for this case.")
            return redirect("forum_moderation_queue")

        if u.is_staff:
            messages.error(request, "Staff accounts cannot be auto-suspended here.")
            return redirect("forum_moderation_queue")

        u.forum_is_suspended = True
        u.forum_suspended_at = timezone.now()
        u.forum_suspension_reason = note or "Suspended due to reports."
        u.save(update_fields=["forum_is_suspended", "forum_suspended_at", "forum_suspension_reason"])

        case.status = "actioned"
        case.needs_review = False
        case.save(update_fields=["status", "needs_review", "updated_at"])

        messages.success(request, f"User {u.username} suspended.")
        return redirect("forum_moderation_queue")

    messages.error(request, "Invalid action.")
    return redirect("forum_moderation_queue")
