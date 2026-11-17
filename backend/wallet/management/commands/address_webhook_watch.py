"""Every two hours: the Tier 3 address checks the bank has not answered.

Wema sends the address-verification webhook once and does not retry; a missed
one is sent again only when Zitch asks (Temi, Wema, 17 Nov 2026). So every two
hours the operator (``utility.alerts`` → ``OPS_ALERT_EMAILS``) gets the list to
raise with Wema. An unchanged list is emailed again at most every six hours
(``OPS_ALERT_REPEAT_SECONDS``); a new customer on it is emailed at once:

- address checks waiting more than two hours with no webhook at all;
- checks the webhook passed whose bank record still doesn't show Tier 3 a day
  later (the reconcile job finishes those by itself once the record catches up).

Pseudonymous only, as every operator alert: user ids and the last four digits
of the account, never names or addresses. The full account number is in admin.
Nothing to report sends nothing.
"""
import hashlib
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

#: An address check with no webhook after this long is listed.
NO_WEBHOOK_AFTER = timedelta(hours=2)
#: A webhook-passed check whose bank record still lags after this long is listed.
RECORD_LAG_AFTER = timedelta(days=1)
#: Customers named per list in the email; the count covers the rest.
LIST_LIMIT = 20


def _entries(users, since_field, now):
    rows = []
    for user in users.order_by(since_field)[:LIST_LIMIT]:
        since = getattr(user, since_field)
        hours = int((now - since).total_seconds() // 3600) if since else 0
        waited = f"{hours}h" if hours < 48 else f"{hours // 24}d"
        last4 = (user.address_verification_account_number or "")[-4:] or "----"
        rows.append(f"user {user.pk} (account ******{last4}, {waited})")
    return rows


class Command(BaseCommand):
    help = "Email the operator the Tier 3 address checks Wema has not answered."

    def handle(self, *args, **options):
        from accounts.models import User
        from utility.alerts import alert
        from wallet.wema_callbacks import TIER3_BANK_OK

        now = timezone.now()
        waiting = User.objects.filter(address_verification_pending=True, address_verified=False)
        no_webhook = waiting.exclude(tier3_review=TIER3_BANK_OK).filter(
            address_verification_requested_at__lt=now - NO_WEBHOOK_AFTER)
        lagging = waiting.filter(tier3_review=TIER3_BANK_OK,
                                 tier3_review_at__lt=now - RECORD_LAG_AFTER)
        missing, behind = no_webhook.count(), lagging.count()
        self.stdout.write(f"address checks: no_webhook={missing} bank_record_behind={behind}")
        if not (missing or behind):
            return
        context = {"no_webhook": missing, "bank_record_behind": behind}
        if missing:
            context["no_webhook_list"] = "; ".join(
                _entries(no_webhook, "address_verification_requested_at", now))
        if behind:
            context["bank_record_behind_list"] = "; ".join(
                _entries(lagging, "tier3_review_at", now))
        # The same list is emailed again only after OPS_ALERT_REPEAT_SECONDS (six
        # hours by default); a list with a customer added or gone is a new email.
        listed = (sorted(no_webhook.values_list("pk", flat=True)),
                  sorted(lagging.values_list("pk", flat=True)))
        alert("Tier 3 address checks to raise with Wema. no_webhook: ask Wema to resend "
              "the address webhook. bank_record_behind: the webhook passed but Wema's "
              "record does not show Tier 3", level="error",
              dedupe=hashlib.sha256(repr(listed).encode()).hexdigest()[:12], **context)
