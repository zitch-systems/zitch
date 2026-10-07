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
* **Never on a rolled-back transaction, and never hold up a payment response.**
  Outbox rows persist with the ledger. Production sends run in the existing
  worker's background alert sweep, so slow notification providers cannot keep
  the payment response open after its money committed. Local/test callbacks
  run after commit for synchronous development feedback.
"""
import logging
from html import escape
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

log = logging.getLogger("zitch")
_LAGOS_TZ = ZoneInfo("Africa/Lagos")


def _alert_timestamp(value, format_string: str) -> str:
    """Render customer-facing financial timestamps in Lagos time with its label.

    Transaction datetimes are timezone-aware UTC values in production. Formatting
    them directly bypasses Django's display timezone and made alerts disagree with
    the receipt shown to a Lagos customer.
    """
    return f"{timezone.localtime(value, _LAGOS_TZ):{format_string}} WAT"

#: Rows the customer did not do and should not be pinged about. Matched against
#: `Transaction.service` as a prefix.
_SILENT_SERVICES = ("reversal", "settlement", "adjustment", "sweep")


def _silent_transaction(txn) -> bool:
    return (bool(_meta(txn).get("suppress_transaction_alert"))
            or str(txn.service or "").casefold().startswith(_SILENT_SERVICES))


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


def _balance_rows(txn) -> list[tuple[str, str]]:
    """Use the spending boundary, not an aggregate that includes retained funds."""
    from .services import wallet_balance_payload

    try:
        balances = wallet_balance_payload(txn.user)
        total = balances["balance"]
        available = balances["available_balance"]
        historical = balances["historical_balance"]
        if available != total or historical:
            lines = [("Total NGN wallet balance", _money(total)),
                     ("Available for bills", _money(available))]
            if historical:
                lines.append(("Historical funds unavailable for bills", _money(historical)))
            return lines
        return [("Available balance", _money(available))]
    except Exception:  # noqa: BLE001 — balance reads must never prevent a payment alert
        return []


def _describe(txn, *, reversal: bool = False) -> tuple:
    """(subject, body) for the alert, with bounded non-secret transaction detail."""
    credit = txn.direction == txn.IN
    word = "Reversal" if reversal else ("Credit" if credit else "Debit")
    amount = _money(txn.amount, txn.currency)

    meta = _meta(txn)
    counterparty = meta.get("recipient_name") or meta.get("counterparty") or ""
    where = f" {'from' if credit else 'to'} {counterparty}" if counterparty else ""
    # A WhatsApp request that returned PROCESSING already told the customer
    # that it was processing. When the bank later settles that same row, the
    # follow-up must say SUCCESSFUL explicitly; a second generic "Debit alert"
    # looks like a second charge and was the source of the confusing screenshots.
    service = str(getattr(txn, "service", "") or "").lower()
    settled_after_pending = (
        not reversal
        and bool(meta.get("wa_awaiting_settlement"))
        and getattr(txn, "transaction_status", None) == txn.SUCCESS
    )
    if settled_after_pending:
        if "transfer" in service:
            settled_word = "Transfer successful"
        elif "airtime" in service:
            settled_word = "Airtime purchase successful"
        elif "data" in service:
            settled_word = "Data purchase successful"
        else:
            settled_word = "Payment successful"
        subject = f"{settled_word}: {amount}"
        body = (f"✅ {settled_word}: {amount}{where} on your Zitch account.\n"
                f"{_detail_lines(txn)}"
                f"{_narration_line(txn)}"
                f"Ref: {txn.reference}\n"
                f"{_alert_timestamp(txn.created, '%d %b %Y, %I:%M %p')}")
    elif reversal:
        subject = f"Reversal alert: {amount}"
        body = (f"The {amount} debit{where} did not go through and has been "
                f"returned to your Zitch account.\n"
                f"Ref: {txn.reference}\n"
                f"{_alert_timestamp(txn.created, '%d %b %Y, %I:%M %p')}")
    else:
        subject = f"{word} alert: {amount}"
        body = (f"{word} of {amount}{where} on your Zitch account.\n"
                f"{_detail_lines(txn)}"
                f"{_narration_line(txn)}"
                f"Ref: {txn.reference}\n"
                f"{_alert_timestamp(txn.created, '%d %b %Y, %I:%M %p')}")
    for label, value in _balance_rows(txn):
        body += f"\n{label}: {value}"
    body += "\n\nNot you? Contact Zitch support immediately."
    return subject, body


def send_transaction_alert(txn, *, reversal: bool = False) -> None:
    """Resume due outbox channels; accepted channels are never sent again."""
    for delivery_pk in _enqueue_alert(txn.pk, reversal=reversal):
        _dispatch_alert(delivery_pk)


def _push_alert(txn, subject: str) -> dict:
    """Send a privacy-bounded native app notification to every live install.

    The lock-screen body carries the direction/amount but no recipient account,
    phone number, balance, or narration. Full details remain behind app auth.
    Invalid Expo tokens are removed immediately so every later ledger movement
    does not retry a handset that uninstalled the app.
    """
    if not _alerts_on("push"):
        return {"success": False, "not_dispatched": True, "code": "channel_disabled"}
    try:
        import requests
        from accounts.models import PushDevice

        devices = list(txn.user.push_devices.filter(enabled=True).only("id", "token")[:100])
        if not devices:
            return {"success": False, "not_dispatched": True, "code": "no_push_device"}
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
        # Partial acceptance cannot be retried as one batch without duplicating
        # the devices that accepted it. Keep that case visible for review.
        passed = (isinstance(results, list) and len(results) == len(devices)
                  and all(isinstance(result, dict) and result.get("status") == "ok"
                          for result in results))
        return {"success": passed, "uncertain": not passed, "code": "push_partial_or_unknown"}
    except Exception:  # noqa: BLE001 — an alert can never fail a settled payment
        log.warning("txn_alert_push_failed ref=%s", txn.reference)
        return {"success": False, "uncertain": True, "code": "push_dispatch_unknown"}


def send_whatsapp_transaction_alert(txn, *, reversal: bool = False) -> bool:
    """Resume only the WhatsApp outbox channel; return provider acceptance.

    Independent state lets the worker retry an explicit refusal without
    repeating any accepted email, SMS, push or WhatsApp delivery.
    """
    return any(_dispatch_alert(pk) for pk in _enqueue_alert(
        txn.pk, reversal=reversal, whatsapp_only=True))


def _deliver_claimed_whatsapp_alert(txn, *, reversal: bool = False):
    """Deliver a WhatsApp alert after its durable row claim was acquired."""
    subject, body = _describe(txn, reversal=reversal)
    return _whatsapp_alert(txn, subject, body, reversal=reversal)


def _whatsapp_claim_flag(reversal: bool) -> str:
    """Return the legacy acceptance mirror; the outbox is authoritative."""
    return "whatsapp_reversal_alerted" if reversal else "whatsapp_alerted"


def _whatsapp_alert(txn, subject: str, body: str, *, reversal: bool = False):
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

    Sent as free-form text, which WhatsApp delivers only inside the customer's
    24-hour service window. Outside it — the usual case for an app transaction or
    a late-settling payout — Meta refuses the text and the send falls back to a
    pre-approved UTILITY template (`_whatsapp_alert_via_template`), the only
    proactive message the platform allows there.
    """
    if not _alerts_on("whatsapp"):
        return {"success": False, "not_dispatched": True, "code": "channel_disabled"}
    meta = _meta(txn)
    if (not reversal and meta.get("channel") == "whatsapp"
            and not meta.get("wa_awaiting_settlement")):
        return {"success": False, "not_dispatched": True, "code": "chat_already_announced"}
    try:
        from whatsapp.models import WhatsAppLink
        from whatsapp.router import reply

        link = WhatsAppLink.objects.filter(user=txn.user, status=WhatsAppLink.ACTIVE).first()
        if link is None:
            log.info("txn_alert_whatsapp_no_active_link ref=%s user=%s",
                     txn.reference, txn.user_id)
            return {"success": False, "not_dispatched": True, "code": "no_active_link"}
        icon = "💰" if txn.direction == txn.IN else "💸"
        result = reply(link.wa_msisdn, f"{icon} *{subject}*\n\n{body}")
        if isinstance(result, dict) and result.get("success") is True:
            return result
        # Free-form text is delivered only INSIDE WhatsApp's 24-hour
        # customer-service window. Meta refuses it once that window closes
        # (re-engagement error 131047) — and that is the normal state for the
        # alert this channel exists to send: a transaction the customer did in the
        # app, or a payout that settles hours after they last chatted, when they
        # have not messaged the bot at all. A pre-approved UTILITY template is the
        # only message the platform still delivers then, so fall back to it.
        #
        # ONLY on the window-closed codes: any other rejection (a transient Meta
        # error, an undeliverable number) is left owed and retried as free-form on
        # the next ledger touch or the WhatsApp-worker retry sweep — the customer may be back
        # inside the window by then, and escalating a transient blip to a paid
        # template every time would be both wasteful and a duplicate once the text
        # goes through. The in-window path already returned above, so a template
        # is spent only when the text was genuinely refused for being out-of-window.
        if _window_closed(result):
            return _whatsapp_alert_via_template(txn, link.wa_msisdn, reversal=reversal)
        log.warning("txn_alert_whatsapp_not_delivered ref=%s user=%s code=%s",
                    txn.reference, txn.user_id, (result or {}).get("error_code"),
                    )
        return result
    except Exception:  # noqa: BLE001
        log.warning("txn_alert_whatsapp_failed ref=%s", txn.reference)
        return {"success": False, "uncertain": True, "code": "whatsapp_dispatch_unknown"}


#: Meta error codes that mean WhatsApp's 24-hour customer-service window has
#: closed, so the free-form text was refused and only a template can reach the
#: customer. 131047 is the Cloud API "re-engagement message" code; 470 is the
#: older Graph code for the same condition, still returned by some versions.
_WINDOW_CLOSED_CODES = frozenset({131047, 470})


def _window_closed(result) -> bool:
    """True when a send was refused specifically for being outside the 24-hour
    window. `error_code` is Meta's int, but `_message_result` can fall back to an
    HTTP status or the string "provider_error", so coerce defensively."""
    try:
        return int((result or {}).get("error_code")) in _WINDOW_CLOSED_CODES
    except (TypeError, ValueError):
        return False


def _oneline(text) -> str:
    """One-line form for a WhatsApp template parameter.

    Meta rejects a template variable that contains a newline, a tab, or four or
    more consecutive spaces, so every value substituted into the alert template
    is flattened first. Whitespace is collapsed rather than merely stripped, so a
    name or narration carrying an embedded newline cannot slip a rejection
    through and silently drop the whole alert."""
    return " ".join(str(text or "").split())


def _whatsapp_template_summary(txn, *, reversal: bool) -> str:
    """The single-line headline for the template's ``{{1}}``.

    Carries the same facts the free-form alert leads with — direction, amount,
    counterparty, resulting balance — flattened onto one line, because a template
    variable cannot hold the line breaks the free-form body uses.
    """
    credit = reversal or txn.direction == txn.IN
    amount = _money(txn.amount, txn.currency)
    counterparty = _meta(txn).get("recipient_name") or _meta(txn).get("counterparty") or ""
    if reversal:
        where = f" to {counterparty}" if counterparty else ""
        summary = f"Reversal: {amount}{where} returned to your Zitch account."
    else:
        word = "Credit" if credit else "Debit"
        where = f" {'from' if credit else 'to'} {counterparty}" if counterparty else ""
        summary = f"{word} of {amount}{where} on your Zitch account."
    for label, value in _balance_rows(txn):
        summary += f" {label}: {value}."
    return _oneline(summary)


def _whatsapp_alert_via_template(txn, msisdn: str, *, reversal: bool = False):
    """Deliver the alert through the pre-approved UTILITY template.

    A template is the only message WhatsApp will send outside the 24-hour window,
    so this is the fallback ``_whatsapp_alert`` reaches for when the free-form
    send is refused. Two body variables, both single-line: ``{{1}}`` the summary,
    ``{{2}}`` the reference (see ``WHATSAPP["TXN_ALERT_TEMPLATE"]`` and
    ``docs/whatsapp-production-operations.md`` for the exact template to create and approve).
    A blank template name disables the fallback — in-window free-form still works.

    Best-effort, like every other leg: a refused or unconfigured template is
    logged with the fix and never raised, so it cannot break the ledger write
    that triggered the alert. Returns the bounded provider outcome.
    """
    cfg = getattr(settings, "WHATSAPP", {}) or {}
    template = str(cfg.get("TXN_ALERT_TEMPLATE") or "").strip()
    if not template:
        return {"success": False, "not_dispatched": True, "code": "template_unconfigured"}
    lang = str(cfg.get("TXN_ALERT_TEMPLATE_LANG") or "en_US").strip() or "en_US"
    from whatsapp.router import reply_template

    summary = _whatsapp_template_summary(txn, reversal=reversal)
    result = reply_template(msisdn, template, [summary, txn.reference], lang=lang)
    if isinstance(result, dict) and result.get("success") is True:
        log.info("txn_alert_whatsapp_template_sent ref=%s template=%s", txn.reference, template)
        return result
    log.warning(
        "txn_alert_whatsapp_template_not_delivered ref=%s template=%s code=%s — the "
        "out-of-window fallback needs a two-variable UTILITY template named %r, APPROVED in "
        "WhatsApp Manager (see docs/whatsapp-production-operations.md); set WHATSAPP_TXN_ALERT_TEMPLATE to "
        "rename it, or blank to disable the fallback",
        txn.reference, template, (result or {}).get("error_code"), template)
    return result


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

    terminal_status = None
    with db_transaction.atomic():
        row = (Transaction.objects.select_for_update()
               .filter(pk=txn.pk).first())
        if row is None:
            return
        merged = dict(_meta(row))
        if merged.get("wa_awaiting_settlement"):
            return
        merged["wa_awaiting_settlement"] = True
        # The row lock serializes this merge with settlement writes, preserving
        # provider metadata and ensuring the terminal save sees the marker.
        Transaction.objects.filter(pk=txn.pk).update(meta=merged)
        terminal_status = row.transaction_status

    # A bank response can settle between the provider call and this marker. In
    # that case the post-save signal may already have run without knowing that
    # the customer only heard "processing". Repair the terminal outcome now,
    # using the same durable claims as the normal signal path.
    if terminal_status == Transaction.FAILED:
        _defer(txn, "reversal_alerted", reversal=True)
    elif terminal_status == Transaction.SUCCESS:
        _defer(txn, "whatsapp_alerted", reversal=False, whatsapp_only=True)


def _meta(txn) -> dict:
    """`meta` is a free-form JSONField, so a caller can and does put a bare string
    in it. This runs on every ledger write — it must not be the thing that raises."""
    value = getattr(txn, "meta", None)
    return value if isinstance(value, dict) else {}


@receiver(post_save, sender="wallet.Transaction", dispatch_uid="wallet_txn_alert")
def _alert_on_settled_transaction(sender, instance, **kwargs):
    # Callers often set only transaction_status on an instance whose `meta` was
    # loaded before mark_awaiting_settlement merged the provider response. Read
    # the database row so the pending marker and alert claims are authoritative.
    from .models import Transaction

    txn = Transaction.objects.filter(pk=instance.pk).first() or instance
    if _silent_transaction(txn):
        return

    # A reversal does not create a ledger row — `refund` and the disbursement
    # webhook flip the existing one to Failed and credit the balance back. So a
    # debit that settled and later reversed would alert once, saying money left,
    # and never say it came back. That is worse than not alerting at all.
    if txn.transaction_status == txn.FAILED:
        # "was this row already announced?" is answered against the DB inside
        # _fire, not here: the flag is written with .update(), so the in-memory
        # instance the caller is holding never sees it and would always say no.
        requires = "" if _meta(txn).get("wa_awaiting_settlement") else "alerted"
        _defer(txn, "reversal_alerted", reversal=True, requires=requires)
        return

    if txn.transaction_status != txn.SUCCESS:
        return
    _defer(txn, "alerted", reversal=False)


def _whatsapp_retry_due(txn, *, reversal: bool = False) -> bool:
    """Whether a terminal row still owes a WhatsApp transaction alert."""
    if not _alerts_on("whatsapp"):
        return False
    if reversal:
        return True
    meta = _meta(txn)
    if meta.get("channel") == "whatsapp" and not meta.get("wa_awaiting_settlement"):
        return False
    return True


def retry_pending_whatsapp_alerts(*, since=None, limit: int = 50) -> int:
    """Resume the bounded notification outbox from the existing worker hook.

    Every enabled channel now has independent durable state. Historical boolean
    claims carry no acceptance evidence and are not replayed or marked delivered.
    Expired preparation is safe to resume; expired dispatch needs review.
    """
    from .models import TransactionAlertDelivery
    from django.db.models import F, Q

    now = timezone.now()
    due = Q(state__in=(TransactionAlertDelivery.READY, TransactionAlertDelivery.RETRY)) & (
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
    stale = Q(state__in=(TransactionAlertDelivery.PREPARING, TransactionAlertDelivery.DISPATCHING),
              lease_expires_at__lte=now)
    # Fresh work has no next-attempt time and goes first. A long-lived missing
    # contact/route must not monopolize each bounded sweep and starve new alerts.
    qs = TransactionAlertDelivery.objects.filter(due | stale).order_by(
        F("next_attempt_at").asc(nulls_first=True), "created", "pk")
    # Enqueued jobs stay owed even when the transaction ages outside the worker's
    # lookback. `since` is retained for API compatibility, not used to drop work.
    return sum(_dispatch_alert(pk) for pk in qs.values_list("pk", flat=True)[:max(0, int(limit or 0))])


MAX_ALERT_ATTEMPTS = 5
ALERT_LEASE = timedelta(minutes=5)


def _enqueue_alert(txn_pk, *, reversal=False, requires="", whatsapp_only=False):
    """Persist channels inside the ledger transaction, before on_commit sends."""
    from .models import Transaction, TransactionAlertDelivery

    with db_transaction.atomic():
        txn = Transaction.objects.select_for_update().filter(pk=txn_pk).first()
        if txn is None or _silent_transaction(txn):
            return []
        meta = _meta(txn)
        if requires and not meta.get(requires) and not txn.alert_deliveries.filter(reversal=False).exists():
            return []
        if ((reversal and txn.transaction_status != txn.FAILED)
                or (not reversal and txn.transaction_status != txn.SUCCESS)):
            return []
        marker = "reversal_alerted" if reversal else "alerted"
        legacy_event = bool(meta.get(marker)) and not txn.alert_deliveries.filter(reversal=reversal).exists()
        channels = ("whatsapp",) if whatsapp_only else ("email", "sms", "push", "whatsapp")
        ids = []
        for channel in channels:
            if not _alerts_on(channel) or (channel == "whatsapp" and not _whatsapp_retry_due(txn, reversal=reversal)):
                continue
            # An old pre-send flag cannot prove acceptance or non-delivery. Surface
            # it for review rather than retroactively claiming a successful send.
            legacy = legacy_event or bool(meta.get(_whatsapp_claim_flag(reversal)) if channel == "whatsapp"
                                          else meta.get(marker))
            delivery, _created = TransactionAlertDelivery.objects.get_or_create(
                transaction=txn, reversal=reversal, channel=channel,
                defaults={"state": TransactionAlertDelivery.REVIEW if legacy else TransactionAlertDelivery.READY,
                          "error_code": "legacy_delivery_unknown" if legacy else ""},
            )
            ids.append(delivery.pk)
        merged = dict(meta)
        merged[marker] = True  # Event queued, not proof any channel accepted it.
        Transaction.objects.filter(pk=txn.pk).update(meta=merged)
        return ids


def _channel_available(txn, channel, *, reversal=False):
    """No dispatch/attempt is spent while a route or proved contact is absent."""
    from utility.providers import email_live, sms_live
    from whatsapp.models import WhatsAppLink
    from whatsapp.providers import wa_live

    local = settings.DEBUG or getattr(settings, "TESTING", False)
    if not _alerts_on(channel):
        return "channel_disabled"
    if channel == "email" and (not txn.user.email or not txn.user.email_verified):
        return "email_unverified"
    if channel == "sms" and (not txn.user.phone or not txn.user.phone_verified):
        return "phone_unverified"
    if not local and channel == "email" and not email_live():
        return "email_unconfigured"
    if not local and channel == "sms" and not sms_live():
        return "sms_unconfigured"
    if channel == "whatsapp":
        if not _whatsapp_retry_due(txn, reversal=reversal):
            return "chat_already_announced"
        if not WhatsAppLink.objects.filter(user_id=txn.user_id, status=WhatsAppLink.ACTIVE).exists():
            return "no_active_link"
        if not local and not wa_live():
            return "whatsapp_unconfigured"
    if channel == "push" and not txn.user.push_devices.filter(enabled=True).exists():
        return "no_push_device"
    return ""


def _prepare_alert(pk):
    from .models import TransactionAlertDelivery

    now = timezone.now()
    with db_transaction.atomic():
        # Enqueue takes ledger -> outbox locks. Lock only this outbox row while
        # reading the joined ledger/user, never acquire the inverse order.
        row = (TransactionAlertDelivery.objects.select_for_update(of=("self",))
               .select_related("transaction__user").filter(pk=pk).first())
        if row is None or row.state in (row.ACCEPTED, row.REVIEW, row.EXHAUSTED, row.SKIPPED):
            return None
        if row.state == row.DISPATCHING:
            if row.lease_expires_at and row.lease_expires_at <= now:
                row.state, row.error_code = row.REVIEW, "dispatch_lease_expired"
                row.save(update_fields=["state", "error_code", "updated"])
                log.warning("txn_alert_review delivery=%s channel=%s reason=dispatch_lease_expired", row.pk, row.channel)
            return None
        if row.state == row.PREPARING and row.lease_expires_at and row.lease_expires_at > now:
            return None
        if row.next_attempt_at and row.next_attempt_at > now:
            return None
        txn = row.transaction
        if (_silent_transaction(txn) or (row.reversal and txn.transaction_status != txn.FAILED)
                or (not row.reversal and txn.transaction_status != txn.SUCCESS)):
            row.state, row.error_code = row.SKIPPED, "outcome_changed"
            row.save(update_fields=["state", "error_code", "updated"])
            return None
        reason = _channel_available(txn, row.channel, reversal=row.reversal)
        if (row.channel == "whatsapp" and row.error_code == "template_unconfigured"
                and not (getattr(settings, "WHATSAPP", {}) or {}).get("TXN_ALERT_TEMPLATE")):
            reason = "template_unconfigured"
        if reason:
            row.state, row.error_code = row.READY, reason
            row.next_attempt_at = now + timedelta(seconds=60)
            row.save(update_fields=["state", "error_code", "next_attempt_at", "updated"])
            return None
        if row.attempts >= MAX_ALERT_ATTEMPTS:
            row.state, row.error_code = row.EXHAUSTED, "retry_limit"
            row.save(update_fields=["state", "error_code", "updated"])
            return None
        previous_state, previous_token = row.state, row.claim_token
        token = secrets.token_hex(32)
        # Conditional update closes the weaker-database race as well as the
        # production row lock. Only this token can cross the dispatch boundary.
        changed = TransactionAlertDelivery.objects.filter(
            pk=row.pk, state=previous_state, claim_token=previous_token,
        ).update(state=row.PREPARING, claim_token=token,
                 lease_expires_at=now + ALERT_LEASE, updated=now)
        if not changed:
            return None
        row.state, row.claim_token = row.PREPARING, token
        return row


def _send_alert_channel(row):
    from utility.providers import send_email, send_sms

    txn = row.transaction
    subject, body = _describe(txn, reversal=row.reversal)
    if row.channel == "email":
        return send_email(txn.user.email, subject, body, html=_email_alert_html(txn, reversal=row.reversal))
    if row.channel == "sms":
        return send_sms(txn.user.phone, _sms_alert(txn, reversal=row.reversal))
    if row.channel == "push":
        return _push_alert(txn, subject)
    return _deliver_claimed_whatsapp_alert(txn, reversal=row.reversal)


def _dispatch_alert(pk):
    from .models import TransactionAlertDelivery

    row = _prepare_alert(pk)
    if row is None:
        return False
    now = timezone.now()
    if not TransactionAlertDelivery.objects.filter(
            pk=row.pk, state=row.PREPARING, claim_token=row.claim_token,
    ).update(state=row.DISPATCHING, attempts=row.attempts + 1,
             lease_expires_at=now + ALERT_LEASE, updated=now):
        return False
    row.attempts += 1
    try:
        result = _send_alert_channel(row)
    except Exception:
        # An exception after the persisted boundary cannot prove non-delivery.
        result = {"success": False, "uncertain": True, "code": "dispatch_exception"}
    accepted = _finish_alert(row, result)
    if accepted and row.channel == "whatsapp":
        _set_accepted_whatsapp_flag(row.transaction_id, row.reversal)
    return accepted


def _finish_alert(row, result):
    from .models import TransactionAlertDelivery

    # bool results exist only in legacy test doubles. Real rails return bounded
    # outcome dictionaries; unknown/malformed results require review.
    if result is True:
        result = {"success": True}
    elif result is False:
        result = {"success": False, "uncertain": False}
    result = result if isinstance(result, dict) else {}
    now = timezone.now()
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    reference = result.get("message_id") or raw.get("id", "")
    if not isinstance(reference, str):
        reference = ""
    accepted = result.get("success") is True and result.get("uncertain") is not True
    missing_acceptance_id = (accepted and row.channel in ("email", "sms", "whatsapp")
                             and not reference.strip()
                             and not (settings.DEBUG or getattr(settings, "TESTING", False)))
    if missing_acceptance_id:
        accepted = False
    undispatched = result.get("not_dispatched") is True or (
        result.get("mock") and not (settings.DEBUG or getattr(settings, "TESTING", False)))
    if undispatched:
        state, reason = row.READY, str(result.get("code") or "route_unavailable")[:64]
        next_attempt = now + timedelta(seconds=60)
    elif accepted:
        state, reason, next_attempt = row.ACCEPTED, "", None
    elif missing_acceptance_id or result.get("uncertain") is True:
        state, reason, next_attempt = row.REVIEW, "dispatch_outcome_unknown", None
    elif (result.get("uncertain") is False or result.get("retryable") is True
          or (result.get("error_code") and result.get("uncertain") is not True)):
        state = row.EXHAUSTED if row.attempts >= MAX_ALERT_ATTEMPTS else row.RETRY
        reason = "retry_limit" if state == row.EXHAUSTED else "provider_refused"
        next_attempt = None if state == row.EXHAUSTED else now + timedelta(seconds=min(60 * 2**(row.attempts - 1), 3600))
    else:
        state, reason, next_attempt = row.REVIEW, "dispatch_outcome_unknown", None
    # A very slow accepted response can resolve our expired-dispatch review only
    # while it still carries the exact winning token. No new sender can claim it.
    changed = TransactionAlertDelivery.objects.filter(
        pk=row.pk, claim_token=row.claim_token, state__in=(row.DISPATCHING, row.REVIEW),
    ).update(state=state, error_code=reason, provider_reference=reference[:128],
             next_attempt_at=next_attempt, lease_expires_at=None,
             attempts=max(0, row.attempts - 1) if undispatched else row.attempts, updated=now)
    if changed and state in (row.REVIEW, row.EXHAUSTED):
        log.warning("txn_alert_attention delivery=%s channel=%s state=%s reason=%s", row.pk, row.channel, state, reason)
    return bool(changed and state == row.ACCEPTED)


def _set_accepted_whatsapp_flag(txn_pk, reversal):
    from .models import Transaction

    # The outbox already committed acceptance. This compatibility mirror is
    # deliberately separate: never take ledger -> outbox locks in reverse order.
    with db_transaction.atomic():
        txn = Transaction.objects.select_for_update().filter(pk=txn_pk).first()
        if txn:
            merged = dict(_meta(txn))
            merged[_whatsapp_claim_flag(reversal)] = True
            Transaction.objects.filter(pk=txn.pk).update(meta=merged)


def _defer(txn, flag: str, *, reversal: bool, requires: str = "",
           whatsapp_only: bool = False) -> None:
    """Queue durable channels, then dispatch after the ledger commits.

    `requires` accepts prior queued debit state or its legacy marker so an
    already announced debit can receive a distinct reversal notice.
    """

    deliveries = _enqueue_alert(txn.pk, reversal=reversal, requires=requires, whatsapp_only=whatsapp_only)

    # The durable outbox is already part of the ledger commit. An on_commit
    # callback still executes on the request/interactive WhatsApp thread; serial
    # email/SMS/Meta timeouts there could hide a committed payment from its user.
    # Production delivery belongs to the existing background worker sweep.
    if not settings.DEBUG and not getattr(settings, "TESTING", False):
        return

    def _fire():
        try:
            for delivery_pk in deliveries:
                _dispatch_alert(delivery_pk)
        except Exception:  # noqa: BLE001 — an alert must never break a payment
            log.warning("txn_alert_callback_failed ref=%s flag=%s", txn.reference, flag)

    db_transaction.on_commit(_fire)


#: Nigerian bank alerts all follow one shape, and customers read them by
#: position rather than by reading them: direction and amount on line one, the
#: masked account, the description, the balance, the timestamp. Matching it
#: means a Zitch alert is scanned the same way as the one from their bank
#: sitting directly above it, instead of asking them to learn a second format.
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


def _transaction_account_number(txn) -> str:
    """Resolve this movement's account without relabelling historical funds."""
    from .models import BillFundingBinding, Wallet
    from wema_vas.models import Receipt, VirtualAccount

    try:
        receipt = Receipt.objects.select_related("account").filter(transaction_id=txn.pk).first()
        if receipt is not None:
            account = receipt.account
            if (txn.direction == txn.IN and txn.currency == "NGN" and receipt.amount == txn.amount
                    and account.user_id == txn.user_id and account.mode == VirtualAccount.LIVE
                    and receipt.state == Receipt.CREDITED and not account.number.startswith("711")):
                return account.number
            return ""
        binding = BillFundingBinding.objects.select_related("vas_account").filter(transaction_id=txn.pk).first()
        if binding is not None:
            if txn.direction != txn.OUT or txn.currency != "NGN":
                return ""
            if binding.vas_account_id:
                account = binding.vas_account
                if (account.user_id == txn.user_id and account.mode == VirtualAccount.LIVE
                        and not account.number.startswith("711")):
                    return account.number
                return ""
            number = binding.source_account
        else:
            # A late legacy credit belongs to its retained account, even after
            # the customer has a live VAS account. Mutable metadata is not proof.
            number = Wallet.objects.filter(user_id=txn.user_id).values_list("account_number", flat=True).first()
        return number if number and not number.startswith("711") else ""
    except Exception:  # noqa: BLE001 — omit unknown provenance; never guess a funding account
        return ""


def _sms_alert(txn, *, reversal: bool = False) -> str:
    """The alert in the bank's own format.

    A reversal is a CR whatever the row's direction says — the customer is being
    given money back, and calling it a debit because the original was one would
    be the single most alarming way to phrase good news.
    """
    from .services import get_or_create_wallet, wallet_balance_payload

    credit = reversal or txn.direction == txn.IN
    balance_label = "Bal"
    try:
        wallet = get_or_create_wallet(txn.user)
        balances = wallet_balance_payload(txn.user, wallet=wallet)
        balance = _sms_money(balances["available_balance"])
        if balances["available_balance"] != balances["balance"] or balances["historical_balance"]:
            balance_label = "Avail bills"
    except Exception:  # noqa: BLE001 — an alert must never depend on reading a wallet
        balance = ""
    account = _transaction_account_number(txn)

    desc = (_meta(txn).get("recipient_name") or _meta(txn).get("counterparty")
            or (txn.service or "").strip() or ("Credit" if credit else "Debit"))
    if reversal:
        desc = f"REVERSAL-{desc}"

    head = (f"{'CR' if credit else 'DR'}:{_sms_money(txn.amount, txn.currency)}\n"
            f"Acct No:{_mask_account(account)}\n")
    tail = f"\n{balance_label} :{balance}\n{_alert_timestamp(txn.created, '%d-%m-%Y %H:%M:%S')}"
    # Trim the description rather than the balance or the timestamp: those are
    # what the customer checks, and a second segment costs a second message.
    room = _SMS_MAX - len(head) - len(tail) - len("Desc :")
    return head + "Desc :" + desc[:max(room, 0)].strip() + tail


def _email_alert_html(txn, *, reversal: bool = False) -> str:
    """The alert as a bank-standard card inside the shared brand shell — same
    header, same footer (team sign-off, contact points, socials) as every other
    Zitch email. The plain-text body stays as the fallback, so clients that
    refuse HTML lose the layout and nothing else."""
    from common.emails import email_shell

    credit = reversal or txn.direction == txn.IN
    word = "Reversal" if reversal else ("Credit" if credit else "Debit")
    colour = "#0f9c93" if credit else "#b8402f"
    sign = "+" if credit else "\u2212"

    account = _mask_account(_transaction_account_number(txn))
    balance_rows = _balance_rows(txn)

    counterparty = (_meta(txn).get("recipient_name") or _meta(txn).get("counterparty")
                    or (txn.service or "").strip() or word)
    first = (txn.user.first_name or "").strip().title() or "there"

    def row(label, value, bold=False):
        weight = "600" if bold else "400"
        return (f'<tr><td style="padding:7px 0;color:#8fa3a0;font-size:13px;'
                f'font-family:Arial,Helvetica,sans-serif">{escape(str(label))}</td>'
                f'<td align="right" style="padding:7px 0;color:#12201f;font-size:13px;'
                f'font-weight:{weight};font-family:Arial,Helvetica,sans-serif">{escape(str(value))}</td></tr>')

    content = f"""
  <tr><td style="padding:28px 28px 6px;font-family:Arial,Helvetica,sans-serif">
    <p style="margin:0 0 4px;color:#8fa3a0;font-size:12px;letter-spacing:.12em;
              text-transform:uppercase">{word} alert</p>
    <p style="margin:0;color:{colour};font-size:32px;font-weight:700">
      {sign}{_money(txn.amount, txn.currency)}</p>
    <p style="margin:10px 0 0;color:#5f7370;font-size:14px">Hi {escape(first)}, here are the details:</p>
  </td></tr>
  <tr><td style="padding:14px 28px 4px;font-family:Arial,Helvetica,sans-serif">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="border-top:1px solid #e4ecea">
      {row("Description", counterparty)}
      {row("Account", account)}
      {row("Reference", txn.reference)}
      {row("Date", _alert_timestamp(txn.created, "%d %b %Y, %I:%M %p"))}
      {"".join(row(label, value, bold=True) for label, value in balance_rows)}
    </table>
  </td></tr>
  <tr><td style="padding:16px 28px 26px;font-family:Arial,Helvetica,sans-serif">
    <p style="margin:0;padding:12px 14px;background:#eef4f3;border-radius:8px;
              color:#5f7370;font-size:12px;line-height:1.5">
      Didn\u2019t make this transaction? Contact
      <a href="mailto:support@zitch.ng" style="color:#0a6b65">support@zitch.ng</a> immediately.
    </p>
  </td></tr>"""
    return email_shell(content,
                       preheader=f"{word} of {_money(txn.amount, txn.currency)} on your Zitch account")
