"""
Management command: mint_missing_certs

Scans all currently released chapters and mints contributor certificates
for any that are missing one. Run this once to backfill certificates for
chapters that were released before the certificate system existed.

Usage:
    python manage.py mint_missing_certs
    python manage.py mint_missing_certs --dry-run
"""

from django.core.management.base import BaseCommand
from accounts.models import ReleasedContent, BlockchainCertificate, Course


class Command(BaseCommand):
    help = "Mint missing contributor certificates for all currently released chapters."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be minted without actually minting.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # Find all released chapters that have a best upload
        released = (
            ReleasedContent.objects
            .filter(release_status=True)
            .select_related("upload__chapter__course", "upload__contributor")
        )

        self.stdout.write(f"Found {released.count()} released content records.")

        minted = 0
        skipped = 0
        errors = 0

        for rc in released:
            upload = rc.upload
            chapter = upload.chapter
            course = chapter.course
            contributor = upload.contributor

            if not contributor:
                self.stdout.write(
                    self.style.WARNING(f"  [SKIP] Chapter '{chapter.chapter_name}' — no contributor on upload.")
                )
                skipped += 1
                continue

            # Check if cert already exists
            already = BlockchainCertificate.objects.filter(
                user=contributor,
                chapter=chapter,
                certificate_type=BlockchainCertificate.CERT_TYPE_CONTRIBUTOR,
            ).exists()

            if already:
                self.stdout.write(
                    f"  [EXISTS] {contributor.username} | {chapter.chapter_name} — cert already exists, skipping."
                )
                skipped += 1
                continue

            self.stdout.write(
                f"  [MINT] {contributor.username} | {chapter.chapter_name} | {course.course_name}"
            )

            if dry_run:
                minted += 1
                continue

            try:
                from blockchain.services.certificate_service import (
                    mint_certificate, ISSUE_TYPE_CONTRIBUTOR
                )
                result = mint_certificate(
                    recipient_name=contributor.get_full_name() or contributor.username,
                    course_name=f"{course.course_name} – {chapter.chapter_name}",
                    issue_type=ISSUE_TYPE_CONTRIBUTOR,
                )
                if result.get("success"):
                    BlockchainCertificate.objects.get_or_create(
                        token_id=result["token_id"],
                        defaults={
                            "user": contributor,
                            "course": course,
                            "chapter": chapter,
                            "certificate_type": BlockchainCertificate.CERT_TYPE_CONTRIBUTOR,
                            "tx_hash": result["tx_hash"],
                        }
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"    ✓ Minted! token_id={result['token_id']} tx={result['tx_hash'][:16]}..."
                        )
                    )
                    minted += 1
                else:
                    self.stdout.write(
                        self.style.ERROR(f"    ✗ Minting failed: {result.get('error')}")
                    )
                    errors += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"    ✗ Exception: {e}"))
                errors += 1

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{prefix}Done. Minted: {minted} | Skipped: {skipped} | Errors: {errors}"
            )
        )
