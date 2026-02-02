from django.contrib import admin
from django.utils import timezone
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    User,
    Course,
    Chapter,
    ChapterPolicy,
    ChapterDeadlineExtension,
    ForumTopic,
    ForumQuestion,
    ForumAnswer,
    Program,
    Department,
    Scheme,
    Expertise,
    CourseObjective,
    CourseOutcome,
    OutcomeChapterMapping,
    UploadCheck,
    ContentCheck,
    ContentScore,
    ReleasedContent,
    EnrolledCourse,
    Assessment,
    Question,
    Option,
)
from .views.email.email_service import AccountApprovedEmail


@admin.register(ForumTopic)
class ForumTopicAdmin(admin.ModelAdmin):
    list_display = ("name",)

@admin.register(ForumQuestion)
class ForumQuestionAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "created_at")
    search_fields = ("title", "content")
    list_filter = ("created_at", "topics")

@admin.register(ForumAnswer)
class ForumAnswerAdmin(admin.ModelAdmin):
    list_display = ("question", "author", "created_at", "parent")
    search_fields = ("content",)
    list_filter = ("created_at",)


@admin.action(description="Approve selected contributors")
def approve_contributors(modeladmin, request, queryset):
    qs = queryset.filter(role=User.Role.CONTRIBUTOR)
    qs.update(
        contributor_approval_status=User.ContributorApprovalStatus.APPROVED,
        contributor_approved_at=timezone.now(),
        contributor_rejected_at=None,
        contributor_rejection_reason="",
        is_active=True,
    )
    for user in qs:
        AccountApprovedEmail(user.email, user.first_name).send()

@admin.action(description="Reject selected contributors")
def reject_contributors(modeladmin, request, queryset):
    qs = queryset.filter(role=User.Role.CONTRIBUTOR)
    qs.update(
        contributor_approval_status=User.ContributorApprovalStatus.REJECTED,
        contributor_rejected_at=timezone.now(),
        is_active=False,
    )


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "role", "contributor_approval_status", "is_active")
    list_filter = ("role", "contributor_approval_status", "is_active")
    actions = [approve_contributors, reject_contributors]

    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Contributor Approval", {
            "fields": (
                "contributor_approval_status",
                "contributor_approved_at",
                "contributor_rejected_at",
                "contributor_rejection_reason",
            )
        }),
    )

    readonly_fields = ("contributor_approved_at", "contributor_rejected_at")

@admin.action(description="Create missing ChapterPolicy for selected chapters")
def create_missing_chapter_policies(modeladmin, request, queryset):
    for chapter in queryset:
        ChapterPolicy.objects.get_or_create(chapter=chapter)



@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
    list_display = ("course", "chapter_number", "chapter_name")
    list_filter = ("course__department", "course__semester", "course__scheme")
    search_fields = ("chapter_name", "course__course_name")
    actions = [create_missing_chapter_policies]


class ChapterDeadlineExtensionInline(admin.TabularInline):
    model = ChapterDeadlineExtension
    extra = 0
    readonly_fields = ("extended_at", "old_deadline", "new_deadline")


@admin.register(ChapterPolicy)
class ChapterPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "chapter",
        "current_deadline",
        "extensions_used",
        "max_extensions",
        "min_contributions",
    )
    list_filter = ("chapter__course__department", "chapter__course__semester")
    search_fields = ("chapter__chapter_name", "chapter__course__course_name")
    inlines = [ChapterDeadlineExtensionInline]


admin.site.register(Program)
admin.site.register(Department)
admin.site.register(Scheme)
admin.site.register(Expertise)
admin.site.register(Course)
admin.site.register(CourseObjective)
admin.site.register(CourseOutcome)
admin.site.register(OutcomeChapterMapping)

admin.site.register(UploadCheck)
admin.site.register(ContentCheck)
admin.site.register(ContentScore)
admin.site.register(ReleasedContent)

admin.site.register(EnrolledCourse)

admin.site.register(Assessment)
admin.site.register(Question)
admin.site.register(Option)

