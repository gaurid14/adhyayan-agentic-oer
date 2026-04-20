from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages

from accounts.forms import StudentProfileForm
from accounts.models import EnrolledCourse, CourseCompletion, StudentChapterProgress


@login_required
def student_profile(request):
    user = request.user

    # ✅ SAFE BASIC STATS (no datetime usage)
    total_enrolled = EnrolledCourse.objects.filter(student=user).count()
    completed_courses = CourseCompletion.objects.filter(student=user).count()
    chapters_completed = StudentChapterProgress.objects.filter(
        student=user,
        completed=True
    ).count()

    stats = {
        "total_enrolled": total_enrolled,
        "completed_courses": completed_courses,
        "chapters_completed": chapters_completed,
    }

    if request.method == "POST":
        form = StudentProfileForm(request.POST, request.FILES, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully.")
            return redirect("student_profile")
    else:
        form = StudentProfileForm(instance=user)

    return render(request, "student/student_profile.html", {
        "form": form,
        "stats": stats,
    })