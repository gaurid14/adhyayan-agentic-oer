# accounts/views/forum.py
from concurrent.futures import thread
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import (
    Count, Q, Prefetch, F, IntegerField, ExpressionWrapper,
    OuterRef, Subquery
)
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone
from datetime import timedelta
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.urls import reverse

from ..models import (
    Chapter, Course, ForumQuestion, ForumAnswer, ForumTopic,
    DmThread, DmMessage, User
)
from ..forms import ForumQuestionForm, ForumAnswerForm, ForumTopicForm
from django.views.decorators.http import require_GET
from django.db.models import OuterRef, Subquery, Count, Q
from django.db.models import Count
from django.shortcuts import get_object_or_404, render

def _clean_forum_text(text: str, max_len: int = 10000) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    # prevent accidental template-tag-like storage (optional safety)
    if "{%" in text:
        return ""
    return text[:max_len]


def _clean_forum_text(text: str, max_len: int = 10000) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    # prevent accidental template-tag-like storage (optional safety)
    if "{%" in text:
        return ""
    return text[:max_len]


def forum_home(request):
    q = request.GET.get("q", "").strip()
    topic_id = request.GET.get("topic")
    sort = request.GET.get("sort", "new").strip()
    page = request.GET.get("page", 1)
    course_id = request.GET.get("course")
    chapter_id = request.GET.get("chapter")

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

    if q:
        questions = questions.filter(Q(title__icontains=q) | Q(content__icontains=q))

    if topic_id:
        questions = questions.filter(topics__id=topic_id).distinct()

    if course_id:
        questions = questions.filter(course_id=course_id)

    if chapter_id:
        questions = questions.filter(chapter_id=chapter_id)

    # Sorting (safe, no schema change)
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
    trending = (
        base_qs
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
    if course_id:
        chapters = Chapter.objects.filter(course_id=course_id).order_by("chapter_number", "chapter_name")

    context = {
        "questions": page_obj.object_list,
        "page_obj": page_obj,
        "topics": topics,
        "selected_topic": int(topic_id) if topic_id else None,
        "q": q,
        "sort": sort,
        "q_form": ForumQuestionForm(),
        "topic_form": ForumTopicForm(),
        "trending": trending,
        "my_discussions": my_discussions,
        "top_users": top_users,
        "courses": courses,
        "chapters": chapters,
        "selected_course": int(course_id) if course_id else None,
        "selected_chapter": int(chapter_id) if chapter_id else None,
    }

    return render(request, "forum/list.html", context)


def _top_level_answers_qs():
    return (
        ForumAnswer.objects
        .filter(parent__isnull=True)
        .select_related("author")
        .annotate(upvote_count=Count("upvotes", distinct=True))
        .order_by("-created_at")
    )


def forum_detail(request, pk: int):
    question = (
        ForumQuestion.objects
        .select_related("author", "course", "chapter")
        .prefetch_related("topics")
        .annotate(upvote_count=Count("upvotes", distinct=True))
        .get(pk=pk)
    )

    answers = (
        ForumAnswer.objects
        .filter(question=question)
        .select_related("author")
        .annotate(upvote_count=Count("upvotes", distinct=True))
        .order_by("created_at")
    )

    # parent_id -> [children...]
    by_parent = {}
    for a in answers:
        by_parent.setdefault(a.parent_id, []).append(a)

    top_answers = by_parent.get(None, [])

    # Build groups: each top answer + a flat list of replies with nesting level
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

    return render(request, "forum/detail.html", {"question": question, "a_form": ForumAnswerForm()})


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
        messages.error(request, "Title/content cannot be empty (or contain invalid template tags).")
        return redirect("forum_home")

    qobj = form.save(commit=False)
    course = form.cleaned_data.get("course")
    chapter = form.cleaned_data.get("chapter")

    # Require course (makes the feature meaningful)
    if not course:
        messages.error(request, "Please select a course.")
        return redirect("forum_home")

    # If chapter selected, ensure it belongs to selected course
    if chapter and chapter.course_id != course.id:
        messages.error(request, "Selected chapter does not belong to that course.")
        return redirect("forum_home")

    qobj.course = course
    qobj.chapter = chapter
    qobj.author = request.user
    qobj.title = title
    qobj.content = content

    qobj.save()
    form.save_m2m()

    messages.success(request, "Your question was posted.")
    return redirect("forum_detail", pk=qobj.pk)


@login_required
@require_POST
def post_answer(request, question_id):
    question = get_object_or_404(ForumQuestion, pk=question_id)
    form = ForumAnswerForm(request.POST)
    if form.is_valid():
        content = _clean_forum_text(form.cleaned_data.get("content"))
        if not content:
            messages.error(request, "Invalid content.")
            return redirect("forum_detail", pk=question.pk)

        ans = form.save(commit=False)
        ans.content = content
        ans.author = request.user
        ans.question = question
        ans.parent = None
        ans.save()

    return redirect("forum_detail", pk=question.pk)


@login_required
@require_POST
def post_reply(request, question_id, parent_id):
    question = get_object_or_404(ForumQuestion, pk=question_id)
    parent = get_object_or_404(ForumAnswer, pk=parent_id, question=question)

    MAX_DEPTH = 10

    def _depth(ans: ForumAnswer) -> int:
        d = 0
        cur_id = ans.parent_id
        while cur_id is not None and d < 50:
            d += 1
            cur_id = ForumAnswer.objects.only("id", "parent_id").get(id=cur_id).parent_id
        return d

    if _depth(parent) >= MAX_DEPTH:
        messages.error(request, f"Reply limit reached (max depth {MAX_DEPTH}).")
        return redirect(reverse("forum_detail", kwargs={"pk": question.pk}) + f"#a{parent.id}")

    form = ForumAnswerForm(request.POST)
    if form.is_valid():
        content = _clean_forum_text(form.cleaned_data.get("content"), max_len=5000)
        if not content:
            messages.error(request, "Invalid content.")
            return redirect("forum_detail", pk=question.pk)

        reply = form.save(commit=False)
        reply.content = content
        reply.author = request.user
        reply.question = question
        reply.parent = parent
        reply.save()

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


@login_required
def dm_inbox(request):
    last_msg_qs = (
        DmMessage.objects
        .filter(thread=OuterRef("pk"))
        .order_by("-created_at")
    )

    threads = (
        DmThread.objects
        .filter(Q(user_a=request.user) | Q(user_b=request.user))
        .select_related("user_a", "user_b")
        .annotate(
            unread_count=Count(
                "messages",
                filter=Q(messages__is_read=False) & ~Q(messages__sender=request.user),
                distinct=True
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
            "unread_count": t.unread_count,
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

    if request.method == "GET":
        thread.messages.filter(sender=other, is_read=False).update(is_read=True, read_at=timezone.now())

    last_msg = msgs.last()
    last_id = last_msg.id if last_msg else 0

    if request.method == "POST":
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
                        "created_at": m.created_at.strftime("%b %d, %H:%M"),
                    }
                })
        return redirect("dm_thread", user_id=other.id)

    return render(request, "forum/dm_thread.html", {
        "thread": thread,
        "other": other,
        "messages": msgs,
        "last_id": last_id
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

    new_msgs.filter(sender=other, is_read=False).update(is_read=True, read_at=timezone.now())

    data = [{
        "id": m.id,
        "content": m.content,
        "sender_id": m.sender_id,
        "created_at": m.created_at.strftime("%b %d, %H:%M"),
    } for m in new_msgs]

    return JsonResponse({"ok": True, "messages": data})


@require_GET
@login_required
def dm_inbox_updates(request):
    """
    Returns unread counts + last message meta for inbox rows (AJAX).
    """
    last_msg_qs = (
        DmMessage.objects
        .filter(thread=OuterRef("pk"))
        .order_by("-created_at")
    )

    threads = (
        DmThread.objects
        .filter(Q(user_a=request.user) | Q(user_b=request.user))
        .annotate(
            unread_count=Count(
                "messages",
                filter=Q(messages__is_read=False) & ~Q(messages__sender=request.user),
                distinct=True
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
        last_at_display = timezone.localtime(last_at).strftime("%b %d, %H:%M")

        payload.append({
            "other_id": other.id,
            "unread_count": int(t.unread_count or 0),
            "last_text": last_text,
            "last_at_display": last_at_display,
        })

    return JsonResponse({"ok": True, "threads": payload})


@require_GET
def forum_course_chapters(request, course_id: int):
    """
    Public endpoint used by forum.js to populate chapters dropdown.
    Keeping it public prevents auth-redirect HTML being returned to fetch().
    """
    chapters = (
        Chapter.objects
        .filter(course_id=course_id)
        .order_by("chapter_number", "chapter_name")
        .values("id", "chapter_number", "chapter_name")
    )
    return JsonResponse({"ok": True, "chapters": list(chapters)})