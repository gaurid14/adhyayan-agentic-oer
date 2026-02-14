# accounts/middleware/decision_autorun.py
from django.utils.deprecation import MiddlewareMixin
from django.core.cache import cache
import time, logging

logger = logging.getLogger(__name__)

class DecisionAutoRunMiddleware(MiddlewareMixin):
    THROTTLE_SECONDS = 30

    def process_request(self, request):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None

        # ✅ Allow staff OR contributor dashboard
        allowed = user.is_staff or request.path.startswith("/dashboard/contributor/")
        if not allowed:
            return None

        # throttle per-user (IMPORTANT: not global)
        key = f"dm:last_check_ts:{user.id}"
        last = cache.get(key)
        now = time.time()
        if last and (now - float(last)) < self.THROTTLE_SECONDS:
            return None
        cache.set(key, now, timeout=self.THROTTLE_SECONDS)

        logger.info("DM middleware HIT path=%s user=%s staff=%s", request.path, user.username, user.is_staff)

        try:
            from accounts.services.auto_decision import trigger_due_decisions
            trigger_due_decisions(max_chapters=2)
        except Exception:
            logger.exception("DM middleware failed")

        return None
