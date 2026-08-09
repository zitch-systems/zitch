"""Credit and debit alerts — the "₦5,000 debit on your account" message.

Wired to a `post_save` signal on `Transaction` rather than to each money path.
A debit is marked Successful in a dozen places (transfers, VTU, cards, savings,
loans, FX, funding callbacks) and new ones get added; hooking each is a list that
silently goes out of date, and the one path someone forgets is a customer who
never hears that their money moved. One hook on the ledger row covers every
mover, present and future — the ledger is what "money moved" *means*.

Three properties this has to hold:

* **Only on Successful.** `debit()` writes a PENDING row and the caller flips it
  later, so alerting at debit time would announce spends that then fail and
  reverse.
* **Only once.** The signal fires on every save of the row — the status flip is
  itself a save, and later saves (settlement, reconciliation) touch it again.
* **Never on a rolled-back transaction, and never fatal.** Sends are deferred to
  `on_commit` so an alert cannot describe a movement the database threw away, and
  every failure is swallowed and logged: a mail outage must not fail a payment
  that already succeeded.
"""
import logging

from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

log = logging.getLogger("zitch")

#: Rows the customer did not do and should not be pinged about. Matched against
#: `Transaction.service` as a prefix.
_SILENT_SERVICES = ("reversal", "settlement", "adjustment", "sweep")


def _money(amount, currency: str = "NGN") -> str:
    symbol = "₦" if currency == "NGN" else f"{currency} "
    return f"{symbol}{amount:,.2f}"


def _alerts_on(channel: str) -> bool:
    """Per-channel switch. SMS is off unless turned on: at Nigerian per-message
    rates an alert on every transaction is a real recurring cost, and that is a
    business decision rather than a default we should make silently. Email costs
    effectively nothing and is on."""
    cfg = getattr(settings, "TXN_ALERTS", {}) or {}
    return bool(cfg.get(channel.upper(), channel == "email"))


def _describe(txn, *, reversal: bool = False) -> tuple:
    """(subject, body) for the alert. Deliberately short: this is read on a lock
    screen, and the detail lives in the receipt."""
    credit = txn.direction == txn.IN
    word = "Reversal" if reversal else ("Credit" if credit else "Debit")
    amount = _money(txn.amount, txn.currency)

    from .services import get_or_create_wallet
    try:
        balance = _money(get_or_create_wallet(txn.user).balance)
    except Exception:  # noqa: BLE001 — an alert must not depend on reading a balance
        balance = ""

    counterparty = _meta(txn).get("recipient_name") or _meta(txn).get("counterparty") or ""
    where = f" {'from' if credit else 'to'} {counterparty}" if counterparty else ""
    subject = f"{word} alert: {amount}"
    if reversal:
        body = (f"The {amount} debit{where} did not go through and has been "
                f"returned to your Zitch account.\n"
                f"Ref: {txn.reference}\n"
                f"{txn.created:%d %b %Y, %I:%M %p}")
    else:
        body = (f"{word} of {amount}{where} on your Zitch account.\n"
                f"Ref: {txn.reference}\n"
                f"{txn.created:%d %b %Y, %I:%M %p}")
    if balance:
        body += f"\nAvailable balance: {balance}"
    body += "\n\nNot you? Contact Zitch support immediately."
    return subject, body


def send_transaction_alert(txn, *, reversal: bool = False) -> None:
    """Send the credit/debit alert for one settled ledger row.

    Separate from the signal so it can be called directly — re-sending a single
    alert from a shell or a management command needs the message, not the
    dedupe."""
    from utility.providers import send_email, send_sms

    user = txn.user
    subject, body = _describe(txn, reversal=reversal)
    if _alerts_on("email") and getattr(user, "email", ""):
        try:
            send_email(user.email, subject, body)
        except Exception:  # noqa: BLE001
            log.exception("txn_alert_email_failed ref=%s", txn.reference)
    if _alerts_on("sms") and getattr(user, "phone", ""):
        try:
            # One segment where possible — a multi-part alert costs multiples.
            send_sms(user.phone, f"Zitch: {subject}. Ref {txn.reference}.")
        except Exception:  # noqa: BLE001
            log.exception("txn_alert_sms_failed ref=%s", txn.reference)


def _meta(txn) -> dict:
    """`meta` is a free-form JSONField, so a caller can and does put a bare string
    in it. This runs on every ledger write — it must not be the thing that raises."""
    value = getattr(txn, "meta", None)
    return value if isinstance(value, dict) else {}


@receiver(post_save, sender="wallet.Transaction", dispatch_uid="wallet_txn_alert")
def _alert_on_settled_transaction(sender, instance, **kwargs):
    txn = instance
    if str(txn.service or "").startswith(_SILENT_SERVICES):
        return

    # A reversal does not create a ledger row — `refund` and the disbursement
    # webhook flip the existing one to Failed and credit the balance back. So a
    # debit that settled and later reversed would alert once, saying money left,
    # and never say it came back. That is worse than not alerting at all.
    if txn.transaction_status == txn.FAILED:
        # "was this row already announced?" is answered against the DB inside
        # _fire, not here: the flag is written with .update(), so the in-memory
        # instance the caller is holding never sees it and would always say no.
        _defer(txn, "reversal_alerted", reversal=True, requires="alerted")
        return

    if txn.transaction_status != txn.SUCCESS:
        return
    if _meta(txn).get("alerted"):
        return
    _defer(txn, "alerted", reversal=False)


def _defer(txn, flag: str, *, reversal: bool, requires: str = "") -> None:
    """Claim `flag` on the row and send once the surrounding transaction commits.

    `requires` names a flag that must ALREADY be set for this send to happen —
    a reversal notice is only owed to someone who was told about the debit.
    """

    def _fire():
        # Re-read rather than trust the in-memory copy: between the save and the
        # commit another writer may have alerted, and the flag is what stops the
        # customer getting the same alert twice.
        from .models import Transaction

        row = Transaction.objects.filter(pk=txn.pk).first()
        if row is None or _meta(row).get(flag):
            return
        if requires and not _meta(row).get(requires):
            return
        # Merge onto what is in the DB now, not onto the in-memory copy: `meta`
        # carries real payload (provider response, recipient) that another writer
        # may have added since this instance was loaded, and writing the whole
        # field back from a stale copy would drop it.
        #
        # `has_key` rather than `meta__<flag>=True`: a JSON lookup for a key the
        # row does not have yields NULL, and an `exclude` on NULL drops the row —
        # which would make this claim "already alerted" for every first send.
        merged = dict(_meta(row))
        merged[flag] = True
        updated = Transaction.objects.filter(
            pk=txn.pk).exclude(meta__has_key=flag).update(meta=merged)
        if not updated:
            return
        try:
            send_transaction_alert(txn, reversal=reversal)
        except Exception:  # noqa: BLE001 — an alert must never break a payment
            log.exception("txn_alert_failed ref=%s flag=%s", txn.reference, flag)

    db_transaction.on_commit(_fire)
