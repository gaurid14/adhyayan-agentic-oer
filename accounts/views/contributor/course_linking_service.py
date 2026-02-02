import re
from django.db.models import Q
from accounts.models import Expertise, Course


STOP_WORDS = {"and", "or", "the", "of", "to", "in", "for"}


def tokenize(text: str):
    text = (text or "").lower().strip()
    words = re.split(r"[,\s;/\-]+", text)
    words = [w for w in words if w and w not in STOP_WORDS and len(w) >= 3]
    return list(set(words))  # unique


def auto_link_expertise_to_courses(expertise_obj: Expertise):
    """
    fills accounts_expertise_courses automatically
    """

    keywords = tokenize(expertise_obj.name)
    if not keywords:
        return []

    q = Q()
    for word in keywords:
        q |= Q(course_name__icontains=word)
        q |= Q(course_code__icontains=word)

    matched_courses = Course.objects.filter(q).distinct()

    # attach in expertise_courses table
    if matched_courses.exists():
        expertise_obj.courses.add(*matched_courses)

    return list(matched_courses)
