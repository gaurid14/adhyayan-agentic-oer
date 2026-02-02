from accounts.models import Course
from accounts.views.contributor.course_linking_service import auto_link_expertise_to_courses
from accounts.views.contributor.expertise_service import save_user_expertise


def save_expertise_and_generate_course_links(user, raw_expertise: str):
    """
    contributor enters expertise
    it stores expertise + user mapping
    then auto-links those expertise to courses
    """
    names = save_user_expertise(user, raw_expertise)

    for exp in user.domain_of_expertise.all():
        auto_link_expertise_to_courses(exp)

    return names


def recommend_courses_for_contributor(user):
    """
    will work ONLY after expertise->courses mapping exists
    """
    return Course.objects.filter(
        expertises__in=user.domain_of_expertise.all()
    ).distinct()
