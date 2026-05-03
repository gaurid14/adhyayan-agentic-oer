from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import ContentScore, DecisionRun
from accounts.services.decision_maker import DecisionMakerService


@receiver(post_save, sender=ContentScore)
def auto_run_decision_maker(sender, instance, created, **kwargs):
    """Fire DecisionMaker when a ContentScore is created (new evaluation result)."""
    if created:
        DecisionMakerService().decide_for_chapter(
            chapter_id=instance.upload.chapter.id,
            force=True
        )


@receiver(post_save, sender=ContentScore)
def auto_mint_on_best_change(sender, instance, created, **kwargs):
    """
    When ContentScore.is_best is set to True (via admin or any save),
    immediately run the admin agent for that course so:
      1. Release statuses are recalculated.
      2. Certificates are minted for the best contributor.

    This fires on BOTH create and update so that manually toggling
    is_best in the Django admin correctly triggers the full pipeline.
    """
    if not instance.is_best:
        return  # only care about is_best=True

    try:
        from accounts.services.admin_agent import AdminAgentService
        course = instance.upload.chapter.course
        AdminAgentService().run_for_course(course)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "[Signal] auto_mint_on_best_change failed for ContentScore id=%s", instance.id
        )


@receiver(post_save, sender=DecisionRun)
def auto_run_admin_agent(sender, instance, created, **kwargs):
    """Run the admin release + cert pipeline whenever a DecisionRun is created."""
    if created:
        from accounts.services.admin_agent import AdminAgentService
        course = instance.chapter.course
        AdminAgentService().run_for_course(course)