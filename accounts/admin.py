from django.contrib import admin
from django.utils import timezone
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User, ForumTopic, ForumQuestion, ForumAnswer

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
