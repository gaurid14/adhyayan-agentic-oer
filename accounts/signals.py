from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import ContentScore, DecisionRun
from accounts.services.decision_maker import DecisionMakerService
# from accounts.services.admin_agent import run_admin_release_for_course


@receiver(post_save, sender=ContentScore)
def auto_run_decision_maker(sender, instance, created, **kwargs):
    if created:
        DecisionMakerService().decide_for_chapter(
            chapter_id=instance.upload.chapter.id,
            force=True
        )


# signals.py

@receiver(post_save, sender=DecisionRun)
def auto_run_admin_agent(sender, instance, created, **kwargs):
    if created:
        # 1. Import inside the function to avoid circular imports
        from accounts.services.admin_agent import AdminAgentService
        
        course = instance.chapter.course
        
        # 2. Use the new class method
        service = AdminAgentService()
        service.run_for_course(course)