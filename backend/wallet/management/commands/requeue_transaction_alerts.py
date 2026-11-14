"""Send again the transaction alerts that stopped short of the customer.

A delivery lands in REVIEW when its outcome is unknown (the provider answered
ambiguously, or acknowledged without a message id) and in EXHAUSTED when its
retries ran out. Neither is retried on its own, so without this a customer can
go without the alert for a deposit or a debit for good.

Read-only unless --apply. Prints which deliveries it would requeue: the channel,
the state and reason, and the movement's direction, amount, reference tail and
booking time. No contact address or message text is printed.

  python manage.py requeue_transaction_alerts                 # list the last 48 hours
  python manage.py requeue_transaction_alerts --apply         # send them again
  python manage.py requeue_transaction_alerts --ids 76,78 --apply
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from wallet.models import TransactionAlertDelivery as Delivery

DEFAULT_REASONS = ("dispatch_lease_expired", "dispatch_outcome_unknown", "retry_limit")


class Command(BaseCommand):
    help = ("List, and with --apply requeue, transaction alerts in review or exhausted. "
            "Requeued alerts are sent by the WhatsApp worker within seconds.")

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Requeue them (default: list only).")
        parser.add_argument("--since-hours", type=int, default=48,
                            help="Only alerts created in the last N hours (1-720, default 48).")
        parser.add_argument("--ids", default="", help="Comma-separated delivery ids to limit to.")
        parser.add_argument("--channel", choices=[c for c, _ in Delivery.CHANNELS], default="",
                            help="Only this channel.")
        parser.add_argument("--reason", action="append", default=[],
                            help=f"Only this error code (repeatable). Default: {', '.join(DEFAULT_REASONS)}.")

    def handle(self, *args, **options):
        hours = options["since_hours"]
        if not 1 <= hours <= 720:
            raise CommandError("--since-hours must be 1-720.")
        ids = []
        for part in str(options["ids"] or "").split(","):
            part = part.strip()
            if not part:
                continue
            if not part.isdigit():
                raise CommandError(f"--ids takes numbers, got {part!r}.")
            ids.append(int(part))
        reasons = options["reason"] or list(DEFAULT_REASONS)

        rows = Delivery.objects.filter(
            state__in=(Delivery.REVIEW, Delivery.EXHAUSTED),
            error_code__in=reasons,
            created__gte=timezone.now() - timedelta(hours=hours),
        ).select_related("transaction").order_by("pk")
        if ids:
            rows = rows.filter(pk__in=ids)
        if options["channel"]:
            rows = rows.filter(channel=options["channel"])

        found = list(rows)
        for d in found:
            t = d.transaction
            self.stdout.write(
                f"delivery {d.pk}: {d.channel:<8} {d.state:<9} {d.error_code:<24} "
                f"{t.direction} N{t.amount} ref ...{t.reference[-6:]} "
                f"booked {timezone.localtime(t.created):%d %b %H:%M}")
        if not found:
            self.stdout.write("Nothing to requeue.")
            return
        if not options["apply"]:
            self.stdout.write(f"{len(found)} alert(s) would be requeued. Run again with --apply to send them.")
            return
        # attempts restarts at zero: the operator has decided these are owed, and
        # an exhausted row would otherwise be exhausted again on its first pass.
        requeued = Delivery.objects.filter(
            pk__in=[d.pk for d in found], state__in=(Delivery.REVIEW, Delivery.EXHAUSTED),
        ).update(state=Delivery.READY, error_code="requeued_by_operator", attempts=0,
                 next_attempt_at=None, lease_expires_at=None, updated=timezone.now())
        self.stdout.write(self.style.SUCCESS(
            f"Requeued {requeued} alert(s); the WhatsApp worker sends them within seconds."))
