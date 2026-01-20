# utils/email_service.py

from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags


class BaseEmailService:
    template_name = None
    subject = None

    def __init__(self, to_email, context):
        self.to_email = to_email
        self.context = context

    def send(self):
        if not self.template_name or not self.subject:
            raise NotImplementedError("Email template or subject missing")

        html_message = render_to_string(self.template_name, self.context)
        plain_message = strip_tags(html_message)

        send_mail(
            self.subject,
            plain_message,
            None,
            [self.to_email],
            html_message=html_message
        )

class RegistrationSuccessEmail(BaseEmailService):
    template_name = "emails/registration_success.html"
    subject = "🎓 Welcome to Adhyayan!!!"

    def __init__(self, to_email, user_name):
        super().__init__(to_email, {
            "name": user_name
        })

class ContributionSuccessEmail(BaseEmailService):
    template_name = "emails/contribution_success.html"

    def __init__(self, to_email, contributor_name, subject_name, chapter_title):
        self.subject = f"🎉 Contribution Received for {subject_name} - {chapter_title}"
        super().__init__(to_email, {
            "name": contributor_name,
            "subject_name": subject_name,
            "chapter": chapter_title,
        })

class EvaluationResultEmail(BaseEmailService):
    template_name = "emails/evaluation_result.html"

    def __init__(self, to_email, contributor_name, status, remarks=None):
        subject_map = {
            "APPROVED": "✅ Your content has been approved!",
            "REJECTED": "❌ Content requires revision",
            "PENDING": "⏳ Content under evaluation",
        }

        self.subject = subject_map.get(status, "📋 Content Evaluation Update")

        super().__init__(to_email, {
            "name": contributor_name,
            "status": status,
            "remarks": remarks
        })
