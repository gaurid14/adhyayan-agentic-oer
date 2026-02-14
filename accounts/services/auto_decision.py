# accounts/services/auto_decision.py
from __future__ import annotations
from django.db import transaction
from django.utils import timezone
import logging

from accounts.models import ChapterPolicy, UploadCheck
try:
    from accounts.models import DecisionRun
except Exception:
    DecisionRun = None

from accounts.services.decision_maker import DecisionMakerService

logger = logging.getLogger(__name__)

def trigger_decision_if_due(chapter_id: int):
    policy = ChapterPolicy.objects.filter(chapter_id=chapter_id).first()
    if not policy:
        logger.info("[DM] chapter=%s no policy", chapter_id)
        return None

    if policy.is_open:
        logger.info("[DM] chapter=%s not due yet (deadline=%s)", chapter_id, policy.current_deadline)
        return None

    evaluated_count = UploadCheck.objects.filter(
        chapter_id=chapter_id,
        evaluation_status=True,
    ).count()

    min_req = int(policy.min_contributions or 0)
    if evaluated_count < min_req:
        logger.info("[DM] chapter=%s not enough evaluated uploads (%s/%s)", chapter_id, evaluated_count, min_req)
        return None

    if DecisionRun is not None:
        latest = DecisionRun.objects.filter(chapter_id=chapter_id, is_latest=True).first()
        if latest and latest.status == "ok" and latest.selected_upload_id:
            logger.info("[DM] chapter=%s already decided selected=%s (skip)", chapter_id, latest.selected_upload_id)
            return None


    logger.info("[DM] chapter=%s RUNNING decision maker...", chapter_id)
    service = DecisionMakerService()

    with transaction.atomic():
        run = service.decide_for_chapter(
            chapter_id=chapter_id,
            force=True,
            only_evaluated_uploads=True,
            min_contributions_policy="respect",
            auto_release=False,
            persist=True,
            top_k_audit=5,
        )

    logger.info("[DM] chapter=%s decision done result=%s", chapter_id, getattr(run, "status", run))
    return run

def trigger_due_decisions(*, max_chapters: int = 2):
    now = timezone.now()
    due_policies = (
        ChapterPolicy.objects
        .filter(current_deadline__isnull=False, current_deadline__lt=now)
        .order_by("current_deadline")
    )

    logger.info("[DM] due policies found=%s (now=%s)", due_policies.count(), now)

    ran = 0
    for p in due_policies:
        res = trigger_decision_if_due(p.chapter_id)
        if res is not None:
            ran += 1
            if ran >= max_chapters:
                break

    logger.info("[DM] trigger_due_decisions finished ran=%s", ran)
