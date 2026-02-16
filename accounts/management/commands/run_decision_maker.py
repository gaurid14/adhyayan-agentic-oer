from __future__ import annotations

from typing import Any, Dict, Optional

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import ChapterPolicy
from accounts.services.decision_maker import DecisionMakerService


class Command(BaseCommand):
    help = "Run Decision Maker to select the best upload for chapters (typically after deadline)."

    def add_arguments(self, parser):
        parser.add_argument("--chapter-id", type=int, default=None, help="Run for a specific chapter id")
        parser.add_argument("--all-due", action="store_true", help="Run for all chapters whose deadline has passed")
        parser.add_argument("--force", action="store_true", help="Run even if deadline hasn't passed")
        parser.add_argument("--include-unevaluated", action="store_true", help="Also consider uploads not marked evaluated")
        parser.add_argument("--ignore-min-contrib", action="store_true", help="Ignore policy.min_contributions check")
        parser.add_argument("--auto-release", action="store_true", help="Auto-release winner (sets ReleasedContent.release_status)")
        parser.add_argument("--dry-run", action="store_true", help="Do not write DecisionRun/is_best/release flags")
        parser.add_argument("--top-k", type=int, default=5, help="How many top candidates to store/print")
        parser.add_argument("--quiet", action="store_true", help="Less verbose output")

    def handle(self, *args, **options):
        chapter_id = options["chapter_id"]
        all_due = options["all_due"]
        force = bool(options["force"])
        only_evaluated = not bool(options["include_unevaluated"])
        min_policy = "ignore" if bool(options["ignore_min_contrib"]) else "respect"
        auto_release = bool(options["auto_release"])
        persist = not bool(options["dry_run"])
        top_k = int(options["top_k"])
        quiet = bool(options["quiet"])

        if not chapter_id and not all_due:
            raise CommandError("Provide --chapter-id=<id> OR --all-due")

        svc = DecisionMakerService()

        if chapter_id:
            run = svc.decide_for_chapter(
                chapter_id=chapter_id,
                force=force,
                only_evaluated_uploads=only_evaluated,
                min_contributions_policy=min_policy,
                auto_release=auto_release,
                persist=persist,
                top_k_audit=top_k,
            )
            if run is None:
                # If persisted, show latest audit row if available.
                try:
                    from accounts.models import DecisionRun
                    latest = DecisionRun.objects.filter(chapter_id=chapter_id).order_by("-id").first()
                    if not quiet:
                        self.stdout.write(self.style.WARNING(f"No winner selected. Latest audit: {latest}"))
                except Exception:
                    if not quiet:
                        self.stdout.write(self.style.WARNING("No winner selected (not due / not ready / no candidates)."))
                return
            if not quiet:
                self.stdout.write(self.style.SUCCESS(f"Decision result: {run}"))
            return

        # all due
        now = timezone.now()
        due_policies = ChapterPolicy.objects.filter(current_deadline__isnull=False, current_deadline__lt=now)

        if not due_policies.exists():
            if not quiet:
                self.stdout.write("No due chapters found.")
            return

        for pol in due_policies.select_related("chapter"):
            ch_id = pol.chapter_id
            run = svc.decide_for_chapter(
                chapter_id=ch_id,
                force=force,
                only_evaluated_uploads=only_evaluated,
                min_contributions_policy=min_policy,
                auto_release=auto_release,
                persist=persist,
                top_k_audit=top_k,
            )
            if run is None:
                if not quiet:
                    self.stdout.write(self.style.WARNING(f"chapter_id={ch_id} -> no winner"))
                continue
            if not quiet:
                self.stdout.write(self.style.SUCCESS(f"chapter_id={ch_id} -> {run}"))
