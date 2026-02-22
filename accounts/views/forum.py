# accounts/views/forum.py
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, F, IntegerField, ExpressionWrapper, OuterRef, Subquery
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.http import require_POST, require_http_methods
from ..models import (
    Chapter, Course, ForumQuestion, ForumAnswer, ForumTopic,
    DmThread, DmMessage, User
)
from ..forms import ForumQuestionForm, ForumAnswerForm, ForumTopicForm

from accounts.moderation_perspective import moderate_text, apply_decision_to_instance

def _clean_int_param(v):
    """
    Convert GET params like "", None, "None", "null" into real None.
    Return int for valid digits, else None.
    """
    if v is None:
        return None
    v = str(v).strip()
    if not v or v.lower() in {"none", "null", "undefined"}:
        return None
    if v.isdigit():
        return int(v)
    return None

def _clean_forum_text(text: str, max_len: int = 10000) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if "{%" in text:  # optional safety
        return ""
    return text[:max_len]

def _apply_question_visibility(request, qs):
    """Limit visibility of hidden questions to staff or the author."""
    if request.user.is_authenticated:
        if request.user.is_staff:
            return qs
        return qs.filter(Q(is_hidden=False) | Q(author=request.user))
    return qs.filter(is_hidden=False)




def forum_home(request):
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "new").strip()
    page = request.GET.get("page", 1)

    # ✅ sanitize params so "None" never breaks filters
    topic_id = _clean_int_param(request.GET.get("topic"))
    course_id = _clean_int_param(request.GET.get("course"))
    chapter_id = _clean_int_param(request.GET.get("chapter"))

    base_qs = (
        ForumQuestion.objects
        .select_related("author", "course", "chapter")
        .prefetch_related("topics")
        .annotate(
            upvote_count=Count("upvotes", distinct=True),
            top_answer_count=Count(
                "answers",
                filter=Q(answers__parent__isnull=True),
                distinct=True
            ),
        )
    )

    questions = base_qs

    # ✅ visibility rules (hidden content only for staff/author)
    questions = _apply_question_visibility(request, questions)

    if q:
        questions = questions.filter(Q(title__icontains=q) | Q(content__icontains=q))

    if topic_id is not None:
        questions = questions.filter(topics__id=topic_id).distinct()

    if course_id is not None:
        questions = questions.filter(course_id=course_id)

    if chapter_id is not None:
        questions = questions.filter(chapter_id=chapter_id)

    if sort == "upvotes":
        questions = questions.order_by("-upvote_count", "-created_at")
    elif sort == "answers":
        questions = questions.order_by("-top_answer_count", "-created_at")
    else:
        questions = questions.order_by("-created_at")

    topics = (
        ForumTopic.objects
        .annotate(num_questions=Count("questions"))
        .order_by("-num_questions", "name")
    )

    window = timezone.now() - timedelta(days=7)
    visible_base_qs = _apply_question_visibility(request, base_qs)
    trending = (
        visible_base_qs
        .annotate(
            q_up=Count("upvotes", distinct=True),
            recent_ans=Count("answers", filter=Q(answers__created_at__gte=window), distinct=True),
        )
        .annotate(score=ExpressionWrapper(F("q_up") * 2 + F("recent_ans"), output_field=IntegerField()))
        .order_by("-score", "-created_at")[:5]
    )

    my_discussions = None
    if request.user.is_authenticated:
        my_discussions = base_qs.filter(author=request.user).order_by("-created_at")[:5]

    top_users = (
        User.objects
        .annotate(
            q_ups=Count("forum_questions__upvotes", distinct=True),
            a_ups=Count("forum_answers__upvotes", distinct=True),
        )
        .annotate(total_upvotes=F("q_ups") + F("a_ups"))
        .filter(total_upvotes__gt=0)
        .order_by("-total_upvotes", "username")[:10]
    )

    paginator = Paginator(questions, 10)
    page_obj = paginator.get_page(page)

    courses = Course.objects.all().order_by("course_name", "course_code")
    chapters = Chapter.objects.none()
    if course_id is not None:
        chapters = Chapter.objects.filter(course_id=course_id).order_by("chapter_number", "chapter_name")

    context = {
        "questions": page_obj.object_list,
        "page_obj": page_obj,
        "topics": topics,
        "selected_topic": topic_id,
        "q": q,
        "sort": sort,
        "q_form": ForumQuestionForm(),
        "topic_form": ForumTopicForm(),
        "trending": trending,
        "my_discussions": my_discussions,
        "top_users": top_users,
        "courses": courses,
        "chapters": chapters,
        "selected_course": course_id,
        "selected_chapter": chapter_id,
    }
    return render(request, "forum/list.html", context)

def forum_detail(request, pk: int):
    question = get_object_or_404(
        ForumQuestion.objects
        .select_related("author", "course", "chapter")
        .prefetch_related("topics")
        .annotate(upvote_count=Count("upvotes", distinct=True)),
        pk=pk
    )

    # ✅ Question visibility
    if question.is_hidden:
        if request.user.is_authenticated and request.user.is_staff:
            pass
        elif (
            request.user.is_authenticated and
            request.user == question.author and
            question.moderation_status == "pending_review"
        ):
            pass
        else:
            raise Http404()

    # ✅ Answers / replies visibility:
    # staff: all
    # author: public + own pending_review hidden
        # others: only public
    answers_qs = (
        ForumAnswer.objects
        .filter(question=question)
        .select_related("author")
        .annotate(upvote_count=Count("upvotes", distinct=True))
        .order_by("created_at")
    )

    if request.user.is_authenticated and request.user.is_staff:
        visible_answers_qs = answers_qs
    elif request.user.is_authenticated:
        visible_answers_qs = answers_qs.filter(
            Q(is_hidden=False) |
            Q(is_hidden=True, moderation_status="pending_review", author=request.user)
        )
    else:
        visible_answers_qs = answers_qs.filter(is_hidden=False)

    answers = list(visible_answers_qs)


    # Build thread structure (parent -> children)
    by_parent = {}
    for a in answers:
        by_parent.setdefault(a.parent_id, []).append(a)

    top_answers = by_parent.get(None, [])
    thread_groups = []

    def walk(parent, level, out):
        kids = by_parent.get(parent.id, [])
        for k in kids:
            out.append({"answer": k, "level": level})
            walk(k, level + 1, out)

    for top in top_answers:
        replies = []
        walk(top, 1, replies)
        thread_groups.append({"top": top, "replies": replies})

    question.thread_groups = thread_groups
    question.top_answers = top_answers

    return render(request, "forum/detail.html", {
        "question": question,
        "a_form": ForumAnswerForm(),
    })
@login_required
@require_POST
def post_question(request):
    form = ForumQuestionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please fix the errors in the form.")
        return redirect("forum_home")

    title = _clean_forum_text(form.cleaned_data.get("title"), max_len=255)
    content = _clean_forum_text(form.cleaned_data.get("content"), max_len=10000)
    if not title or not content:
        messages.error(request, "Title/content cannot be empty.")
        return redirect("forum_home")

    qobj = form.save(commit=False)
    course = form.cleaned_data.get("course")
    chapter = form.cleaned_data.get("chapter")

    if not course:
        messages.error(request, "Please select a course.")
        return redirect("forum_home")
    if chapter and chapter.course_id != course.id:
        messages.error(request, "Selected chapter does not belong to that course.")
        return redirect("forum_home")

    qobj.course = course
    qobj.chapter = chapter
    qobj.author = request.user
    qobj.title = title
    qobj.content = content

    decision, err = moderate_text(f"{title}\n\n{content}")

    # 1) HARD BLOCK (do not save)
    if decision and decision.action == "block":
        messages.error(
            request,
            "Your question wasn’t published because it violates our community guidelines. "
            "Please rewrite and try again."
        )
        return redirect("forum_home")

    # 2) If moderation service failed, hide for non-staff (fail-soft)
    if err and not request.user.is_staff:
        qobj.is_hidden = True
        qobj.moderation_status = "pending_review"
        qobj.moderation_details = {"kind": "question", "error": err}

    # 3) Apply model decision (hide/review/allow) BUT protect pending-review for non-staff
    apply_decision_to_instance(qobj, decision, kind="question")

    # ✅ IMPORTANT: If decision says "review/hide", ensure it becomes pending_review for non-staff
    # (Different implementations name this differently, so we handle common cases safely.)
    if not request.user.is_staff and decision:
        if getattr(decision, "action", None) in {"review", "hide"}:
            qobj.is_hidden = True
            qobj.moderation_status = "pending_review"
        # Some implementations use flags instead of action strings:
        if getattr(decision, "should_hide", False) is True:
            qobj.is_hidden = True
            qobj.moderation_status = "pending_review"

    qobj.save()
    form.save_m2m()

    # 4) Message shown on detail page only (as per your design)
    if qobj.is_hidden and not request.user.is_staff:
        messages.success(request, "Post Hidden — pending review.", extra_tags="detail_only")
    else:
        messages.success(request, "Your question was posted.", extra_tags="detail_only")

    return redirect("forum_detail", pk=qobj.pk)

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

@login_required
@require_POST
def post_answer(request, question_id):
    question = get_object_or_404(ForumQuestion, pk=question_id)

    # If question is hidden, only staff OR author can interact (optional)
    if question.is_hidden and not (request.user.is_staff or request.user == question.author):
        raise Http404()

    form = ForumAnswerForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please fix the errors in your answer.")
        return redirect("forum_detail", pk=question.pk)

    content = _clean_forum_text(form.cleaned_data.get("content"), max_len=10000)
    if not content:
        messages.error(request, "Answer cannot be empty.")
        return redirect("forum_detail", pk=question.pk)

    ans = form.save(commit=False)
    ans.content = content
    ans.author = request.user
    ans.question = question
    ans.parent = None

    decision, err = moderate_text(content)

    # Hard block
    if decision and decision.action == "block":
        messages.error(
            request,
            "Your answer wasn’t published because it violates our community guidelines. "
            "Please rewrite and try again.",
            extra_tags="detail_only",
        )
        return redirect("forum_detail", pk=question.pk)

    # Fail-soft moderation error → pending review for non-staff
    if err and not request.user.is_staff:
        ans.is_hidden = True
        ans.moderation_status = "pending_review"
        ans.moderation_details = {"kind": "answer", "error": err}

    apply_decision_to_instance(ans, decision, kind="answer")
    ans.save()

    # ✅ message based on final hidden state (NO staff exception)
    if ans.is_hidden:
        messages.success(request, "Answer submitted — pending review.", extra_tags="detail_only")
    else:
        messages.success(request, "Your answer was posted.", extra_tags="detail_only")

    return redirect("forum_detail", pk=question.pk)

@login_required
@require_POST
def post_reply(request, question_id, parent_id):
    question = get_object_or_404(ForumQuestion, pk=question_id)
    parent = get_object_or_404(ForumAnswer, pk=parent_id, question=question)

    # Visibility rules: if hidden, only staff or the hidden item's author can access
    if question.is_hidden and not (request.user.is_staff or request.user == question.author):
        raise Http404()

    if parent.is_hidden and not (request.user.is_staff or request.user == parent.author):
        raise Http404()

    form = ForumAnswerForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please fix the errors in your reply.", extra_tags="detail_only")
        return redirect(reverse("forum_detail", kwargs={"pk": question.pk}) + f"#a{parent.id}")

    content = _clean_forum_text(form.cleaned_data.get("content"), max_len=5000)
    if not content:
        messages.error(request, "Reply cannot be empty.", extra_tags="detail_only")
        return redirect(reverse("forum_detail", kwargs={"pk": question.pk}) + f"#a{parent.id}")

    reply = form.save(commit=False)
    reply.content = content
    reply.author = request.user
    reply.question = question
    reply.parent = parent

    decision, err = moderate_text(content)

    # Hard block
    if decision and decision.action == "block":
        messages.error(
            request,
            "Your reply wasn’t published because it violates our community guidelines. "
            "Please rewrite and try again.",
            extra_tags="detail_only",
        )
        return redirect(reverse("forum_detail", kwargs={"pk": question.pk}) + f"#a{parent.id}")

    # Fail-soft moderation error → pending review
    if err and not request.user.is_staff:
        reply.is_hidden = True
        reply.moderation_status = "pending_review"
        reply.moderation_details = {"kind": "reply", "error": err}

    apply_decision_to_instance(reply, decision, kind="reply")
    reply.save()

    # ✅ message based on final hidden state (NO staff exception)
    if reply.is_hidden:
        messages.success(request, "Reply submitted — pending review.", extra_tags="detail_only")
    else:
        messages.success(request, "Your reply was posted.", extra_tags="detail_only")

    return redirect(reverse("forum_detail", kwargs={"pk": question.pk}) + f"#a{parent.id}")

@login_required
@require_POST
def toggle_question_upvote(request, pk: int):
    question = get_object_or_404(ForumQuestion, pk=pk)
    already = question.upvotes.filter(pk=request.user.pk).exists()

    if already:
        question.upvotes.remove(request.user)
        state = "removed"
    else:
        question.upvotes.add(request.user)
        state = "added"

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "state": state, "count": question.upvotes.count()})
    return redirect("forum_detail", pk=pk)


@login_required
@require_POST
def toggle_answer_upvote(request, pk: int):
    ans = get_object_or_404(ForumAnswer, pk=pk)
    already = ans.upvotes.filter(pk=request.user.pk).exists()

    if already:
        ans.upvotes.remove(request.user)
        state = "removed"
    else:
        ans.upvotes.add(request.user)
        state = "added"

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "state": state, "count": ans.upvotes.count()})
    return redirect("forum_detail", pk=ans.question_id)


# ------------------- Moderation Queue -------------------

@staff_member_required
@login_required
@require_GET
def forum_course_chapters(request, course_id):
    """Return chapters for a given course as JSON (used by dependent dropdowns)."""
    # Always return JSON so the frontend doesn't crash when parsing.
    if not Course.objects.filter(id=course_id).exists():
        return JsonResponse({"ok": False, "chapters": []}, status=404)

    chapters = (
        Chapter.objects
        .filter(course_id=course_id)
        .order_by("chapter_number", "chapter_name")
        .values("id", "chapter_number", "chapter_name")
    )
    return JsonResponse({"ok": True, "chapters": list(chapters)})

def forum_moderation_queue(request):
    if not request.user.is_staff:
        raise Http404()
    
    q_pending = ForumQuestion.objects.filter(moderation_status="pending_review").order_by("-created_at")[:200]
    a_pending = ForumAnswer.objects.filter(moderation_status="pending_review").select_related("question").order_by("-created_at")[:200]
    return render(request, "forum/moderation_queue.html", {
        "q_pending": q_pending,
        "a_pending": a_pending,
    })


@staff_member_required
@login_required
@require_POST
def forum_moderation_action(request):
    if not request.user.is_staff:
        raise Http404()
    kind = request.POST.get("kind")  # "question" or "answer"
    obj_id = request.POST.get("id")
    action = request.POST.get("action")  # "approve" | "reject" | "keep_hidden"

    if kind not in {"question", "answer"} or action not in {"approve", "reject", "keep_hidden"}:
        messages.error(request, "Invalid moderation request.")
        return redirect("forum_moderation_queue")

    Model = ForumQuestion if kind == "question" else ForumAnswer
    obj = get_object_or_404(Model, pk=obj_id)

    # apply manual decision
    if action == "approve":
        obj.is_hidden = False
        obj.moderation_status = "approved"
    elif action == "reject":
        obj.is_hidden = True
        obj.moderation_status = "rejected"
    else:  # keep_hidden
        obj.is_hidden = True
        obj.moderation_status = "pending_review"

    details = obj.moderation_details or {}
    details["manual"] = {
        "by": request.user.username,
        "action": action,
        "at": timezone.now().isoformat(),
    }
    obj.moderation_details = details
    obj.save(update_fields=["is_hidden", "moderation_status", "moderation_details"])

    messages.success(request, f"{kind.title()} updated.")
    return redirect("forum_moderation_queue")


# ------------------- DMs (your existing code) -------------------
User = get_user_model()
MODERATOR_USERNAME = "moderator"


def _is_moderator_user(u) -> bool:
    return bool(u) and getattr(u, "username", "") == MODERATOR_USERNAME


@login_required
def dm_inbox(request):
    last_msg_qs = DmMessage.objects.filter(thread=OuterRef("pk")).order_by("-created_at")

    threads = (
        DmThread.objects
        .filter(Q(user_a=request.user) | Q(user_b=request.user))
        .select_related("user_a", "user_b")
        .annotate(
            unread_count=Count(
                "messages",
                filter=Q(messages__is_read=False) & ~Q(messages__sender=request.user),
                distinct=True,
            ),
            last_text=Subquery(last_msg_qs.values("content")[:1]),
            last_at=Subquery(last_msg_qs.values("created_at")[:1]),
        )
        .order_by("-last_at")
    )

    thread_data = []
    for t in threads:
        other = t.user_b if t.user_a == request.user else t.user_a
        last_text = (t.last_text or "").strip()
        if len(last_text) > 70:
            last_text = last_text[:70] + "…"

        thread_data.append({
            "id": t.id,
            "other": other,
            "started_at": t.started_at,
            "last_at": t.last_at or t.started_at,
            "last_text": last_text,
            "unread_count": int(t.unread_count or 0),
        })

    return render(request, "forum/dm_inbox.html", {"threads": thread_data})


@login_required
def dm_thread(request, user_id: int):
    other = get_object_or_404(User, pk=user_id)
    if other == request.user:
        raise Http404()

    a, b = (request.user, other) if request.user.id < other.id else (other, request.user)

    try:
        with transaction.atomic():
            thread, _ = DmThread.objects.get_or_create(user_a=a, user_b=b)
    except IntegrityError:
        thread = DmThread.objects.get(user_a=a, user_b=b)

    msgs = thread.messages.select_related("sender").order_by("created_at")

    # mark incoming as read on GET
    if request.method == "GET":
        thread.messages.filter(sender=other, is_read=False).update(
            is_read=True,
            read_at=timezone.now()
        )

    last_msg = msgs.last()
    last_id = last_msg.id if last_msg else 0

    # ✅ Block replying to moderator ONLY on POST
    if request.method == "POST":
        if _is_moderator_user(other):
            messages.error(request, "This is a system-generated conversation. Replies are disabled.")
            return redirect("dm_thread", user_id=other.id)

        body = (request.POST.get("content") or "").strip()
        if body:
            m = DmMessage.objects.create(thread=thread, sender=request.user, content=body)
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({
                    "ok": True,
                    "message": {
                        "id": m.id,
                        "content": m.content,
                        "sender_id": request.user.id,
                        "created_at": timezone.localtime(m.created_at).strftime("%b %d, %H:%M"),
                    }
                })
        return redirect("dm_thread", user_id=other.id)

    return render(request, "forum/dm_thread.html", {
        "thread": thread,
        "other": other,
        "messages": msgs,
        "last_id": last_id,
        "can_reply": (not _is_moderator_user(other)),
        "is_system_thread": _is_moderator_user(other),
    })


@login_required
def dm_thread_updates(request, user_id: int):
    other = get_object_or_404(User, pk=user_id)
    if other == request.user:
        raise Http404()

    a, b = (request.user, other) if request.user.id < other.id else (other, request.user)
    thread = get_object_or_404(DmThread, user_a=a, user_b=b)

    after_id = int(request.GET.get("after_id", 0))

    new_msgs = (
        thread.messages
        .select_related("sender")
        .filter(id__gt=after_id)
        .order_by("created_at")
    )

    # mark incoming as read
    new_msgs.filter(sender=other, is_read=False).update(is_read=True, read_at=timezone.now())

    data = [{
        "id": m.id,
        "content": m.content,
        "sender_id": m.sender_id,
        "created_at": timezone.localtime(m.created_at).strftime("%b %d, %H:%M"),
    } for m in new_msgs]

    return JsonResponse({"ok": True, "messages": data})


@require_GET
@login_required
def dm_inbox_updates(request):
    last_msg_qs = DmMessage.objects.filter(thread=OuterRef("pk")).order_by("-created_at")

    threads = (
        DmThread.objects
        .filter(Q(user_a=request.user) | Q(user_b=request.user))
        .annotate(
            unread_count=Count(
                "messages",
                filter=Q(messages__is_read=False) & ~Q(messages__sender=request.user),
                distinct=True,
            ),
            last_text=Subquery(last_msg_qs.values("content")[:1]),
            last_at=Subquery(last_msg_qs.values("created_at")[:1]),
        )
    )

    payload = []
    for t in threads:
        other = t.user_b if t.user_a_id == request.user.id else t.user_a
        last_text = (t.last_text or "").strip()
        if len(last_text) > 70:
            last_text = last_text[:70] + "…"
        last_at = t.last_at or t.started_at

        payload.append({
            "other_id": other.id,
            "unread_count": int(t.unread_count or 0),
            "last_text": last_text,
            "last_at_display": timezone.localtime(last_at).strftime("%b %d, %H:%M"),
        })

    return JsonResponse({"ok": True, "threads": payload})

@login_required
@require_http_methods(["GET", "POST"])
def forum_question_edit(request, pk):
    question = get_object_or_404(ForumQuestion, pk=pk)

    # only author or staff
    if not (request.user.is_staff or request.user.id == question.author_id):
        messages.error(request, "You can only edit your own post.")
        return redirect("forum_detail", pk=question.id)

    if request.method == "POST":
        form = ForumQuestionForm(request.POST, instance=question)
        if form.is_valid():
            title = (form.cleaned_data.get("title") or "").strip()
            content = (form.cleaned_data.get("content") or "").strip()

            decision, err = moderate_text(f"{title}\n\n{content}")

            # If BLOCK: don't save
            if decision and getattr(decision, "action", "") == "block":
                messages.error(request, "This edit violates the forum guidelines. Please revise and try again.")
                return render(request, "forum/edit_question.html", {"form": form, "question": question})

            obj = form.save(commit=False)

            # Apply moderation decision fields (toxicity, categories, etc.)
            if decision:
                apply_decision_to_instance(obj, decision, kind="question")

            needs_review = bool(err) or (decision and getattr(decision, "action", "") in ("review", "hide"))

            # Staff edits stay visible; non-staff can be sent to review
            if needs_review and not request.user.is_staff:
                obj.is_hidden = True
                obj.moderation_status = "pending_review"
            else:
                obj.is_hidden = False
                obj.moderation_status = "approved"

            obj.save()
            form.save_m2m()

            messages.success(request, "Post updated.")
            return redirect("forum_detail", pk=obj.id)
    else:
        form = ForumQuestionForm(instance=question)

    return render(request, "forum/edit_question.html", {"form": form, "question": question})


@login_required
@require_http_methods(["GET", "POST"])
def forum_question_delete(request, pk):
    question = get_object_or_404(ForumQuestion, pk=pk)

    # only author or staff
    if not (request.user.is_staff or request.user.id == question.author_id):
        messages.error(request, "You can only delete your own post.")
        return redirect("forum_detail", pk=question.id)

    if request.method == "POST":
        question.delete()
        messages.success(request, "Post deleted.")
        return redirect("forum_home")

    return render(request, "forum/confirm_delete_question.html", {"question": question})

