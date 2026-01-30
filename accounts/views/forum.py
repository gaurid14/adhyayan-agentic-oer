# accounts/views/forum.py
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q, Prefetch, F, IntegerField, ExpressionWrapper
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone
from datetime import timedelta

from ..models import (
    ForumQuestion, ForumAnswer, ForumTopic,
    DmThread, DmMessage, User
)
from ..forms import ForumQuestionForm, ForumAnswerForm, ForumTopicForm

def _clean_forum_text(text: str) -> str:
    text = (text or "").strip()
    # Block accidental Django template syntax being stored in DB
    if "{%" in text or "{{" in text:
        return ""
    return text


def _top_level_answers_qs():
    return ForumAnswer.objects.filter(parent__isnull=True).select_related("author")


# ---------- LIST (All discussions) + sidebar data ----------
def forum_home(request):
    q = request.GET.get("q", "").strip()
    topic_id = request.GET.get("topic")

    base_qs = (
        ForumQuestion.objects
        .select_related("author")
        .prefetch_related("topics")
        .annotate(answer_count=Count("answers"))
    )

    questions = base_qs.order_by("-created_at")
    if q:
        questions = questions.filter(Q(title__icontains=q) | Q(content__icontains=q))
    if topic_id:
        questions = questions.filter(topics__id=topic_id)

    topics = ForumTopic.objects.annotate(num_questions=Count("questions")).order_by("-num_questions", "name")

    # ---------- Trending (simple, fast heuristic) ----------
    # score = 2*upvotes + answers in last 7 days; order by score then recency
    window = timezone.now() - timedelta(days=7)
    trending = (
        base_qs
        .annotate(
            q_up=Count("upvotes", distinct=True),
            recent_ans=Count("answers", filter=Q(answers__created_at__gte=window), distinct=True),
        )
        .annotate(score=ExpressionWrapper(F("q_up")*2 + F("recent_ans"), output_field=IntegerField()))
        .order_by("-score", "-created_at")[:5]
    )

    # ---------- My discussions ----------
    my_discussions = None
    if request.user.is_authenticated:
        my_discussions = base_qs.filter(author=request.user).order_by("-created_at")[:5]

    # ---------- Top users (by total upvotes on Q + A) ----------
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

    context = {
        "questions": questions,
        "topics": topics,
        "selected_topic": int(topic_id) if topic_id else None,
        "q": q,
        "q_form": ForumQuestionForm(),
        "topic_form": ForumTopicForm(),
        "trending": trending,
        "my_discussions": my_discussions,
        "top_users": top_users,
    }
    return render(request, "forum/list.html", context)


# ---------- DETAIL ----------
def forum_detail(request, pk: int):
    question = (
        ForumQuestion.objects
        .select_related("author")
        .prefetch_related(
            "topics",
            Prefetch("answers", queryset=_top_level_answers_qs(), to_attr="top_answers"),
            "answers__child_comments__author",
        )
        .get(pk=pk)
    )
    return render(request, "forum/detail.html", {"question": question, "a_form": ForumAnswerForm()})


# ---------- CREATE QUESTION ----------
@login_required
@require_POST
def post_question(request):
    form = ForumQuestionForm(request.POST)
    if form.is_valid():
        q = form.save(commit=False)
        q.author = request.user
        q.save()
        form.save_m2m()
        messages.success(request, "Your question was posted.")
        return redirect("forum_detail", pk=q.pk)
    messages.error(request, "Please fix the errors below.")
    return redirect("forum_home")


# ---------- ANSWER (top-level) ----------
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
    form = ForumAnswerForm(request.POST)
    if form.is_valid():
        content = _clean_forum_text(form.cleaned_data.get("content"))
        if not content:
            messages.error(request, "Invalid content.")
            return redirect("forum_detail", pk=question.pk)

        reply = form.save(commit=False)
        reply.content = content
        reply.author = request.user
        reply.question = question
        reply.parent = parent
        reply.save()
    return redirect("forum_detail", pk=question.pk)

# ---------- UPVOTES (toggle) ----------
@login_required
@require_POST
def toggle_question_upvote(request, pk: int):
    question = get_object_or_404(ForumQuestion, pk=pk)
    if request.user in question.upvotes.all():
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
    if request.user in ans.upvotes.all():
        ans.upvotes.remove(request.user)
        state = "removed"
    else:
        ans.upvotes.add(request.user)
        state = "added"

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "state": state, "count": ans.upvotes.count()})
    return redirect("forum_detail", pk=ans.question_id)


# ===================== Direct Messages (DM) =====================

@login_required
def dm_inbox(request):
    """List of DM threads for the current user."""
    threads = (
        DmThread.objects
        .filter(Q(user_a=request.user) | Q(user_b=request.user))
        .select_related("user_a", "user_b")
        .order_by("-started_at")
    )

    # Add computed field: the "other" user for template simplicity
    thread_data = []
    for t in threads:
        other = t.user_b if t.user_a == request.user else t.user_a
        thread_data.append({
            "id": t.id,
            "other": other,
            "started_at": t.started_at,
        })

    return render(request, "forum/dm_inbox.html", {"threads": thread_data})


@login_required
def dm_thread(request, user_id: int):
    """Open (or create) a DM thread with another user."""
    other = get_object_or_404(User, pk=user_id)
    if other == request.user:
        raise Http404()

    # order ids to match uniqueness
    a, b = (request.user, other) if request.user.id < other.id else (other, request.user)
    thread, _ = DmThread.objects.get_or_create(user_a=a, user_b=b)

    msgs = thread.messages.select_related("sender")
    if request.method == "POST":
        body = (request.POST.get("content") or "").strip()
        if body:
            DmMessage.objects.create(thread=thread, sender=request.user, content=body)
        return redirect("dm_thread", user_id=other.id)

    return render(request, "forum/dm_thread.html", {"thread": thread, "other": other, "messages": msgs})

