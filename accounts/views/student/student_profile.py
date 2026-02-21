from django.shortcuts import render

def student_profile(request):
    return render(request, "student/student_profile.html")
