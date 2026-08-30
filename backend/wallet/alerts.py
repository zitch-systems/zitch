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


def _narration_line(txn) -> str:
    """The customer's own note for this payment, as its own alert line.

    Read from the ledger row's meta, where both money paths already put it:
    `note` for a bank transfer (execute_payout) and `narration` for a bill or a
    top-up (run_provider_purchase). Two keys because the two paths named it
    differently long before there was a narration field to fill either.

    Empty string when there is no note, so the alert closes up around it — a
    "For:" line with nothing after it reads as a rendering fault on the one
    message customers check for fraud.

    Deliberately NOT on the reversal body: a reversal is about money coming back,
    and repeating what the customer meant to buy would put their own words in a
    sentence about a payment that did not happen.
    """
    meta = _meta(txn)
    note = str(meta.get("note") or meta.get("narration") or "").strip()
    return f"For: {note[:60]}\n" if note else ""


def _detail_lines(txn) -> str:
    """Safe transaction context for app-originated WhatsApp debit alerts.

    The alert is often the only thing the linked WhatsApp chat receives for an
    app purchase. Keep provider secrets/tokens out, but include enough ledger
    metadata to identify the transfer, top-up, or meter payment unambiguously.
    """
    meta = _meta(txn)
    service = " ".join(str(getattr(txn, "service", "") or "").split())[:90]
    low = service.lower()
    lines = []

    def add(label, value, limit=120):
        text = " ".join(str(value or "").split())[:limit]
        if text:
            lines.append(f"{label}: {text}")

    if "transfer" in low or meta.get("bank") or meta.get("account"):
        add("To", meta.get("recipient_name"))
        add("Bank", meta.get("bank"))
        account = meta.get("account")
        if account:
            add("Account", _mask_account(account))
    elif "electric" in low or meta.get("meter"):
        add("Service", service)
        add("Customer", meta.get("customer_name") or meta.get("customer"))
        add("Meter", meta.get("meter"))
        add("Type", meta.get("meter_type"))
        add("Address", meta.get("customer_address") or meta.get("address"), 180)
    elif any(word in low for word in ("airtime", "data")) or meta.get("phone"):
        add("Service", service)
        add("Phone", meta.get("phone"))
    elif service:
        add("Service", service)

    return "".join(f"{line}\n" for line in lines)


def _describe(txn, *, reversal: bool = False) -> tuple:
    """(subject, body) for the alert, with bounded non-secret transaction detail."""
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
                f"{_detail_lines(txn)}"
                f"{_narration_line(txn)}"
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
    mocked = []
    if _alerts_on("email") and getattr(user, "email", ""):
        try:
            res = send_email(user.email, subject, body,
                             html=_email_alert_html(txn, reversal=reversal))
            if (res or {}).get("mock"):
                mocked.append("email")
        except Exception:  # noqa: BLE001
            log.exception("txn_alert_email_failed ref=%s", txn.reference)
    if _alerts_on("sms") and getattr(user, "phone", ""):
        try:
            res = send_sms(user.phone, _sms_alert(txn, reversal=reversal))
            if (res or {}).get("mock"):
                mocked.append("sms")
        except Exception:  # noqa: BLE001
            log.exception("txn_alert_sms_failed ref=%s", txn.reference)
    # send_email/send_sms return a silent MOCK SUCCESS when their provider is
    # unkeyed, and the dedupe flag is claimed before this runs — so an unkeyed
    # process announces nothing, records the row as announced, and never retries.
    # That is exactly how every deposit alert went missing: the crons that credit
    # deposits are separate processes from the web service and had no RESEND_* or
    # TERMII_* keys of their own, so the failure was invisible on both sides.
    # Never silent again: the process that is dropping alerts says so.
    if mocked and not (settings.DEBUG or getattr(settings, "TESTING", False)):
        log.warning("txn_alert_unkeyed_channels ref=%s channels=%s — this process has no "
                    "credentials for them, so the customer was NOT notified",
                    txn.reference, ",".join(mocked))
    _push_alert(txn, subject)
    if _whatsapp_alert(txn, subject, body, reversal=reversal):
        _mark_flag(txn.pk, "whatsapp_alerted")


def _push_alert(txn, subject: str) -> None:
    """Send a privacy-bounded native app notification to every live install.

    The lock-screen body carries the direction/amount but no recipient account,
    phone number, balance, or narration. Full details remain behind app auth.
    Invalid Expo tokens are removed immediately so every later ledger movement
    does not retry a handset that uninstalled the app.
    """
    if not _alerts_on("push"):
        return
    try:
        import requests
        from accounts.models import PushDevice

        devices = list(txn.user.push_devices.filter(enabled=True).only("id", "token")[:100])
        if not devices:
            return
        payload = [{
            "to": device.token,
            "title": subject,
            "body": "Tap to view the transaction securely in Zitch.",
            "sound": "default",
            "priority": "high",
            "channelId": "transactions",
            "data": {"screen": "notifications", "reference": txn.reference},
        } for device in devices]
        response = requests.post("https://exp.host/--/api/v2/push/send",
                                 json=payload, timeout=5)
        response.raise_for_status()
        results = response.json().get("data") or []
        if isinstance(results, dict):
            results = [results]
        invalid_ids = [device.id for device, result in zip(devices, results)
                       if result.get("status") == "error"
                       and (result.get("details") or {}).get("error") == "DeviceNotRegistered"]
        if invalid_ids:
            PushDevice.objects.filter(id__in=invalid_ids).delete()
    except Exception:  # noqa: BLE001 — an alert can never fail a settled payment
        log.exception("txn_alert_push_failed ref=%s", txn.reference)


def send_whatsapp_transaction_alert(txn, *, reversal: bool = False) -> bool:
    """Send only the WhatsApp leg of a transaction alert.

    The main ``alerted`` flag covers email/SMS/push. WhatsApp needs its own
    retryable flag because app-originated transactions can settle while Meta is
    briefly refusing sends, while the customer's WhatsApp link is being repaired,
    or inside a process missing WhatsApp credentials. In those cases email must
    not duplicate, but the chat alert is still owed on the next ledger save or
    reconciliation touch.
    """
    subject, body = _describe(txn, reversal=reversal)
    if _whatsapp_alert(txn, subject, body, reversal=reversal):
        _mark_flag(txn.pk, "whatsapp_alerted")
        return True
    return False


def _whatsapp_alert(txn, subject: str, body: str, *, reversal: bool = False) -> bool:
    """Alert the customer where they actually bank, for a WhatsApp customer.

    Costs nothing per message and lands in the thread they already use, which
    for this channel's customers is the one place they will see it — an email
    alert to someone who signed up on WhatsApp and has never opened the app is a
    notification nobody reads.

    The original debit/credit notice is skipped when the transaction itself was
    done ON WhatsApp: the flow that ran the transfer/purchase already sent a
    receipt and a "balance is now" line into that same chat (see `reply_receipt`
    in whatsapp/router.py), so this alert would just be the same movement
    announced twice in one thread. A transaction started elsewhere (the app, the
    operator console) still gets this — it is the only notice that customer sees
    in WhatsApp. A REVERSAL is never skipped, even on a WhatsApp-channel
    transaction: it happens later, out of band (a settlement callback, a
    reconciler), and nothing else in the original chat ever told the customer
    their money came back. Neither is a row the chat could only announce as
    "⏳ … processing" — see `mark_awaiting_settlement`.

    Best-effort in every direction: no link, no send; a failure is logged and
    never propagates, because an alert must not be able to roll back the ledger
    write that triggered it.
    """
    if not _alerts_on("whatsapp"):
        return False
    meta = _meta(txn)
    if (not reversal and meta.get("channel") == "whatsapp"
            and not meta.get("wa_awaiting_settlement")):
        return False
    try:
        from whatsapp.models import WhatsAppLink
        from whatsapp.router import reply

        link = WhatsAppLink.objects.filter(user=txn.user, status=WhatsAppLink.ACTIVE).first()
        if link is None:
            log.info("txn_alert_whatsapp_no_active_link ref=%s user=%s",
                     txn.reference, txn.user_id)
            return False
        icon = "💰" if txn.direction == txn.IN else "💸"
        result = reply(link.wa_msisdn, f"{icon} *{subject}*\n\n{body}")
        if not (result or {}).get("success"):
            log.warning("txn_alert_whatsapp_not_delivered ref=%s user=%s code=%s detail=%r",
                        txn.reference, txn.user_id, (result or {}).get("error_code"),
                        (result or {}).get("error_detail") or (result or {}).get("message"))
            return False
        return True
    except Exception:  # noqa: BLE001
        log.exception("txn_alert_whatsapp_failed ref=%s", txn.reference)
        return False


def mark_awaiting_settlement(txn) -> None:
    """Record that the WhatsApp chat could only tell the customer "processing".

    The channel de-dupe above rests on one assumption: that the chat already
    announced the outcome. That holds for a transfer or purchase which settled
    synchronously — the chat sent a receipt and a balance line. It does NOT hold
    for a row that came back PENDING: the chat said "⏳ … is processing", the row
    settles minutes later out of band (a payout webhook, the reconciler), and the
    settlement save is the ONLY moment an alert could fire. Suppressed there, the
    last thing the customer ever heard about their money was "processing" —
    strictly worse than the duplicate the de-dupe exists to prevent.

    Merges onto what is in the DB rather than the in-memory copy, for the same
    reason `_defer` does: `meta` carries provider payload another writer may have
    added since this instance was loaded.
    """
    from .models import Transaction

    row = Transaction.objects.filter(pk=txn.pk).first()
    if row is None:
        return
    merged = dict(_meta(row))
    if merged.get("wa_awaiting_settlement"):
        return
    merged["wa_awaiting_settlement"] = True
    Transaction.objects.filter(pk=txn.pk).update(meta=merged)


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
    meta = _meta(txn)
    if meta.get("alerted"):
        if not meta.get("whatsapp_alerted") and _whatsapp_retry_due(txn):
            _defer(txn, "whatsapp_alerted", reversal=False, whatsapp_only=True)
        return
    _defer(txn, "alerted", reversal=False)


def _whatsapp_retry_due(txn) -> bool:
    """Whether a settled row still owes a WhatsApp transaction alert."""
    if not _alerts_on("whatsapp"):
        return False
    meta = _meta(txn)
    if meta.get("channel") == "whatsapp" and not meta.get("wa_awaiting_settlement"):
        return False
    return True


def _mark_flag(txn_pk, flag: str) -> None:
    from .models import Transaction

    row = Transaction.objects.filter(pk=txn_pk).first()
    if row is None or _meta(row).get(flag):
        return
    merged = dict(_meta(row))
    merged[flag] = True
    Transaction.objects.filter(pk=txn_pk).exclude(meta__has_key=flag).update(meta=merged)


def _defer(txn, flag: str, *, reversal: bool, requires: str = "",
           whatsapp_only: bool = False) -> None:
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
        try:
            if whatsapp_only:
                if send_whatsapp_transaction_alert(row, reversal=reversal):
                    _mark_flag(row.pk, flag)
                return
            merged = dict(_meta(row))
            merged[flag] = True
            updated = Transaction.objects.filter(
                pk=txn.pk).exclude(meta__has_key=flag).update(meta=merged)
            if not updated:
                return
            send_transaction_alert(row, reversal=reversal)
        except Exception:  # noqa: BLE001 — an alert must never break a payment
            log.exception("txn_alert_failed ref=%s flag=%s", txn.reference, flag)

    db_transaction.on_commit(_fire)


#: Nigerian bank alerts all follow one shape, and customers read them by
#: position rather than by reading them: direction and amount on line one, the
    masked account, the description, the balance, the timestamp. Matching it
    means a Zitch alert is scanned the same way as the one from their bank
    sitting directly above it, instead of asking them to learn a second format.
_SMS_MAX = 160          # one GSM-7 segment; a multi-part alert costs multiples


def _sms_money(amount, currency: str = "NGN") -> str:
    """Amounts for SMS, written the way the bank writes them: "NGN 4,300.00".

    Not cosmetic. "₦" is outside GSM-7, and one character outside it forces the
    ENTIRE message into UCS-2, where a segment is 70 characters rather than 160
    — so a single naira sign turns every alert into two billable messages. This
    is very likely why the bank's own alerts spell out NGN.
    """
    return f"{currency} {amount:,.2f}"


def _mask_account(number: str) -> str:
    """0228565772 -> 0228****72, the masking the bank's own alerts use."""
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    if len(digits) < 6:
        return digits or "—"
    return f"{digits[:4]}****{digits[-2:]}"


def _sms_alert(txn, *, reversal: bool = False) -> str:
    """The alert in the bank's own format.

    A reversal is a CR whatever the row's direction says — the customer is being
    given money back, and calling it a debit because the original was one would
    be the single most alarming way to phrase good news.
    """
    from .services import get_or_create_wallet

    credit = reversal or txn.direction == txn.IN
    try:
        wallet = get_or_create_wallet(txn.user)
        account, balance = wallet.account_number, _sms_money(wallet.balance)
    except Exception:  # noqa: BLE001 — an alert must never depend on reading a wallet
        account, balance = "", ""

    desc = (_meta(txn).get("recipient_name") or _meta(txn).get("counterparty")
            or (txn.service or "").strip() or ("Credit" if credit else "Debit"))
    if reversal:
        desc = f"REVERSAL-{desc}"
