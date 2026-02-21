from django.shortcuts import render

def student_courses(request):
    return render(request, "student/student_courses.html")
