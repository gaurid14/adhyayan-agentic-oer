from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def chapter_topics(request, course_id):
    return render(
        request,
        "student/chapter_topics.html",
        {
            "course_id": course_id
        }
    )
