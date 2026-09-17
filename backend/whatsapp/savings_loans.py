"""WhatsApp access to the existing Fixed Save and loan repayment ledgers.

Non-secret choices are collected in chat; only the existing encrypted PIN Flow
can authorise a debit. No new borrowing, early withdrawal or bank product is
implied by these routes. Executors reuse the app's atomic ledger services.
"""
import json
import re
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from common.http import parse_amount, stale_pin_error
from loans.models import Loan
from loans.services import repay
from savings.models import FixedSave
from savings.services import lock, settle_user_maturities
from wallet.models import Transaction
from wallet.services import InsufficientFunds, existing_for_key, get_or_create_wallet

from .models import ConversationState, PendingAction, WhatsAppLink


def _router():
    # The router loads its executors after defining the shared Flow helpers.
    from . import router
    return router


def _confirmation_stamp(pa, owner, link):
    """Bind the quoted action to the current credentials and link generation.

    Only this keyed digest is saved, never a PIN or reusable credential hash.
    PIN/password changes or unlink/relink invalidate an unexecuted quote.
    """
    data = [owner.pk, owner.transaction_pin, owner.password, owner.phone, owner.email,
            link.pk, str(link.created), str(link.linked_at), pa.pk, pa.msisdn, pa.action_type,
            {key: pa.payload.get(key) for key in ("amount", "days", "rate", "interest", "loan_ref", "outstanding")}]
    return salted_hmac("whatsapp.ledger-product-confirmation", json.dumps(data, sort_keys=True)).hexdigest()


def _read_allowed(user, msisdn, resume):
    r = _router()
    if not user.is_active or not WhatsAppLink.objects.filter(
        user=user, wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE,
    ).exists():
        r.reply(msisdn, "Your account is not connected for this request. Reply menu for help.")
        return False
    if r._needs_reauth(ConversationState.for_msisdn(msisdn)):
        r._send_unlock(user, msisdn, resume)
        return False
    return True


def _amount(text):
    """Bounded shorthand, rejecting negatives, sub-kobo and oversized values."""
    raw = str(text).strip().lower()
    if len(raw) > 32:
        return None
    raw = re.sub(r"^(?:₦|ngn)\s*", "", raw)
    match = re.fullmatch(r"(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*([km]?)", raw)
    if not match:
        return None
    try:
        value = Decimal(match[1].replace(",", "")) * {"": 1, "k": 1000, "m": 1000000}[match[2]]
    except InvalidOperation:
        return None
    value = parse_amount(value)
    return value if value is not None and Decimal("0") < value <= Decimal("999999999999.99") else None


def show_savings(user, msisdn, *, page=1, history=False):
    r = _router()
    command = f"savings {'history' if history else 'page'} {page}"
    if not _read_allowed(user, msisdn, command):
        return
    try:
        settle_user_maturities(user)
    except Exception:
        r.log.exception("wa_savings_maturity_read_failed user=%s", user.pk)
        return r.reply(msisdn, "I couldn't confirm your savings payouts right now. Please try savings again shortly.")
    rows = user.savings.all()
    if not history:
        rows = rows.filter(status=FixedSave.ACTIVE)
    rows = rows.order_by("-created", "-pk")
    count = rows.count()
    if page > max(1, (count + 4) // 5):
        return r.reply(msisdn, "That savings page is unavailable. Reply savings to see your active plans.")
    total = user.savings.filter(status=FixedSave.ACTIVE).aggregate(total=Sum("principal"))["total"] or Decimal("0")
    lines = ["🏦 *Your Zitch savings*", f"Locked: {r._money(total)}"]
    if not count:
        lines.append("You don't have any saved plans yet." if history else "You don't have any active Zitch savings right now.")
    for plan in rows[(page - 1) * 5:page * 5]:
        state = "paid out" if plan.paid_out else f"matures {timezone.localtime(plan.matures_at):%d %b %Y}"
        lines.append(f"• {r._money(plan.principal)} — {state}\n  Details: savings plan {plan.pk}")
    if count > page * 5:
        lines.append(f"Next page: savings {'history' if history else 'page'} {page + 1}")
    lines.append("Reply *new savings* to create a Fixed Save, *savings rates* for terms, "
                 "or *savings history* for all plans. Matured plans pay into your wallet automatically.")
    return r.reply(msisdn, "\n\n".join(lines))


def show_plan(user, msisdn, plan_id):
    r = _router()
    if not _read_allowed(user, msisdn, f"savings plan {plan_id}"):
        return
    # Ownership is in the query; a guessed ID never selects another user's plan.
    plan = user.savings.filter(pk=plan_id).first()
    if plan is None:
        return r.reply(msisdn, "That plan isn't available on your account. Reply savings to see your plans.")
    try:
        settle_user_maturities(user)
    except Exception:
        r.log.exception("wa_savings_plan_payout_failed user=%s", user.pk)
        return r.reply(msisdn, "I couldn't confirm this plan's payout. Please check savings again shortly.")
    plan.refresh_from_db()
    state = "Paid into your wallet" if plan.paid_out else "Locked until maturity"
    return r.reply(msisdn,
        f"🏦 *Fixed Save*\nRef {plan.reference}\nPrincipal: {r._money(plan.principal)}\n"
        f"Annual rate: {plan.rate * 100:g}%\nInterest: {r._money(plan.interest)}\n"
        f"Maturity value: {r._money(plan.maturity_value)}\n"
        f"Matures: {timezone.localtime(plan.matures_at):%d %b %Y}\nStatus: {state}\n\n"
        "Existing plans cannot be topped up, changed or withdrawn early. "
        "Reply new savings for a separate plan, or savings history for past plans.")


def show_rates(msisdn):
    r = _router()
    lines = [f"• {days} days: {rate * 100:g}% per year" for days, rate in sorted(FixedSave.RATES.items())]
    return r.reply(msisdn, "🏦 *Fixed Save terms*\n" + "\n".join(lines)
        + f"\nMinimum: {r._money(FixedSave.MIN_PRINCIPAL)}. Interest is prorated for the lock period. "
          "Funds stay locked until maturity; no early withdrawal or changes. "
          "Reply new savings to review your quote before confirming privately with your PIN.")


def start_savings(user, msisdn):
    r = _router()
    r._new_flow(user, msisdn, "savings_create", "amount", {})
    return r.reply(msisdn, f"How much would you like to lock in Fixed Save? Minimum {r._money(FixedSave.MIN_PRINCIPAL)}. "
                   "For example, 5k. Funds cannot be withdrawn early. Reply cancel to stop.")


def show_loan(user, msisdn):
    r = _router()
    if not _read_allowed(user, msisdn, "my loan"):
        return
    loan = user.loans.filter(status=Loan.ACTIVE).first()
    if loan is None:
        return r.reply(msisdn, "You don't have an active Zitch loan right now. "
                       "New loan applications aren't available in this WhatsApp flow.")
    overdue = " — *overdue*" if loan.due_date < timezone.now() else ""
    return r.reply(msisdn,
        f"💳 *Your Zitch loan*\nOutstanding: {r._money(loan.outstanding)}\n"
        f"Borrowed: {r._money(loan.principal)} · repaid {r._money(loan.amount_repaid)}\n"
        f"Due: {timezone.localtime(loan.due_date):%d %b %Y}{overdue}\nRef {loan.reference}\n\n"
        "Reply *repay loan* to repay from your wallet here. You'll confirm privately with your PIN.")


def start_repayment(user, msisdn):
    r = _router()
    if not _read_allowed(user, msisdn, "repay loan"):
        return
    loan = user.loans.filter(status=Loan.ACTIVE).first()
    if loan is None:
        return show_loan(user, msisdn)
    r._new_flow(user, msisdn, "loan_repay", "amount", {"loan_ref": loan.reference})
    return r.reply(msisdn, f"Your outstanding loan is {r._money(loan.outstanding)}. "
                   "How much would you like to repay from your wallet? Reply full for the full balance, "
                   "or enter an amount. Reply cancel to stop.")


def _confirm(pa, user):
    """These new routes require a private Flow; never fall back to a chat PIN."""
    r = _router()
    user.refresh_from_db()
    if not user.transaction_pin or user.pin_reset_required:
        PendingAction.objects.filter(pk=pa.pk).delete()
        return r.reply(pa.msisdn, "Reply *reset pin* to set your transaction PIN, then start this request again.")
    link = WhatsAppLink.objects.filter(user=user, wa_msisdn=pa.msisdn, status=WhatsAppLink.ACTIVE).first()
    if not user.is_active or link is None or user.pin_locked:
        PendingAction.objects.filter(pk=pa.pk).delete()
        return r.reply(pa.msisdn, "This account cannot confirm the request right now. Reply menu for help.")
    pa.payload["confirmation_stamp"] = _confirmation_stamp(pa, user, link)
    r._touch(pa, payload=pa.payload)
    if r.flows_live() and r._send_pin_flow(pa, user):
        return
    # _send_pin_flow already explains a live balance shortfall and removes it.
    if PendingAction.objects.filter(pk=pa.pk).exists():
        PendingAction.objects.filter(pk=pa.pk).delete()
        return r.reply(pa.msisdn, "Private PIN confirmation is unavailable right now. "
                       "No request was submitted. Please try again later; never type your PIN in chat.")


def advance_product(pa, user, msisdn, text):
    r = _router()
    if pa.action_type == "savings_create" and pa.state == "days":
        raw = text.strip()
        days = int(raw) if re.fullmatch(r"\d{1,3}", raw) else None
        if days not in FixedSave.RATES:
            return r._reroute_or_reprompt(pa, msisdn, text, "Choose a lock period: "
                                           + ", ".join(map(str, sorted(FixedSave.RATES))) + " days.")
        amount = _amount(pa.payload.get("amount", ""))
        if amount is None or amount < FixedSave.MIN_PRINCIPAL:
            PendingAction.objects.filter(pk=pa.pk).delete()
            return r.reply(msisdn, "This quote is invalid. Reply new savings to start again.")
        pa.payload.update(days=days, rate=str(FixedSave.RATES[days]), interest=str(FixedSave.quote(amount, days)))
        r._touch(pa, payload=pa.payload)
        return _confirm(pa, user)
    if pa.state != "amount":
        PendingAction.objects.filter(pk=pa.pk).delete()
        return r.reply(msisdn, "Start this request again and use the private PIN screen to confirm.")
    loan = None
    if pa.action_type == "loan_repay":
        loan = user.loans.filter(reference=pa.payload.get("loan_ref"), status=Loan.ACTIVE).first()
        if loan is None:
            PendingAction.objects.filter(pk=pa.pk).delete()
            return r.reply(msisdn, "This loan no longer has an active repayment request. Reply my loan for its status.")
    amount = loan.outstanding if loan is not None and text.strip().lower() == "full" else _amount(text)
    if amount is None:
        return r._reroute_or_reprompt(pa, msisdn, text, "Enter a positive amount with at most two decimal places, e.g. 5000 or 5k.")
    if loan is not None and amount > loan.outstanding:
        return r.reply(msisdn, f"Your outstanding balance is {r._money(loan.outstanding)}. Enter that amount or less, or reply full.")
    if loan is None and amount < FixedSave.MIN_PRINCIPAL:
        return r.reply(msisdn, f"The minimum Fixed Save amount is {r._money(FixedSave.MIN_PRINCIPAL)}.")
    pa.payload["amount"] = str(amount)
    if loan is not None:
        pa.payload["outstanding"] = str(loan.outstanding)
        r._touch(pa, payload=pa.payload)
        return _confirm(pa, user)
    r._touch(pa, state="days", payload=pa.payload)
    terms = "; ".join(f"{days} days at {rate * 100:g}% per year" for days, rate in sorted(FixedSave.RATES.items()))
    return r.reply(msisdn, "Choose the number of days to lock your savings: " + terms
                   + ". Interest is prorated for the lock period. Reply with the number of days.")


def confirmation_fields(pa):
    r = _router()
    p = pa.payload
    amount = Decimal(p["amount"])
    if pa.action_type == "loan_repay":
        return {"amount": r._money(amount), "recipient": "Loan repayment from your wallet",
                "details": f"Loan {p['loan_ref']}. Outstanding at quote: {r._money(Decimal(p['outstanding']))}. "
                           "If the balance falls before execution, only the amount still owed is repaid."}
    return {"amount": r._money(amount), "recipient": f"Fixed Save — {p['days']} days",
            "details": f"Annual rate {Decimal(p['rate']) * 100:g}%. Interest {r._money(Decimal(p['interest']))}. "
                       f"Maturity value {r._money(amount + Decimal(p['interest']))}. No early withdrawal or changes."}


def _execute(pa, user):
    """Serialise local product writes, recheck ownership, and replay the ledger.

    No network call occurs within this transaction. The app's service owns the
    balance/loan/plan changes; its ledger uniqueness is the duplicate backstop.
    """
    r = _router()
    with transaction.atomic():
        live = PendingAction.objects.select_for_update().filter(
            pk=pa.pk, user=user, msisdn=pa.msisdn, action_type=pa.action_type,
            state__in=(r.FLOW_PIN_STATE, r.EXECUTING_STATE),
        ).first()
        if live is None or (live.expired and live.state != r.EXECUTING_STATE):
            return "This request expired or was cancelled. Start again in the chat.", r.OUTCOME_FAILED
        pa = live
        owner = get_user_model().objects.select_for_update().get(pk=user.pk)
        link = WhatsAppLink.objects.select_for_update().filter(
            user=owner, wa_msisdn=pa.msisdn, status=WhatsAppLink.ACTIVE,
        ).first()
        if not owner.is_active or link is None:
            return "This account is no longer connected for this request. Check your account history for its status.", r.OUTCOME_FAILED
        key = f"wa-{pa.pk}"
        prior = existing_for_key(owner, key)
        if prior is not None:
            if prior.transaction_status != Transaction.SUCCESS:
                return "This request has no confirmed successful result. Check your history before retrying.", r.OUTCOME_PENDING
            # A replay never falls through to a new debit, even when the loan
            # was fully repaid or the wallet can no longer cover the old amount.
            return f"Already completed: {r._money(prior.amount)}. Ref {prior.reference}. Reply savings or my loan for details.", r.OUTCOME_SUCCESS
        if pa.expired:
            return "This request expired before it could execute. Start a fresh request in the chat.", r.OUTCOME_FAILED
        if (not owner.transaction_pin or owner.pin_locked or stale_pin_error(owner)
                or not constant_time_compare(str(pa.payload.get("confirmation_stamp") or ""),
                                             _confirmation_stamp(pa, owner, link))):
            return "Your account security or request details changed. Start a fresh request and confirm with your current PIN.", r.OUTCOME_FAILED
        amount = _amount(pa.payload.get("amount", ""))
        if amount is None:
            return "This amount is invalid. Start a fresh request in the chat.", r.OUTCOME_FAILED
        if r.velocity_exceeded(owner):
            return "Too many recent transactions. Please wait a few minutes before trying again.", r.OUTCOME_FAILED
        get_or_create_wallet(owner)
        if pa.action_type == "savings_create":
            days = pa.payload.get("days")
            if (isinstance(days, bool) or not isinstance(days, int) or days not in FixedSave.RATES
                    or amount < FixedSave.MIN_PRINCIPAL
                    or pa.payload.get("rate") != str(FixedSave.RATES[days])
                    or pa.payload.get("interest") != str(FixedSave.quote(amount, days))):
                return "Savings terms changed or this quote is invalid. Reply new savings to review a fresh quote.", r.OUTCOME_FAILED
            plan = lock(owner, amount, days, idempotency_key=key)
            return (f"✅ Fixed Save created: {r._money(plan.principal)} for {days} days.\n"
                    f"Maturity value: {r._money(plan.maturity_value)} on {timezone.localtime(plan.matures_at):%d %b %Y}.\n"
                    f"Ref {plan.reference}\nReply savings plan {plan.pk} for details.", r.OUTCOME_SUCCESS)
        loan = Loan.objects.select_for_update().filter(user=owner, reference=pa.payload.get("loan_ref")).first()
        if loan is None:
            return "That loan isn't available on your account. No repayment was taken.", r.OUTCOME_FAILED
        if loan.status != Loan.ACTIVE or loan.outstanding <= 0:
            return "This loan is already repaid. No further repayment was taken.", "done"
        loan = repay(owner, loan, amount, idempotency_key=key)
        paid = existing_for_key(owner, key)
        return (f"✅ Loan repayment successful: {r._money(paid.amount)}.\n"
                f"Outstanding: {r._money(loan.outstanding)}\nRef {paid.reference}\n"
                "Reply my loan to review your loan.", r.OUTCOME_SUCCESS)


def execute_product(pa, user, msisdn):
    r = _router()
    try:
        message, status = _execute(pa, user)
    except InsufficientFunds:
        message, status = "Insufficient wallet balance. No savings lock or loan repayment was taken.", r.OUTCOME_FAILED
    # Failures such as an unavailable database propagate to the existing durable
    # queue. It retains the authorised action and retries the SAME ledger key.
    r.reply(msisdn, message)
    return r.Outcome(message, status)
