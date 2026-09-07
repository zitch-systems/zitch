"""Transaction monitoring — open AML cases for the patterns the policy commits to.

`compliance/WHATSAPP_PAYMENTS_COMPLIANCE_QA_v1.0.md` tells a regulator we monitor for
"threshold breaches (₦5M+), rapid velocity, structuring, layering, PEP activity" and
file SARs with NFIU "within 30 days of detection". Nothing in the code did any of that.

A cron rather than a hook inside `debit()`: monitoring that runs under the wallet lock
either blocks money on a heuristic or has to be made non-blocking anyway, and neither
belongs in a money path. Read-only over the ledger — the only writes are cases.

Also pages on OVERDUE cases, which is the part that actually matters: an unfiled SAR
past its 30-day deadline is a regulatory breach, and it is invisible unless something
looks for it.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = ("Scan recent ledger activity for AML patterns and open cases; report and "
            "page on cases past their 30-day filing deadline.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours", type=int, default=24,
            help="How far back to scan (default 24). Overlap is safe — cases dedupe.")
        parser.add_argument(
            "--fail-on-overdue", action="store_true",
            help="Exit 1 when any open case is past its filing deadline, so the cron "
                 "goes red. An overdue SAR is a breach, not a backlog.")

    def handle(self, *args, **options):
        from datetime import timedelta

        from compliance.models import AmlCase
        from compliance.services import scan_transactions
        from utility.alerts import alert

        since = timezone.now() - timedelta(hours=max(1, options["hours"]))
        counts = scan_transactions(since=since)
        self.stdout.write(
            f"AML scan: {counts['scanned']} outbound row(s) since {since:%Y-%m-%d %H:%M} — "
            f"{counts['threshold']} threshold, {counts['structuring']} structuring, "
            f"{counts['velocity']} velocity case(s) opened")

        opened = counts["threshold"] + counts["structuring"] + counts["velocity"]
        if opened:
            # Not an error: a case opening is the system working. But nobody watches a
            # queue they are not told about.
            alert(f"AML monitoring opened {opened} case(s)", level="warning", **counts)

        open_cases = AmlCase.objects.filter(status__in=(AmlCase.OPEN, AmlCase.INVESTIGATING))
        overdue = [c for c in open_cases if c.overdue]
        due_soon = [c for c in open_cases if not c.overdue
                    and (c.due - timezone.now()).days <= 7]

        self.stdout.write(f"Queue: {open_cases.count()} open, {len(due_soon)} due within 7 days, "
                          f"{len(overdue)} OVERDUE")
        for case in overdue:
            self.stderr.write(f"OVERDUE case={case.pk} user={case.user_id} "
                              f"trigger={case.trigger} due={case.due:%Y-%m-%d}")

        if overdue:
            alert("AML cases past their 30-day NFIU filing deadline", level="error",
                  overdue=len(overdue), case_ids=[c.pk for c in overdue][:25])
        if overdue and options["fail_on_overdue"]:
            raise SystemExit(1)
