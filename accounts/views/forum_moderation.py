from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.models import ForumQuestion, ForumAnswer
from django.contrib.auth.decorators import login_required
from django.db import transaction, IntegrityError
from django.contrib.auth import get_user_model
from accounts.models import DmThread, DmMessage

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

    return render(request, "forum/moderation_queue.html", {
        "pending_questions": pending_questions,
        "pending_answers": pending_answers,
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
