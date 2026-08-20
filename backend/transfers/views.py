"""Bank transfer (payout) endpoints + saved beneficiaries.

Payout to external banks runs on Wema/ALAT; until keys are set this runs in MOCK
mode in dev/tests and resolves/settles automatically so the flow is testable (in
production it fails closed). Money still moves correctly out of the wallet ledger.
Wema exposes NO payout webhook — a PENDING transfer is settled/reversed by the
reconcile_wema poller (utility/management/commands/reconcile_wema.py).
"""
import re

from common.http import (
    MIN_TRANSFER, api, check_daily_limit, check_send_limits, fail, idempotent_replay, ok,
    parse_amount, require_user, spend_key, verify_transaction_pin,
)
from common.ratelimit import ratelimit
from utility.providers import payout_charge, payout_resolve_account
from wallet.models import Transaction
from wallet.services import existing_for_key

from .models import Bank
from .services import PayoutError, detect_account_banks, execute_payout


def _name_tokens(name: str) -> set:
    """Significant word tokens of a holder name, lowercased (drops 1-char bits
    and common prefixes), for tolerant comparison."""
    drop = {"mr", "mrs", "ms", "dr", "miss"}
    toks = re.sub(r"[^a-z ]", " ", (name or "").lower()).split()
    return {t for t in toks if len(t) > 1 and t not in drop}


def _names_match(shown: str, resolved: str) -> bool:
    """Whether the holder name the user confirmed matches the freshly-resolved
    account holder. Tolerant of word order, middle names and prefixes: matches if
    they share >=2 tokens, or one name's tokens are a subset of the other's."""
    a, b = _name_tokens(shown), _name_tokens(resolved)
    if not a or not b:
        return False
    return len(a & b) >= 2 or a <= b or b <= a


@api
def list_banks(request):
    """POST /api/transfers/banks/ -> {banks: [{code, name, color, logo}]}

    Popular (high-volume) banks lead the list so the picker shows the banks
    almost everyone sends to before the long alphabetical tail.
    """
    banks = Bank.objects.filter(active=True).order_by("-popular", "name")
    aliases = {
        "gtb": ["GT", "GTBank"], "uba": ["UBA"], "fcmb": ["FCMB"],
        "access": ["Access"], "zenith": ["Zenith"], "first": ["FirstBank", "First"],
        "fidelity": ["Fidelity"], "sterling": ["Sterling"], "kuda": ["Kuda"],
        "opay": ["OPay"], "palmpay": ["PalmPay"], "moniepoint": ["Moniepoint"],
    }
    return ok(banks=[{"code": b.code, "name": b.name, "aliases": aliases.get(b.code, []),
                      "color": b.color, "logo": b.logo} for b in banks])


def _beneficiary(b) -> dict:
    """One recipient, as the app reads it.

    The first six keys are what shipped builds already consume, with the values
    they already have — `name` stays the bank's holder name and `initials` stays
    derived from it. Everything after is additive, so a phone that has not
    updated keeps rendering exactly what it renders today.
    """
    return {
        "id": b.id, "name": b.name, "account_number": b.account_number,
        "bank_name": b.bank_name, "initials": b.initials, "color": b.color,
        "bank_code": b.bank_code, "nickname": b.nickname,
        "display_name": b.display_name, "saved": b.saved,
        "transfer_count": b.transfer_count,
        "frequent": b.transfer_count >= 3,
    }


def clean_nickname(raw) -> tuple:
    """(nickname, error). Shared with the WhatsApp rail so one label is legal in
    both places — a name a customer sets in the app has to be retypable in chat.

    Whitespace is collapsed rather than merely stripped, because "Mum " and
    "Mum" looking identical while comparing unequal is exactly how a nickname
    becomes ambiguous later, and an ambiguous name is one we refuse to pay.
    """
    text = " ".join(str(raw or "").split())
    if not text:
        return "", ""
    if len(text) > 80:
        return "", "That name is too long. Try something shorter."
    if text.isdigit():
        # A nickname of digits is indistinguishable from an account number in a
        # chat message, where both arrive as bare text.
        return "", "Give them a name rather than a number."
    return text, ""


@api
@require_user
def list_beneficiaries(request):
    """POST /api/transfers/beneficiaries/ {access_token}
    -> {beneficiaries: [{id, name, account_number, bank_name, initials, color,
                         bank_code, nickname, display_name, saved}]}

    Every recipient, saved and unsaved alike. Deliberately unfiltered: a saved
    recipient IS a recent, and the send screen's fast path — filling the bank and
    holder name for an account the customer has paid before, with no name-enquiry
    round trip — reads this same list.
    """
    items = request.user_obj.beneficiaries.filter(saved=True)
    frequent = request.user_obj.beneficiaries.filter(transfer_count__gte=3).order_by("-created")
    return ok(
        beneficiaries=[_beneficiary(b) for b in items],
        frequent_recipients=[_beneficiary(b) for b in frequent],
    )


def _own_beneficiary(request):
    """(row, error_response). Always resolved through the caller's own related
    manager, so a guessed id belonging to somebody else is a 404 and never a
    read of another customer's recipient."""
    try:
        pk = int(request.data.get("beneficiary_id") or 0)
    except (TypeError, ValueError):
        pk = 0
    row = request.user_obj.beneficiaries.filter(pk=pk).first() if pk else None
    if row is None:
        return None, fail("That recipient is no longer in your list.", status=404)
    return row, None


@api
@require_user
def save_beneficiary(request):
    """POST /api/transfers/beneficiaries/save/ {access_token, beneficiary_id, nickname?}
    -> {success, beneficiary}

    Keeps a recipient the customer has already paid. There is no way to add an
    account that was never paid: rows in this table are what the send screen
    treats as proof that money once reached that account, and manufacturing one
    would put an unverified account under a heading that reads "Sent before".
    """
    row, error = _own_beneficiary(request)
    if error:
        return error
    nickname, problem = clean_nickname(request.data.get("nickname"))
    if problem:
        return fail(problem)
    if nickname and request.user_obj.beneficiaries.filter(
            nickname__iexact=nickname).exclude(pk=row.pk).exists():
        return fail(f"You already have someone called {nickname}.", status=409)
    row.nickname = nickname or row.nickname
    row.saved = True
    row.save(update_fields=["nickname", "saved"])
    return ok(success=True, beneficiary=_beneficiary(row))


@api
@require_user
def rename_beneficiary(request):
    """POST /api/transfers/beneficiaries/rename/ {access_token, beneficiary_id, nickname}
    -> {success, beneficiary}

    An empty nickname clears the label and leaves the recipient saved under the
    bank's holder name. Naming someone is itself an act of keeping them, so a
    rename saves an unsaved row rather than quietly labelling a recent that the
    customer would then not find in their address book.
    """
    row, error = _own_beneficiary(request)
    if error:
        return error
    nickname, problem = clean_nickname(request.data.get("nickname"))
    if problem:
        return fail(problem)
    if nickname and request.user_obj.beneficiaries.filter(
            nickname__iexact=nickname).exclude(pk=row.pk).exists():
        return fail(f"You already have someone called {nickname}.", status=409)
    row.nickname = nickname
    row.saved = row.saved or bool(nickname)
    row.save(update_fields=["nickname", "saved"])
    return ok(success=True, beneficiary=_beneficiary(row))


@api
@require_user
def delete_beneficiary(request):
    """POST /api/transfers/beneficiaries/delete/ {access_token, beneficiary_id}
    -> {success}

    Removes the row outright rather than only clearing `saved`. The customer
    asked for the recipient to be gone, and leaving it behind as a recent would
    keep it visible on the send screen and keep the account answering to the
    typed-account fast path — which is not what "remove" means to anyone.
    """
    row, error = _own_beneficiary(request)
    if error:
        return error
    row.delete()
    return ok(success=True)


@api
@ratelimit("resolve_account", limit=20, window=60)
@require_user
def resolve_account(request):
    """POST /api/transfers/resolve/ {access_token, account_number, bank?}
    -> {success, name, bank, bank_name, matches}

    With ``bank`` (our slug) it resolves at that one bank. WITHOUT it, the bank is
    auto-detected: a name-enquiry runs across the active banks and the match(es)
    are returned, so the app fills the bank in automatically once a 10-digit
    account number is typed (``matches`` lists every hit; usually exactly one).
    """
    acct = (request.data.get("account_number") or "").strip()
    if len(acct) != 10 or not acct.isdigit():
        return fail("Enter a valid 10-digit account number")

    bank_slug = str(request.data.get("bank", "") or "").strip()
    if bank_slug:  # explicit bank (manual pick / override) — resolve at just that one
        bank = Bank.objects.filter(code=bank_slug).first()
        if bank is None:
            return fail("Select a bank", status=404)
        res = payout_resolve_account(acct, bank.bank_code)
        if not res.get("success"):
            return fail(res.get("message", "Could not verify this account number"), status=400)
        return ok(success=True, name=res.get("name", ""), bank=bank.code, bank_name=bank.name,
                  mock=bool(res.get("mock")),
                  matches=[{"bank": bank.code, "bank_name": bank.name, "name": res.get("name", "")}])

    matches = detect_account_banks(acct)  # auto-detect across banks
    if not matches:
        return fail("Couldn't detect the bank for this account number — pick the bank manually.", status=404)
    top = matches[0]
    # `mock` => the name-enquiry rail isn't configured and `top` is a placeholder,
    # not a real detection. The app must not silently auto-fill it as verified.
    return ok(success=True, name=top["name"], bank=top["bank"], bank_name=top["bank_name"],
              mock=bool(top.get("mock")), matches=matches)


@api
@require_user
def transfer_charge(request):
    """POST /api/transfers/charge/ {access_token, amount} -> {fee}

    The NIP fee the bank levies on an inter-bank transfer of ``amount`` (from Wema's
    GetNIPCharges schedule). Informational — for display before the user confirms; the
    send flow itself is unchanged. Returns "0.00" when the schedule isn't available."""
    amount = parse_amount(request.data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    fee = payout_charge(amount)
    return ok(success=True, amount=str(amount), fee=str(fee) if fee is not None else "0.00")


@api
@ratelimit("bank_transfer", limit=12, window=60)
@require_user
def bank_transfer(request):
    """POST /api/transfers/send/
    {access_token, account_number, bank, name, amount, note?, transaction_pin}
    -> {success, wallet, reference}
    """
    user, data = request.user_obj, request.data

    pin_err = verify_transaction_pin(user, data.get("transaction_pin"))
    if pin_err:
        return pin_err

    acct = (data.get("account_number") or "").strip()
    if len(acct) != 10:
        return fail("Enter a valid 10-digit account number")
    bank = Bank.objects.filter(code=str(data.get("bank", ""))).first()
    if bank is None:
        return fail("Select a bank", status=404)

    amount = parse_amount(data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    if amount < MIN_TRANSFER:
        return fail(f"Minimum transfer is ₦{MIN_TRANSFER:,.0f}")

    limit_err = check_send_limits(user, amount)
    if limit_err:
        return limit_err

    key = spend_key(data.get("idempotency_key"), user, "bank", acct, bank.code, amount)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay

    # Daily transfer cap (after replay so a retried transfer replays cleanly).
    daily_err = check_daily_limit(user, amount, "transfer")
    if daily_err:
        return daily_err

    note = data.get("note", "")
    # Resolve server-side for the authoritative account name (name enquiry at the
    # submitted bank), then ENFORCE that it matches the holder the user confirmed
    # in the app. Routing is purely by {account_number, bank_code}, so without this
    # a stale/auto-detected/wrong bank could send to a different real person while
    # the app showed the expected name — money leaves to the wrong account.
    resolved = payout_resolve_account(acct, bank.bank_code)
    if not resolved.get("success"):
        return fail(resolved.get("message", "Could not verify this account number"), status=400)
    resolved_name = (resolved.get("name") or "").strip()
    shown_name = (data.get("name") or "").strip()
    # Only enforce on a LIVE enquiry. In mock mode (no live name-enquiry) the
    # resolved name is a fixed stub, so comparing it would false-block.
    if (not resolved.get("mock") and shown_name and resolved_name
            and not _names_match(shown_name, resolved_name)):
        # Block: the account resolves to someone other than who the user confirmed.
        return fail(
            f"This account belongs to {resolved_name}, not {shown_name}. "
            "Re-check the account number and bank before sending.",
            status=409, code="account_mismatch", resolved_name=resolved_name)
    name = resolved_name or shown_name or "Bank recipient"

    try:
        txn = execute_payout(user, amount, acct, bank, name, note=note,
                             idempotency_key=key, channel="app")
    except PayoutError as exc:
        if exc.kind == "duplicate":
            # Try to replay the original outcome (idempotent_replay returns ok(success=True, duplicate=True)).
            # If the prior row isn't found yet (race between debit write and this read),
            # return a clear duplicate signal with duplicate=True so the frontend treats it
            # as "already processed" instead of showing "Error / success".
            return idempotent_replay(existing_for_key(user, key)) or fail(
                "This transfer was already submitted. Check your transaction history.",
                status=409, code="duplicate", duplicate=True,
            )
        if exc.kind == "insufficient":
            return fail("Insufficient wallet balance", status=402)
        # Provider messages pass through to the user, but a bare status echo
        # ("success" when the API REQUEST succeeded) or an empty string would render
        # a nonsense "Error / success" dialog on app builds that show the message
        # raw — replace those with a real sentence.
        message = (exc.message or "").strip()
        if not message or message.lower() in ("success", "successful"):
            message = "Transfer could not be completed. Please try again."
        return fail(message, status=502)

    from wallet.services import get_or_create_wallet
    wallet = get_or_create_wallet(user)
    if txn.transaction_status == Transaction.PENDING:
        # Rail queued it but hasn't confirmed — don't claim "sent".
        return ok(pending=True, wallet=str(wallet.balance), reference=txn.reference, name=name,
                  narration=(txn.meta or {}).get("narration", ""),
                  message="Your transfer is processing and will be confirmed shortly.")
    return ok(success=True, wallet=str(wallet.balance), reference=txn.reference, name=name,
              narration=(txn.meta or {}).get("narration", ""),
              # So the receipt can offer "save this recipient" and act on the tap
              # with an id, rather than posting an account number back and paying
              # for a second name enquiry to identify a row we just wrote.
              beneficiary_id=getattr(txn, "beneficiary_id", None),
              message="Money sent")
