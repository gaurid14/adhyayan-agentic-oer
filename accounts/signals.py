from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import ContentScore, DecisionRun
from accounts.services.decision_maker import DecisionMakerService

# Reentrancy guard: prevents signal → DecisionMaker → save → signal loops
_RUNNING_DM = set()   # chapter_ids currently being processed


@receiver(post_save, sender=ContentScore)
def auto_run_decision_maker(sender, instance, created, **kwargs):
    """Fire DecisionMaker ONLY when:
       1. A NEW ContentScore is created (not an admin edit)
       2. The chapter has a policy with a deadline
       3. That deadline has already passed (is_open == False)
    This prevents premature is_best marking while contributors are still uploading.
    """
    if not created:
        return

    try:
        from accounts.models import ChapterPolicy
        chapter_id = instance.upload.chapter_id

        # Guard against re-entrant calls (DA saves ContentScore → triggers this again)
        if chapter_id in _RUNNING_DM:
            return

        policy = ChapterPolicy.objects.filter(chapter_id=chapter_id).first()

        # No policy → no deadline → never auto-trigger
        if not policy:
            return

        # Chapter still accepting submissions → don't trigger DA
        if policy.is_open:
            return

        _RUNNING_DM.add(chapter_id)
        try:
            DecisionMakerService().decide_for_chapter(
                chapter_id=chapter_id,
                force=True,
            )
        finally:
            _RUNNING_DM.discard(chapter_id)

    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "[Signal] auto_run_decision_maker failed for ContentScore id=%s", instance.id
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