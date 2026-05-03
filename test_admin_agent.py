import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "OER.settings")
django.setup()

from accounts.models import Chapter, Course, UploadCheck, ContentScore, ReleasedContent

chapter = Chapter.objects.get(id=16)
course = chapter.course
print(f"Course: {course.course_name} (ID: {course.id})")
chapters = list(Chapter.objects.filter(course=course).order_by("chapter_number"))
total = len(chapters)
print(f"Total chapters: {total}")

complete_flags = []
for ch in chapters:
    has_best = UploadCheck.objects.filter(chapter=ch, content_score__is_best=True).exists()
    complete_flags.append(has_best)
    print(f"  - Chapter {ch.chapter_number}: {ch.chapter_name} -> is_complete={has_best}")

completed_count = sum(1 for v in complete_flags if v)

threshold = 80
try:
    policy = getattr(course, "release_policy", None)
    if policy and policy.threshold_percentage is not None:
        threshold = int(policy.threshold_percentage)
except Exception:
    pass

import math
required = max(1, math.floor((threshold * total) / 100))
print(f"Completed: {completed_count}, Required: {required} (Threshold: {threshold}%)")

if completed_count < required:
    print("=> SKIPPED THRESHOLD! Admin agent will NOT release anything.")
else:
    print("=> Threshold met.")
    prefix_len = 0
    for flag in complete_flags:
        if flag:
            prefix_len += 1
        else:
            break
    print(f"=> Sequential prefix length: {prefix_len}")
    print(f"   Chapter 16 allowed? {chapters.index(chapter) < prefix_len}")

