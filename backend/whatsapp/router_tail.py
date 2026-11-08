
def _advance_transfer(pa: PendingAction, user, msisdn: str, text: str) -> None:
    state = pa.state

    if state == "amount":
        amount = parse_amount(text)
        if amount is None or amount < MIN_TRANSFER:
            return reply(msisdn, f"Please enter a valid amount, at least ₦{MIN_TRANSFER:,.0f} (e.g. 5000 or 5k).")
        limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "transfer")
        if limit_msg:
            _clear_actions(msisdn)
            return _limit_reply(msisdn, user, limit_msg)
        if _insufficient(user, amount):
            return reply(msisdn, f"Insufficient balance. You have {_money(_fresh_wallet_balance(user))} available. "
                                 "Enter a lower amount, or \"cancel\".")
        pa.payload["amount"] = str(amount)
        _touch(pa, state="account", payload=pa.payload)
        return reply(msisdn, "Enter the recipient's 10-digit account number.")

    if state == "account":
        acct = re.sub(r"\D", "", text)
        if len(acct) != 10:
            return reply(msisdn, "That doesn't look like a 10-digit account number. Please try again.")
        pa.payload["account"] = acct
        _touch(pa, state="bank", payload=pa.payload)
        return reply(msisdn, "Which bank? Type the bank name (e.g. GTBank, Access, Opay).")

    if state == "bank":
        matches = _match_banks(text)
        if not matches:
            return _reroute_or_reprompt(pa, msisdn, text,
                                       "I couldn't find that bank. Type the name again, or \"cancel\".")
        if len(matches) > 1:
            shown = matches[:6]
            pa.payload["bank_choices"] = [b.code for b in shown]
            _touch(pa, state="bank_pick", payload=pa.payload)
            # The list caps at 6; with the full bank catalogue a loose name can
            # match more, so when we truncate, tell the user how to narrow it
            # instead of silently hiding the rest.
            prompt = ("I found a few banks - pick yours:" if len(matches) <= 6
                      else f"I found {len(matches)} banks - here are the first 6. "
                           "Reply a number, or type the bank's exact name:")
            return reply_list(msisdn, prompt,
                              [(str(i + 1), b.name[:24], "") for i, b in enumerate(shown)],
                              button_label="Banks")
        return _resolve_and_confirm(pa, user, msisdn, matches[0])

    if state == "bank_pick":
        choices = pa.payload.get("bank_choices", [])
        try:
            idx = int(text.strip()) - 1
        except ValueError:
            return _reroute_or_reprompt(pa, msisdn, text,
                                       "Reply with the number of the bank from the list, or \"cancel\".")
        if not (0 <= idx < len(choices)):
            return _reroute_or_reprompt(pa, msisdn, text,
                                       "That number isn't on the list. Try again, or \"cancel\".")
        bank = Bank.objects.filter(code=choices[idx], active=True).first()
        if bank is None:
            _clear_actions(msisdn)
            return reply(msisdn, "Something went wrong picking that bank. Reply \"menu\" to start over.")
        return _resolve_and_confirm(pa, user, msisdn, bank)

    if state == "pin":
        return _try_pin(pa, user, msisdn, text)

    _clear_actions(msisdn)
    return send_menu(msisdn)


def _match_banks(text: str) -> list:
    t = text.strip().lower()
    banks = list(Bank.objects.filter(active=True))
    exact = [b for b in banks if b.name.lower() == t]
    if exact:
        return exact
    # What people actually call them. We store one name per bank, usually the
    # short trading name, so "Guaranty Trust Bank" off a statement matched
    # nothing at all against "GTBank" - which reads as the bank not existing
    # rather than as us knowing it by another name. An alias hit is exact and
    # names one bank, so it wins outright.
    slug = slug_for_alias(t)
    if slug:
        named = [b for b in banks if b.code == slug]
        if named:
            return named
    # Substring, over the aliases as well as the name: it is what catches the
    # half-typed word inside a longer sentence ("send 2k to my gtb account"),
    # and it returns every candidate rather than choosing between them.
    return [b for b in banks
            if t and (t in b.name.lower() or b.name.lower() in t
                      or any(t in a or a in t for a in aliases_for(b.code)))]


def _bank_items(candidates=None, query: str = "") -> list:
    """Dropdown data for the transfer form: {id, title} per active bank, popular
    first. When `candidates` (Bank rows) is given they lead the list - the
    NUBAN-narrowed suggestions - with everything else after, because a checksum
    match is a suggestion and the customer must stay able to pick any bank.

    `query` narrows the list to name matches. It exists because a Flow JSON
    Dropdown cannot be searched on the device - there is no filter property and
    no way for a TextInput to narrow one client-side - so the only place a bank
    list of this length can be searched at all is here, on a submit. Substring
    and case-insensitive: people look for "kuda" and "opay" in lower case, and
    for "ibom" as readily as they type the first letters.

    A query that matches NOTHING returns the full list rather than an empty one.
    A Dropdown bound to an empty array does not render at all - the customer gets
    a blank sheet with no way forward, which this repo has already been bitten by
    once (#357) - so a bad search must degrade to "no narrowing", never to a
    screen with nothing on it.
    """
    banks = list(Bank.objects.filter(active=True).order_by("-popular", "name")[:200])
    q = " ".join(str(query or "").split()).lower()
    if q:
        hits = [b for b in banks if q in b.name.lower()]
        if hits:
            banks = hits
    if candidates:
        heads = [b for b in banks if b.code in {c.code for c in candidates}]
        banks = heads + [b for b in banks if b.code not in {c.code for c in candidates}]
    return [{"id": b.code, "title": b.name} for b in banks[:200]]


def nuban_bank_candidates(account: str) -> list:
    """The active banks this 10-digit NUBAN is checksum-valid for.

    The CBN algorithm bakes the bank code into the check digit: weights 3,7,3
    cycling over bank_code + the 9-digit serial, check = (10 - sum mod 10) mod
    10. Running it against every bank's code narrows hundreds to a handful -
    which is why this SUGGESTS an ordering and never picks silently when more
    than one matches: a wrong guess is money at the wrong institution.
    """
    digits = "".join(ch for ch in str(account or "") if ch.isdigit())
    if len(digits) != 10:
        return []
    serial, check = digits[:9], int(digits[9])
    out = []
    for bank in Bank.objects.filter(active=True):
        code = "".join(ch for ch in (bank.bank_code or "") if ch.isdigit())
        if not code:
            continue
        seq = code + serial
        total = sum(int(c) * (3, 7, 3)[i % 3] for i, c in enumerate(seq))
        if (10 - total % 10) % 10 == check:
            out.append(bank)
    return out


def _resolve_and_confirm(pa: PendingAction, user, msisdn: str, bank) -> None:
    """Name-enquiry against the bank, then show the confirm card and await PIN."""
    acct = pa.payload["account"]
    res = payout_resolve_account(acct, bank.bank_code)
    if not res.get("success"):
        _touch(pa, state="account")
        return reply(msisdn, f"Couldn't verify that account at {bank.name}. "
                             "Re-enter the 10-digit account number, or \"cancel\".")
    name = (res.get("name") or "").strip() or "Bank recipient"
    amount = Decimal(pa.payload["amount"])
    pa.payload.update({"bank_code": bank.bank_code, "bank_name": bank.name, "name": name})
    if not _arm_confirm(pa, user):
        return
    _send_confirm(pa, msisdn,
                  "Confirm transfer\n"
                  f"{_money(amount)} -> {name.upper()}\n"
                  f"{bank.name} • {acct}\nFee: {_money(Decimal(pa.payload['expected_fee']))}\nTotal: {_money(amount + Decimal(pa.payload['expected_fee']))}")


def _flow_pin_ok(pa: PendingAction, user, msisdn: str, text: str) -> bool:
    """Shared confirm gate for every money flow: True if the reply is the armed
    single-use SMS code (preferred - the chat never carries the PIN) or, when no
    code was armed (dev/mock SMS), the transaction PIN. Sends the right message
    (expired / locked / retry / cancel) and returns False otherwise."""
    otp_hash = pa.payload.get("otp_hash", "")
    if otp_hash:
        exp = pa.payload.get("otp_exp", "")
        expired = True
        try:
            expired = timezone.now() > timezone.datetime.fromisoformat(exp)
        except (TypeError, ValueError):
            pass
        if expired:
            _clear_actions(msisdn)
            reply(msisdn, "That code has expired - cancelled for your safety. Reply \"menu\" to start over.")
            return False
        if check_password(text.strip(), otp_hash):
            pa.payload.pop("otp_hash", None)   # single use - a replay can't confirm twice
            _touch(pa, payload=pa.payload)
            return True
        attempts = int(pa.payload.get("pin_attempts", 0)) + 1
        if attempts >= PIN_FLOW_ATTEMPTS:
            _clear_actions(msisdn)
            reply(msisdn, "Too many wrong codes. Cancelled - reply \"menu\" to start over.")
            return False
        pa.payload["pin_attempts"] = attempts
        _touch(pa, payload=pa.payload)
        reply(msisdn, "That code isn't right. Check the SMS we sent and try again, or \"cancel\".")
        return False
    # The PIN-from-chat fallback is re-gated HERE, not only where the rung was
    # armed: _arm_confirm never arms it in production, but this function is the
    # point of acceptance, and an action that reaches "pin" without an armed
    # code on a production host (a stale row from a config flip, a future
    # arming bug) must fail closed rather than quietly start reading PINs out
    # of a chat that keeps them forever.
    if not _pin_in_chat_allowed():
        _clear_actions(msisdn)
        reply(msisdn, "For your security this needs to be confirmed in the Zitch app. "
                      "Nothing was sent - please start again.")
        return False
    ok, code, message = evaluate_transaction_pin(user, text)
    if ok:
        return True
    if code == "pin_locked":
        _clear_actions(msisdn)
        # The shared message already says a reset is possible; here it also has
        # to say what to TYPE - said on every lock, not only the 24-hour one, so
        # the way out is offered from the first lock rather than an hour later.
        message += " Reply *reset pin* to choose a new one."
        reply(msisdn, message)
        return False
    attempts = int(pa.payload.get("pin_attempts", 0)) + 1
    if attempts >= PIN_FLOW_ATTEMPTS:
        _clear_actions(msisdn)
        reply(msisdn, "Too many wrong PIN attempts. Cancelled - reply \"menu\" to start over.")
        return False
    pa.payload["pin_attempts"] = attempts
    _touch(pa, payload=pa.payload)
    reply(msisdn, f"{message} Reply with your PIN, or \"cancel\".")
    return False


def _try_pin(pa: PendingAction, user, msisdn: str, text: str) -> None:
    if not _flow_pin_ok(pa, user, msisdn, text):
        return
    return _exec_transfer(pa, user, msisdn)


#: How often the settle wait re-reads the ledger. Short enough that a fast rail
#: is caught almost immediately, long enough that a 3-second budget is a handful
#: of queries rather than hundreds.
_SETTLE_POLL = 0.25

#: The three things that can have happened to money by the time a Flow closes,
#: plus the untagged default. The Flow's terminal screen shows one of them as its
#: heading, so "we tried" and "it worked" stop looking identical to the customer.
OUTCOME_SUCCESS = "success"
OUTCOME_PENDING = "pending"
OUTCOME_FAILED = "failed"


class Outcome(str):
    """An executor's closing line, tagged with what actually happened to the money.

    A plain `str` everywhere one is already used - the chat line, the approve
    API's `message`, the existing assertions - with one extra attribute the Flow
    endpoint reads to pick its terminal heading. A subclass rather than a tuple
    precisely so nothing that consumes these strings has to change, and so an
    executor that returns a bare string still renders correctly: untagged reads
    as `done`, which is what it has always meant.
    """

    status = "done"

    def __new__(cls, text: str, status: str = "done"):
        obj = super().__new__(cls, text)
        obj.status = status
        return obj


def _stored_provider_result(txn) -> dict:
    """Rebuild the safe, durable part of a provider result for a replay.

    ``settle_or_refund`` deliberately persists provider outcome fields on the
    ledger row (and stores a refusal as ``failure``).  A duplicate WhatsApp
    submission must read that row; it must never call the provider again or
    guess that "duplicate" means success.
    """
    result = dict(txn.meta or {})
    if result.get("failure") and not result.get("message"):
        result["message"] = result["failure"]
    return result


def _transfer_outcome(pa: PendingAction, user, msisdn: str, txn, *, replay: bool = False) -> Outcome:
    """Tell the customer what the transfer ledger says *now*.

    This is shared by the first response and an idempotent replay.  In
    particular, a duplicate key is only request identity: the durable row may
    still be Pending or may have failed and been refunded.
    """
    from wallet.alerts import mark_awaiting_settlement
    from wallet.models import Transaction

    txn.refresh_from_db(fields=["transaction_status", "meta", "reference"])
    _clear_actions(msisdn)
    amount = Decimal(pa.payload["amount"])
    who = pa.payload["name"].upper()

    from wallet.review_state import transaction_review_map
    if txn.pk in transaction_review_map([txn]):
        # A conflicting returned-funds row is awaiting maker/checker review.
        # Regardless of the payout row's pre-existing status, never issue a
        # success receipt or invite another payment while that hold is active.
        mark_awaiting_settlement(txn)
        lead = "That transfer" if replay else "Your transfer"
        line = (f"⏳ {lead} of {_money(amount)} to {who} is under review. "
                f"Do not send it again; we'll confirm after reconciliation. "
                f"Ref {txn.reference}.")
        reply(msisdn, line)
        return Outcome(line, OUTCOME_PENDING)

    if txn.transaction_status == Transaction.FAILED:
        result = _stored_provider_result(txn)
        reason = customer_safe_failure(
            result, service="transfer", fallback="the bank did not complete it")
        lead = "That earlier transfer" if replay else "Your transfer"
        line = (f"❌ {lead} of {_money(amount)} to {who} failed: {reason}. "
                f"Any held amount was returned. Ref {txn.reference}.")
        reply(msisdn, line)
        return Outcome(line, OUTCOME_FAILED)

    if txn.transaction_status == Transaction.PENDING:
        # "processing" is not an outcome, so the settlement alert has to be let
        # through when the row finally resolves.
        mark_awaiting_settlement(txn)
        lead = "That transfer" if replay else "Your transfer"
        line = (f"⏳ {lead} of {_money(amount)} to {who} is still processing - "
                f"we'll confirm once it settles. Ref {txn.reference}.")
        reply(msisdn, line)
        return Outcome(line, OUTCOME_PENDING)

    if txn.transaction_status != Transaction.SUCCESS:
        line = (f"That {_money(amount)} transfer to {who} has an unrecognised ledger "
                f"status. Do not send it again; contact support with ref {txn.reference}.")
        reply(msisdn, line)
        return Outcome(line, OUTCOME_PENDING)

    # Only a durable Successful row gets a receipt.  Replaying the receipt is
    # safe; replaying the payout is not, which is why this helper is reached only
    # after the idempotency lookup.
    wallet = get_or_create_wallet(user)
    reply_receipt(msisdn, "Transfer receipt", _with_narration(pa, [
        ("To", who),
        ("Bank", pa.payload["bank_name"]),
        ("Account", pa.payload["account"]),
        ("Amount", _money(amount)),
        ("Fee", _money(Decimal(txn.meta.get("fee", "0")))),
        ("Total", _money(txn.amount)),
        ("Reference", txn.reference),
        ("Date", txn.created.strftime("%d %b %Y, %H:%M")),
    ]), ref=txn.reference, user=user, balance_after=wallet.balance)
    if not replay:
        _offer_to_save(user, msisdn, getattr(txn, "beneficiary_id", None))
    if replay:
        return Outcome(f"That {_money(amount)} transfer to {who} was already completed - "
                       "the receipt is in your chat.", OUTCOME_SUCCESS)
    return Outcome(f"{_money(amount)} sent to {who} - the receipt is in your chat.",
                   OUTCOME_SUCCESS)


def _exec_transfer(pa: PendingAction, user, msisdn: str) -> str:
    """Execute a PIN-confirmed transfer (called by the chat PIN path AND the
    secure Flow endpoint). Sends the chat receipt and returns a short outcome
    line for the Flow success screen."""
    from wallet.models import Transaction

    # Replay the actual ledger outcome before mutable bank/enquiry/limit state.
    # A disabled bank or an outage must not relabel an existing payment failed
    # and invite the customer to send it again with a new reference.
    prior = Transaction.objects.filter(user=user, idempotency_key=f"wa-{pa.pk}").first()
    if prior is not None:
        return _transfer_outcome(pa, user, msisdn, prior, replay=True)
    if velocity_exceeded(user):  # same fraud brake the app enforces (parity)
        _clear_actions(msisdn)
        msg = "Too many transactions in a short time. Please wait a few minutes and try again."
        reply(msisdn, msg)
        return Outcome(msg, OUTCOME_FAILED)
    amount = Decimal(pa.payload["amount"])
    bank = Bank.objects.filter(bank_code=pa.payload["bank_code"], active=True).first()
    if bank is None:
        _clear_actions(msisdn)
        msg = f"Something went wrong with your {_money(amount)} transfer. Reply \"menu\" to start over."
        reply(msisdn, msg)
        return Outcome(msg, OUTCOME_FAILED)
    # Re-run the name enquiry immediately before paying, exactly as the app does
    # (transfers.views.bank_transfer). The name shown at the "bank" step can be
    # minutes old by the time the PIN comes back, and routing is purely by
    # {account_number, bank_code} - if the account now resolves to someone else,
    # paying against the stale name sends money to the wrong real person.
    confirmed_name = pa.payload["name"]
    fresh = payout_resolve_account(pa.payload["account"], bank.bank_code)
    if not fresh.get("success") or not str(fresh.get("name") or "").strip():
        _clear_actions(msisdn)
        msg = ("We couldn't verify the recipient with the bank. Your transfer was not sent "
               "and you were not charged. Check the account details and try again shortly.")
        reply(msisdn, msg)
        return Outcome(msg, OUTCOME_FAILED)
    if fresh.get("success"):
        fresh_name = (fresh.get("name") or "").strip()
        # Only enforce on a LIVE enquiry: the mock returns a fixed stub, which
        # would false-block every sandbox transfer.
        if not fresh.get("mock") and fresh_name and not _names_match(confirmed_name, fresh_name):
            _clear_actions(msisdn)
            msg = (f"This account now belongs to {fresh_name}, not {confirmed_name.upper()}. "
                   f"Your {_money(amount)} transfer was not sent - please check the account "
                   "number and start again.")
            reply(msisdn, msg)
            return Outcome(msg, OUTCOME_FAILED)
        confirmed_name = fresh_name or confirmed_name
    try:
        # Stable key per flow: a re-sent "pin" message can't double-pay.
        txn = execute_payout(
            user, amount, pa.payload["account"], bank, confirmed_name,
            # The customer's own words on the beneficiary's statement. Falls back
            # inside execute_payout to "Transfer to <name>" when it is blank,
            # which is what every transfer sent before narration existed.
            note=_narration(pa),
            idempotency_key=f"wa-{pa.id}", channel="whatsapp", expected_fee=pa.payload.get("expected_fee"),
        )
    except PayoutError as exc:
        who = pa.payload["name"].upper()
        if exc.kind == "insufficient":
            _clear_actions(msisdn)
            msg = f"Insufficient balance for the {_money(amount)} transfer to {who} - cancelled."
            reply(msisdn, msg)
            return Outcome(msg, OUTCOME_FAILED)
        if exc.kind == "duplicate":
            # Duplicate means only that this action's key already has a ledger
            # row.  It does *not* mean the rail settled successfully.  Read the
            # row and render its actual terminal/pending state without sending
            # another payout.
            from wallet.models import Transaction

            prior = Transaction.objects.filter(
                user=user, idempotency_key=f"wa-{pa.id}").first()
            if prior is not None:
                return _transfer_outcome(pa, user, msisdn, prior, replay=True)
            _clear_actions(msisdn)
            msg = (f"That {_money(amount)} transfer to {who} was submitted before, but its "
                   "status is not available yet. Do not send it again; contact support so "
                   "we can confirm it.")
            reply(msisdn, msg)
            return Outcome(msg, OUTCOME_PENDING)
        _clear_actions(msisdn)
        msg = f"Transfer of {_money(amount)} to {who} failed: {exc.message}"
        reply(msisdn, msg)
        return Outcome(msg, OUTCOME_FAILED)

    return _transfer_outcome(pa, user, msisdn, txn)


# ---------------------------------------------------------------------------
# Saved people - keeping a recipient, naming them, and paying them by name
# ---------------------------------------------------------------------------

BENEFICIARY_STATE = "beneficiary_menu"   # list shown, waiting for a row number
BENEFICIARY_PICK_STATE = "beneficiary_pick"   # row chosen, waiting for an action
BENEFICIARY_NAME_STATE = "beneficiary_name"   # waiting for the nickname itself

# How long a "not now" is respected. A customer who pays their landlord every
# month should be asked once, not after every rent payment: an offer that cannot
# be refused permanently is a nag, and this one rides on a metered channel.
_SAVE_DECLINE_TTL = 180 * 24 * 3600

# "send 5k to mum", "pay 2000 to landlord". Deliberately narrow: an amount, the
# word to, and a name. Anything carrying an account number is a paste and is
# handled by _start_transfer_from_paste, which is why the digit guard below
# refuses this path outright rather than trying to be clever about both.
_SEND_TO_NAME = re.compile(
    r"^(?:send|pay|transfer)\s+(?P<amount>[\d,.]+\s*[km]?)\s+to\s+(?P<who>[a-z][a-z' .\-]{0,39})$",
    re.I)


def _decline_key(pk: int) -> str:
    return f"wa:bene:no:{pk}"


def _offer_to_save(user, msisdn: str, beneficiary_id) -> None:
    """After a settled transfer, offer to keep the recipient.

    Silent when there is nothing to offer: an already-saved recipient, or one the
    customer has declined before. The offer is a question about their address
    book, and asking it again every month is how a helpful prompt turns into
    something people learn to ignore.
    """
    if not beneficiary_id:
        return
    row = user.beneficiaries.filter(pk=beneficiary_id).first()
    # Ask only after the third transfer and only once. Automatic recipient
    # memory remains private unless the customer accepts or reaches 51 transfers.
    if row is None or row.saved or row.transfer_count < 3 or row.save_offer_sent or cache.get(_decline_key(row.pk)):
        return
    row.save_offer_sent = True
    row.save(update_fields=["save_offer_sent"])
    reply_buttons(
        msisdn,
        f"⭐ Save *{row.name.upper()}* so you can pay them by name next time?",
        [(f"bene:save:{row.pk}", "Save"), (f"bene:no:{row.pk}", "Not now")])


def _handle_save_button(user, msisdn: str, low: str) -> bool:
    """A tap on the save offer. True when this message was one.

    Resolved through the customer's OWN related manager, so the row id in the
    button - which travels through WhatsApp and which anybody could simply type
    back into the chat - can only ever name a recipient of theirs. A guessed id
    belonging to somebody else finds nothing and says so.

    Note what this does NOT do: open a pending action. _new_flow would clear the
    customer's actions first, and these buttons stay tappable in the history
    forever, so a tap on last month's offer would delete an in-flight payment's
    only row while it was executing. Saving somebody needs no flow.
    """
    if not low.startswith(("bene:save:", "bene:no:")):
        return False
    decision, _, raw = low.partition(":")[2].partition(":")
    row = user.beneficiaries.filter(pk=raw if raw.isdigit() else 0).first()
    if row is None:
        reply(msisdn, "That recipient is no longer in your list.")
        return True
    if decision == "no":
        cache.set(_decline_key(row.pk), 1, _SAVE_DECLINE_TTL)
        reply(msisdn, "No problem - I won't ask about them again.")
        return True
    row.saved = True
    row.save(update_fields=["saved"])
    _new_flow(user, msisdn, "beneficiary", BENEFICIARY_NAME_STATE, {"id": row.pk})
    reply(msisdn, f"⭐ Saved. What should I call {row.name.upper()}? "
                  "Reply with a short name like \"Mum\", or *skip* to keep the bank's name.")
    return True


def _looks_like_a_name(text: str) -> bool:
    """Whether this reads as what somebody is CALLED, rather than a request.

    Names are short, made of letters, and carry no account number. Instructions
    carry amounts, account numbers and bank names. The two are far enough apart
    that shape decides it, which is what keeps "Elizabeth" a name while
    "2k to. Yahaya 0998787776 polaris" is a payment somebody is waiting for.

    Digits are not banned outright - "Flat 3B" and "Ada 2" are things people
    genuinely call each other - but a run of four or more is an account number,
    a phone number or an amount, and none of those is a name.
    """
    t = " ".join(str(text or "").split())
    if not t or len(t) > 40 or len(t.split()) > 4:
        return False
    if re.search(r"\d{4,}", t):
        return False
    if not re.fullmatch(r"[A-Za-z0-9' .\-]+", t):
        return False
    # At least one real word. "3B" alone, or ". -", names nobody.
    return bool(re.search(r"[A-Za-z]{2,}", t))


def _beneficiary_lines(rows) -> str:
    return "\n".join(
        f"{i}. *{r.display_name}* - {r.bank_name} {r.account_number}"
        for i, r in enumerate(rows, start=1))


def _saved_rows(user):
    return list(user.beneficiaries.filter(saved=True)[:20])


def _do_beneficiaries(user, msisdn: str) -> None:
    """Item 12 - the customer's address book."""
    rows = _saved_rows(user)
    if not rows:
        return reply(msisdn, "⭐ *My saved people*\n\nYou haven't saved anyone yet. "
                             "After your next transfer I'll offer to keep the recipient, "
                             "and then you can pay them by name - \"send 5k to mum\".")
    _new_flow(user, msisdn, "beneficiary", BENEFICIARY_STATE,
              {"ids": [r.pk for r in rows]})
    reply(msisdn, "⭐ *My saved people*\n\n" + _beneficiary_lines(rows) +
                  "\n\nReply with a number to rename or remove someone, "
                  "or just say \"send 5k to " + rows[0].display_name.lower() + "\".")


def _advance_beneficiary(pa: PendingAction, user, msisdn: str, text: str) -> None:
    low = text.strip().lower()
    if pa.state == BENEFICIARY_NAME_STATE:
        row = user.beneficiaries.filter(pk=pa.payload.get("id") or 0).first()
        if row is None:
            _clear_actions(msisdn)
            return reply(msisdn, "That recipient is no longer in your list.")
        if low in ("skip", "no", "none"):
            _clear_actions(msisdn)
            return reply(msisdn, f"Kept as {row.name.upper()}.")
        # A person's name, or something else entirely? This prompt sits in the way
        # of the whole chat, so whatever arrives next is at least as likely to be
        # a new instruction as an answer - "2k to. Yahaya 0998787776 polaris" was
        # accepted as somebody's name, which is nonsense on its face and, worse,
        # ate the payment the customer was asking for.
        #
        # Decided by SHAPE rather than by keyword. _names_another_intent matches
        # substrings, so "Elizabeth" contains "bet" and "Ricardo" contains "card":
        # asking it first would make ordinary names unusable. Asking what a name
        # looks like has neither failure - a name has letters and no account
        # number, and an instruction almost always carries digits.
        if not _looks_like_a_name(text):
            return _reroute_or_reprompt(pa, msisdn, text,
                                        "That doesn't look like a name. Reply with a short "
                                        "one like \"Mum\", or *skip* to keep the bank's name.")
        nickname, problem = clean_nickname(text)
        if problem:
            return reply(msisdn, f"{problem} Or reply *skip*.")
        if not nickname:
            return _reroute_or_reprompt(pa, msisdn, text,
                                        "What should I call them? Or reply *skip*.")
        if user.beneficiaries.filter(nickname__iexact=nickname).exclude(pk=row.pk).exists():
            return reply(msisdn, f"You already have someone called {nickname}. "
                                 "Try another name, or reply *skip*.")
        row.nickname = nickname
        # Naming somebody is itself an act of keeping them. Without this a
        # recipient named in the chat would stay unsaved, and paying by name only
        # ever reads saved rows - so the customer would have named someone they
        # then could not pay by that name.
        row.saved = True
        row.save(update_fields=["nickname", "saved"])
        _clear_actions(msisdn)
        return reply(msisdn, f"⭐ Saved as *{nickname}*. Next time just say "
                             f"\"send 5k to {nickname.lower()}\".")

    rows = list(user.beneficiaries.filter(pk__in=pa.payload.get("ids") or []))
    order = {pk: i for i, pk in enumerate(pa.payload.get("ids") or [])}
    rows.sort(key=lambda r: order.get(r.pk, 0))

    if pa.state == BENEFICIARY_PICK_STATE:
        row = user.beneficiaries.filter(pk=pa.payload.get("id") or 0).first()
        if row is None:
            _clear_actions(msisdn)
            return reply(msisdn, "That recipient is no longer in your list.")
        if low in ("1", "rename", "name"):
            pa.state = BENEFICIARY_NAME_STATE
            pa.payload = {"id": row.pk}
            pa.save(update_fields=["state", "payload"])
            return reply(msisdn, f"What should I call {row.name.upper()}? "
                                 "Reply with a short name, or *skip*.")
        if low in ("2", "remove", "delete"):
            label = row.display_name
            row.delete()
            _clear_actions(msisdn)
            return reply(msisdn, f"Removed *{label}*.")
        return _reroute_or_reprompt(pa, msisdn, text,
                                    "Reply *1* to rename or *2* to remove.")

    if low.isdigit() and 1 <= int(low) <= len(rows):
        row = rows[int(low) - 1]
        pa.state = BENEFICIARY_PICK_STATE
        pa.payload = {"id": row.pk}
        pa.save(update_fields=["state", "payload"])
        return reply(msisdn, f"*{row.display_name}* - {row.bank_name} {row.account_number}\n\n"
                             "Reply *1* to rename, or *2* to remove.")
    return _reroute_or_reprompt(pa, msisdn, text,
                                "Reply with a number from the list above.")


def _start_transfer_to_saved(user, msisdn: str, text: str) -> bool:
    """"send 5k to mum" - pay a saved recipient by the name the customer gave them.

    Returns True only when it actually opened a transfer. A miss returns False on
    purpose, so the ordinary guided form underneath still runs: "send 5k to Ada"
    has always ended in a transfer form, and answering it with "I don't know an
    Ada" and nothing else would be a dead end where there used to be a working
    payment.

    Matching is EXACT and against the nickname only. A nickname is unique per
    customer and is a label they chose; the bank's holder name is neither, and a
    loose match on it could name two accounts - at which point the only safe
    answer about where money goes is to stop and ask.
    """
    m = _SEND_TO_NAME.match(text.strip())
    if not m:
        return False
    # Anything carrying an account number is a paste, not a name.
    if re.search(r"\d{10,}", text):
        return False
    who = " ".join(m.group("who").split())
    rows = list(user.beneficiaries.filter(saved=True).exclude(nickname="")
                .filter(nickname__iexact=who)[:2])
    if len(rows) != 1:
        return False
    amount = parse_amount(m.group("amount"))
    if amount is None:
        return False
    row = rows[0]
    reply(msisdn, f"Paying *{row.display_name}* - {row.bank_name} {row.account_number}.")
    return _begin_bank_transfer(user, msisdn, amount, row.account_number, row.bank_name)


def _start_transfer_from_paste(user, msisdn: str, text: str) -> bool:
    """Parse "0123456789 GTBank John Doe 5000" -> jump straight to name-enquiry.
    Returns True if handled as a transfer paste, else False."""
    tokens = text.split()
    # 10 digits is a NUBAN; 11 is how the app-first banks address an account -
    # Moniepoint, OPay, PalmPay and Kuda all use the customer's phone number. Only
    # matching 10 meant "12300. Moniepoint 01827364728 cravings" fell through to
    # the guided form, which reads as the assistant ignoring a complete instruction.
    acct = next((re.sub(r"\D", "", t) for t in tokens
                 if len(re.sub(r"\D", "", t)) in (10, 11)), None)
    amount = None
    for t in reversed(tokens):
        if len(re.sub(r"\D", "", t)) >= 10:  # skip account- / phone-length tokens
            continue
        amount = parse_amount(t)
        if amount is not None:
            break
    if not acct or amount is None:
        return False
    return _begin_bank_transfer(user, msisdn, amount, acct, text)


def _begin_bank_transfer(user, msisdn: str, amount: Decimal, acct: str, bank_query: str) -> bool:
    """Validate then open a transfer at the bank step - shared by the paste path
    and the LLM. Returns False only when the bank can't be matched (caller decides)."""
    if _blocked_from_spending(user, msisdn, outbound=True):
        return True
    matches = _match_banks(bank_query)
    # The NUBAN carries its own bank code in the check digit, so a message that
    # names no bank is not missing anything - "send 1000 to Mutumin 2217940528"
    # was answered with the guided form purely because `bank_query` had no bank
    # name in it, which reads as the assistant ignoring a complete instruction.
    # The checksum resolves it, and the name enquiry below is the real safety
    # net either way. It also NARROWS an ambiguous name match: _match_banks does
    # substring matching over a whole sentence, so several banks matching is
    # common and the account number can only belong to one of them.
    nuban = nuban_bank_candidates(acct)
    if nuban:
        narrowed = [b for b in matches if b.code in {c.code for c in nuban}]
        matches = narrowed or (matches if matches else nuban)
    if not matches:
        return False
    if amount < MIN_TRANSFER:
        reply(msisdn, f"Minimum transfer is ₦{MIN_TRANSFER:,.0f}.")
        return True
    limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "transfer")
    if limit_msg:
        _limit_reply(msisdn, user, limit_msg)
        return True
    if _insufficient(user, amount):
        reply(msisdn, f"Insufficient balance. You have {_money(_fresh_wallet_balance(user))} available.")
        return True
    pa = _new_flow(user, msisdn, "transfer", "bank",
                   {"amount": str(amount), "account": acct, "pin_attempts": 0})
    if len(matches) == 1:
        _resolve_and_confirm(pa, user, msisdn, matches[0])
    else:
        pa.payload["bank_choices"] = [b.code for b in matches[:6]]
        _touch(pa, state="bank_pick", payload=pa.payload)
        lines = "\n".join(f"{i+1}  {b.name}" for i, b in enumerate(matches[:6]))
        reply(msisdn, "Which bank? Reply with the number:\n" + lines)
    return True


#: The narration the CURRENT message asked for, for the duration of dispatching
#: it. Set once by dispatch_intent and read once by _new_flow.
#:
#: A context variable rather than a parameter because the alternative is
#: threading an optional string through every _begin_*/_start_* entry point and
#: every _new_flow call inside them, to carry one word that only the AI path can
#: supply. Scoped to one dispatch and cleared in a finally, so it cannot leak
#: into the next message; a ContextVar (not a global) so concurrent requests on
#: the same worker cannot read each other's.
_ai_narration: contextvars.ContextVar[str] = contextvars.ContextVar("ai_narration", default="")

#: The bank the CURRENT message named, for the length of dispatching it.
#:
#: Same mechanism and same reason as the narration above. A customer who says
#: "send 5k to my Kuda account" has already told us the bank; opening the
#: transfer form on the full list of every Nigerian bank asks them again, which
#: is the channel telling them it did not read their message.
_ai_bank: contextvars.ContextVar[str] = contextvars.ContextVar("ai_bank", default="")

#: Action types with money and a receipt behind them - the only ones a narration
#: means anything for. Airtime, data, electricity and cable were here until the
#: VAS rail went; a transfer is now the only money flow left to narrate.
_NARRATABLE = ("transfer",)


def _new_flow(user, msisdn: str, action_type: str, state: str, payload: dict | None = None) -> PendingAction:
    _clear_actions(msisdn)
    payload = payload if payload is not None else {"pin_attempts": 0}
    note = _ai_narration.get("")
    if note and action_type in _NARRATABLE and "narration" not in payload:
        # Stamped at birth, before anything arms a confirm: the Flow's send
        # payload is built from this payload, so a narration attached afterwards
        # would be missing from the very screen it exists to appear on.
        payload = {**payload, "narration": note}
    return PendingAction.objects.create(
        user=user, msisdn=msisdn, action_type=action_type, state=state,
        payload=payload, expires_at=_flow_deadline(state, payload),
    )


def _own_phone(user) -> str | None:
    """The linked account phone in the familiar local form VTU inputs show."""
    return normalize_nigerian_mobile(getattr(user, "phone", "") or "")


def _phone_from(text: str, user) -> str | None:
    """Resolve ``me`` or validate a Nigerian mobile destination."""
    if text.strip().lower() in ("me", "self", "mine"):
        return _own_phone(user)
    return normalize_nigerian_mobile(text)


def _insufficient(user, amount: Decimal) -> bool:
    return _fresh_wallet_balance(user) < amount


# --------------------------------------------------------------------------- #
# AI intent layer - the LLM proposes; these map its intent to the SAME flows
# --------------------------------------------------------------------------- #
def ai_active(link: WhatsAppLink, convo: ConversationState) -> bool:
    """AI runs only if all scopes are on: an LLM key is set, the global kill
    switch is on, this user's AI is enabled, and this conversation's AI is on
    (handover turns the conversation scope off)."""
    return (ai.llm_available()
            and SystemSetting.get_bool("ai_enabled_global", False)
            and link.ai_enabled
            and convo.ai_enabled)


def _record_intent(msisdn: str, intent: dict) -> None:
    """Attach the parsed intent to the inbound row (for QA / the monitor)."""
    from .models import WebhookEvent

    row = WaMessageLog.objects.filter(msisdn=msisdn, direction=WaMessageLog.IN).order_by("-created").first()
    if row is not None:
        # Store the MASKED input (tokens, not identifiers): intent_json feeds the
        # ops console's intent list, and a re-hydrated copy would put customer
        # account numbers on a screen that only needs to show routing quality.
        logged = {"name": intent.get("name"),
                  "input": intent.get("masked_input", intent.get("input", {}))}
        row.intent_json = WebhookEvent.redact(logged)
        row.save(update_fields=["intent_json"])


#: "5k", "5,000", "₦5000", "2 million" - the amount as customers actually write
#: it. Used only to FILL IN what the model left blank, never to override it.
_AMOUNT_HINT = re.compile(
    r"(?:₦|ngn\s*)?(\d[\d,]*(?:\.\d+)?)\s*(k|m)?\b", re.I)
#: "2 days ago", "3days ago", "2 dayssgo" (the typo in the report), "yesterday",
#: "today". Tolerant of the missing space and the doubled letter, because this
#: exists precisely for the messages a tidier parser would miss.
_DAYS_HINT = re.compile(
    r"\b(?:(\d+)\s*d[ae]y?s?\s*s?\s*a?go|(yesterday)|(today))\b", re.I)
_KIND_HINT = (
    ("transfer", r"\b(?:transfer|sent|send|paid to|payment to)\b"),
    ("airtime", r"\bairtime|recharge|top.?up\b"),
    ("data", r"\bdata|bundle|gb\b"),
    ("bill", r"\belectric|power|nepa|disco|cable|dstv|gotv|bill\b"),
    ("funding", r"\bfund(?:ing|ed)?|deposit\b"),
)


def lookup_hints(text: str) -> dict:
    """Amount / how-many-days-ago / type, read straight from the message.

    The model is asked for these and usually returns them, but "usually" is not
    a contract: a weaker provider, a rate-limited retry or a typo-laden sentence
    ("I sent 5k to someone 2 dayssgo") can come back with the right TOOL and no
    parameters at all - and a transaction_history call with nothing in it is the
    generic list-and-statement dump this whole change exists to stop.

    So the deterministic layer reads the message too, and its answers are used
    only to fill blanks the model left. Never to override it: when the model did
    extract a value it saw the whole sentence, and this regex saw a fragment.
    """
    low = str(text or "").lower()
    out: dict = {}

    m = _DAYS_HINT.search(low)
    if m:
        out["days_ago"] = 0 if m.group(3) else 1 if m.group(2) else int(m.group(1))

    # The amount must not swallow the "2" out of "2 days ago", nor a phone
    # number: only a figure with a currency mark, a k/m suffix, or a thousands
    # separator reads as money in these sentences.
    for raw, suffix in _AMOUNT_HINT.findall(low):
        digits = raw.replace(",", "")
        if not digits:
            continue
        try:
            value = Decimal(digits)
        except InvalidOperation:
            continue
        if suffix:
            value *= 1000 if suffix.lower() == "k" else 1_000_000
        elif "," not in raw and value < 100:
            continue          # "2" in "2 days ago" is not an amount
        elif len(digits) >= 10:
            continue          # a phone or account number, not a price
        out["amount"] = float(value)
        break

    for kind, pattern in _KIND_HINT:
        if re.search(pattern, low):
            out["kind"] = kind
            break
    return out


#: A Nigerian line as customers write it. Matched and REMOVED before the amount is
#: read, so "recharge 08012345678 with 500" cannot price the top-up at 8 billion.
_VAS_PHONE_RE = re.compile(r"(?:\+?234|0)\d{10}\b")
#: A data SIZE, not a price ("1gb", "500 mb") — removed before the amount too.
_VAS_SIZE_RE = re.compile(r"\b\d[\d.,]*\s*(?:gb|mb)\b", re.I)
#: "₦55", "N55", "55 naira", "55naira" (no space — as it was actually typed), "2k",
#: "1,500", "500". The currency word is part of the match rather than left to the
#: trailing boundary, which a digit running straight into a letter cannot satisfy.
_VAS_AMOUNT_RE = re.compile(
    r"(?:₦|\bngn\b|\bn(?=\d))?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m)?(?:\s*(?:naira|ngn))?\b",
    re.I)
_VAS_DATA_RE = re.compile(r"\b(?:data|bundle)\b|\d\s*(?:gb|mb)\b", re.I)
_VAS_AIRTIME_RE = re.compile(r"\b(?:airtime|recharge|top\s?-?\s?up|topup)\b", re.I)
#: Asking ABOUT a top-up is not asking to buy one, and opening a money flow off a
#: question is worse than the menu. "How much is 2k airtime?" stays a question.
_VAS_QUESTION_RE = re.compile(
    r"^\s*(?:how|what|why|when|where|which|can|could|does|do|is|are|did)\b|\?\s*$", re.I)
#: Above any real top-up, and far below a balance worth guessing at.
_VAS_MAX_AMOUNT = Decimal("100000")


#: Model intents that read what _SENSITIVE_READS protects, with the keyword each
#: one would have been typed as. It sat beside the VAS text-intent parser and
#: went out with it; none of its entries are VAS.
_GATED_INTENTS = {
    "check_balance": "balance",
    "transaction_history": "history",
    "account_details": "account details",
}


def dispatch_intent(user, msisdn: str, intent: dict, text: str = "") -> bool:
    """Map one LLM tool call to a deterministic flow. Returns False for
    clarify/unknown so the caller shows the menu. Money still requires the
    flow's confirm + PIN - the LLM only routes here."""
    from .flows import clean_narration

    name = intent.get("name")
    p = intent.get("input", {}) or {}
    # Only the two READ tools get the deterministic fallback. A money-moving
    # intent must never have its amount or its recipient inferred from a regex:
    # those are confirmed on a screen the customer reads, and a guess that gets
    # that far is a guess someone might approve.
    if text and name in ("transaction_history", "report_problem"):
        for key, value in lookup_hints(text).items():
            if p.get(key) in (None, ""):
                p = {**p, key: value}
    # The same idle gate the keywords pass through. "What's my balance?" reaches
    # the balance as surely as "balance" does, so it cannot skip the challenge
    # merely because the model, not the keyword table, recognised it. Savings
    # and loan reads gate themselves in savings_loans.
    if name in _GATED_INTENTS and _needs_reauth(ConversationState.for_msisdn(msisdn)):
        _send_unlock(user, msisdn, _GATED_INTENTS[name], intent={"name": name, "input": p})
        return True
    # Cleaned here, at the boundary, so a model that returns a newline or 300
    # characters cannot put either into a payload, a bank statement or a
    # rendered receipt. Reset in the finally below: a narration is a fact about
    # ONE message, and one that outlived its dispatch would attach the last
    # customer's words to the next customer's payment.
    token = _ai_narration.set(clean_narration(p.get("narration")))
    bank_token = _ai_bank.set(" ".join(str(p.get("bank_name") or "").split())[:40])
    try:
        return _dispatch_intent(user, msisdn, name, p)
    finally:
        _ai_narration.reset(token)
        _ai_bank.reset(bank_token)


def _dispatch_intent(user, msisdn: str, name, p: dict) -> bool:

    if name == "check_balance":
        _do_balance(user, msisdn)
        return True
    if name == "add_money":
        _do_add_money(user, msisdn)
        return True
    if name == "transfer":
        amt, acct, bank = p.get("amount"), p.get("account_number"), p.get("bank_name")
        # No bank_name is no longer a reason to fall back to the guided form:
        # _begin_bank_transfer reads the bank off the account number's checksum
        # when the message didn't name one, and only returns False when even
        # that can't resolve it.
        if amt and acct:
            try:
                if _begin_bank_transfer(user, msisdn, Decimal(str(amt)), re.sub(r"\D", "", str(acct)), str(bank or "")):
                    return True
            except (InvalidOperation, TypeError):
                pass
        _start_transfer(user, msisdn)  # partial details -> guided flow
        return True
    if name == "convert_currency":
        _start_convert(user, msisdn)
        return True
    if name == "transaction_history":
        _do_history(user, msisdn, p.get("count"),
                    amount=p.get("amount"), days_ago=p.get("days_ago"),
                    kind=p.get("kind"), recipient=p.get("recipient"),
                    status=p.get("status"), as_document=p.get("as_document"))
        return True
    if name == "account_details":
        _do_account_details(user, msisdn)
        return True
    if name == "verify_identity":
        _start_kyc(user, msisdn)
        return True
    if name == "reset_pin":
        # Opens the secure reset ladder. The PIN itself is never collected in
        # the chat - same path the "reset pin" keyword takes.
        _start_pin_reset(user, msisdn)
        return True
    if name == "contact_support":
        _do_support(msisdn)
        return True
    if name == "check_loan_balance":
        _do_loan_balance(user, msisdn)
        return True
    if name == "check_savings_balance":
        _do_savings_balance(user, msisdn)
        return True
    if name == "report_problem":
        _do_report_problem(user, msisdn, amount=p.get("amount"), days_ago=p.get("days_ago"),
                           kind=p.get("kind"), recipient=p.get("recipient"),
                           reference=p.get("reference"), reason=p.get("reason"),
                           detail=p.get("detail"))
        return True
    return False  # clarify / unknown


#: Nigerian mobile prefixes by network, national form. Used only to fill in a
#: network the customer did not state - never to override one they did.
_NETWORK_PREFIXES = {
    "1": ("0803", "0806", "0703", "0706", "0813", "0816", "0810", "0814", "0903", "0906", "0913", "0916"),
    "2": ("0805", "0807", "0705", "0815", "0811", "0905", "0915"),
    "3": ("0802", "0808", "0708", "0812", "0701", "0902", "0901", "0904", "0907", "0912"),
    "4": ("0809", "0817", "0818", "0908", "0909"),
}
_PREFIX_TO_NETWORK = {p: net for net, prefixes in _NETWORK_PREFIXES.items() for p in prefixes}


def _network_from_prefix(phone) -> str | None:
    """The network a Nigerian mobile number belongs to, or None if unrecognised.

    Ported numbers make this a guess, not a fact - which is why it only ever
    pre-fills a confirm screen the customer still has to approve, and why a
    network they stated always wins.
    """
    digits = re.sub(r"\D", "", str(phone or ""))
    if digits.startswith("234"):
        digits = "0" + digits[3:]
    return _PREFIX_TO_NETWORK.get(digits[:4]) if len(digits) >= 10 else None


# --------------------------------------------------------------------------- #
# currency conversion (FX) - quote -> PIN-within-TTL -> settle (Fincra rail)
# --------------------------------------------------------------------------- #
CONVERT_CCYS = ["NGN", "USD", "GBP", "CAD"]  # settle-able; CNY is quote-only (blocked)


def _start_convert(user, msisdn: str) -> None:
    if not product_available("fx"):
        return reply(msisdn, PRODUCT_MESSAGES["fx"])
    if _blocked_from_spending(user, msisdn):
        return None
    _new_flow(user, msisdn, "convert", "from")
    reply(msisdn, "Convert currency.\nWhich currency are you selling? (NGN, USD, GBP, CAD)")


def _advance_convert(pa: PendingAction, user, msisdn: str, text: str) -> None:
    if not product_available("fx"):
        _clear_actions(msisdn)
        return reply(msisdn, PRODUCT_MESSAGES["fx"])
    st = pa.state
    if st == "from":
        c = text.strip().upper()
        if c not in CONVERT_CCYS:
            return reply(msisdn, "Reply a currency code: NGN, USD, GBP or CAD.")
        pa.payload["from"] = c
        _touch(pa, state="to", payload=pa.payload)
        return reply(msisdn, "Which currency do you want to receive?")
    if st == "to":
        c = text.strip().upper()
        if c not in CONVERT_CCYS:
            return reply(msisdn, "Reply a currency code: NGN, USD, GBP or CAD.")
        if c == pa.payload["from"]:
            return reply(msisdn, "Pick a different currency to receive.")
        pa.payload["to"] = c
        _touch(pa, state="amount", payload=pa.payload)
        return reply(msisdn, f"How much {pa.payload['from']} do you want to sell?")
    if st == "amount":
        amount = parse_amount(text)
        if amount is None or amount <= 0:
            return reply(msisdn, "Enter a valid amount.")
        try:
            quote = create_fx_quote(user, pa.payload["from"], pa.payload["to"], amount)
        except FxError as exc:
            _clear_actions(msisdn)
            return reply(msisdn, exc.message)
        pa.payload["quote_ref"] = quote.quote_ref
        if not _arm_confirm(pa, user):
            return
        secs = max(1, int((quote.expires_at - timezone.now()).total_seconds()))
        return _send_confirm(
            pa, msisdn,
            "Confirm conversion\n"
            f"Sell {quote.sell_amount:,.2f} {quote.from_currency} -> "
            f"Receive {quote.receive_amount:,.2f} {quote.to_currency}\n"
            f"Rate {quote.rate:.4f} • expires in {secs}s")
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        return _exec_convert(pa, user, msisdn)
    _clear_actions(msisdn)
    return send_menu(msisdn)


def _exec_convert(pa: PendingAction, user, msisdn: str) -> str:
    try:
        quote = execute_fx(user, pa.payload["quote_ref"],
                           idempotency_key=f"wa-fx-{pa.id}", channel="whatsapp")
    except FxError as exc:
        _clear_actions(msisdn)
        reply(msisdn, exc.message)
        # Tagged, like every other executor. Untagged fell through to the neutral
        # "Done" heading, so a refused conversion closed the Flow on the same word
        # a successful one did - the exact tell-them-apart-at-a-glance failure the
        # status heading was added to end, still live on this one path.
        return Outcome(exc.message, OUTCOME_FAILED)
    _clear_actions(msisdn)
    new_bal = currency_balance(user, quote.to_currency)
    line = (f"✅ Converted. -{quote.sell_amount:,.2f} {quote.from_currency} / "
            f"+{quote.receive_amount:,.2f} {quote.to_currency}. "
            f"New {quote.to_currency} balance: {new_bal:,.2f}.")
    reply(msisdn, line)
    return Outcome(line, OUTCOME_SUCCESS)


# --------------------------------------------------------------------------- #
# Secure-Flow execution dispatch - the Flows endpoint (whatsapp.flows) calls this
# AFTER verifying the PIN, so a Flow-confirmed action runs the exact same money
# path as the chat PIN path.
# --------------------------------------------------------------------------- #
_UNLINKED_CHANNEL = ("This WhatsApp number is no longer connected to your Zitch account, "
                     "so nothing was sent and you were not charged.")


def authorise_flow_execution(pa: PendingAction, user) -> str:
    """The PIN just passed. Get the money OFF Meta's clock.

    A Flows data-exchange must be answered within 10 seconds or the customer is
    shown "Couldn't load content. Try again later." - and executing a transfer
    here means a name enquiry, a payout to the bank rail, a rendered receipt, a
    media upload and two Graph sends, all in sequence. That is routinely more
    than ten seconds, so the customer was shown a failure for a payment that had
    in fact gone through, with the money already gone.

    So the endpoint answers as soon as the PIN is verified, and the payment runs
    where every chat-confirmed payment has always run: the durable queue, with
    its lease, retries and dead-letter. The outcome arrives in the chat, which is
    where the receipt was always going to land.

    Inline mode (dev, tests, and any host with no background execution at all)
    keeps running it in-process, exactly as the webhook does - see
    WHATSAPP_PROCESS_INLINE.
    """
    if getattr(settings, "WHATSAPP_PROCESS_INLINE", False):
        return run_flow_execution(pa, user)
    from .jobs import drain_in_background, enqueue_flow_execution
    proof = (getattr(pa, "_unlock_proof", None) or pa.payload
             if pa.action_type == "unlock" else {})

    # Claim and persist the work in ONE transaction. Committing `executing`
    # before the queue insert strands an authorised payment if encryption,
    # insertion or the process fails between them. Re-read the action under a
    # lock as well: cancellation/expiry must win if it happened during PIN
    # verification, and stale duplicate submissions must not resurrect it.
    with db_transaction.atomic():
        live = PendingAction.objects.select_for_update().filter(
            pk=pa.pk, user_id=user.pk, msisdn=pa.msisdn,
            state__in=(FLOW_PIN_STATE, "pin", EXECUTING_STATE),
        ).first()
        if live is not None and is_retired_setup(live):
            live.delete()
            return Outcome(RETIRED_SETUP_MESSAGE, OUTCOME_FAILED)
        if live is None or (live.expired and live.state != EXECUTING_STATE):
            return Outcome("This request expired or was cancelled. Start again in the chat.",
                           OUTCOME_FAILED)
        current = get_user_model().objects.select_for_update().filter(pk=user.pk).first()
        if current is None or not current.is_active:
            return Outcome("Your Zitch account is currently suspended. Please contact support.",
                           OUTCOME_FAILED)
        if not _channel_linked(current, live.msisdn):
            if live.state != EXECUTING_STATE:
                live.delete()
            return Outcome(_UNLINKED_CHANNEL, OUTCOME_FAILED)
        if live.state != EXECUTING_STATE:
            live.state = EXECUTING_STATE
            live.expires_at = timezone.now() + EXECUTION_TTL
            # An unlock resumes sensitive reads, so its proof belongs to this
            # exact channel and credential snapshot, even if the worker is slow.
            if live.action_type == "unlock":
                from .auth_proof import credentials
                if (not secrets.compare_digest(str(proof.get("unlock_credentials") or ""), credentials(current))
                        or not WhatsAppLink.objects.filter(pk=proof.get("unlock_link_id"),
                            user=current, wa_msisdn=live.msisdn, status=WhatsAppLink.ACTIVE).exists()):
                    live.delete()
                    return Outcome("This sign-in changed. Request your details again in the chat.", OUTCOME_FAILED)
                live.payload.update({key: proof[key] for key in ("unlock_link_id", "unlock_credentials")})
            else:
                _mark_verified(live.msisdn)
            live.save(update_fields=["state", "expires_at", "payload"])
        enqueue_flow_execution(live)
        pa, user = live, current
    # The worker may finish after this exchange times out. Keep a short-lived,
    # token-scoped pointer so the published RESULT screen's Done exchange can
    # re-read the ledger instead of closing on stale Pending.
    from .flows import remember_pending
    if pa.action_type != "unlock":
        remember_pending(pa, user)
    # Same safety net the webhook uses for a worker that isn't running, and off
    # the request thread so Meta's answer is not held up by it. Not under the
    # test runner, where the suite drives the queue itself and a second thread
    # would only race it.
    if not getattr(settings, "TESTING", False):
        db_transaction.on_commit(drain_in_background)
    # Unlock is an authentication result, not a money movement. There is no
    # transaction row for _await_settlement to find, so sending it through the
    # payment-status fallback mislabeled every successful unlock as "Pending".
    # The requested account/details command still runs on the durable worker;
    # report only what is already true here: identity was confirmed.
    if pa.action_type == "unlock":
        from .flows import forget_pending, sign_flow_token
        forget_pending(sign_flow_token(pa))
        return Outcome("Identity confirmed - your requested details will appear "
                       "in the chat.", "done")

    # Give the rail a moment to answer before closing the Flow. Without this,
    # "Successful" was unreachable in production: every money Flow closed on
    # "Pending" because the endpoint replied the instant the job was queued, so
    # the customer's last word from the Flow was always about a payment that had
    # not been attempted yet.
    settled = _await_settlement(pa.id, user, pa.action_type)
    if settled is not None:
        from .flows import forget_pending, sign_flow_token
        if getattr(settled, "status", "") != OUTCOME_PENDING:
            forget_pending(sign_flow_token(pa), status=settled.status)
        return settled
    # Still working. PENDING, emphatically not success: the rail has not answered
    # yet, and a tick here would read as "done" for a payment that may still
    # fail - the one thing a banking channel must never say. The receipt in the
    # chat remains the authoritative outcome.
    return Outcome("Your payment is processing. Tap Done to check its status; "
                   "we will also confirm the outcome in the chat.", OUTCOME_PENDING)


#: What the settled screen calls each action. "Sent" is true of a transfer and
#: false of everything else - a meter token or a data bundle is bought, not sent -
#: and this screen is the one place the customer is told the money moved, so it
#: should not describe their electricity payment as something posted to someone.
_SETTLED_VERB = {
    "transfer": "Sent",
    "airtime": "Airtime purchased",
    "data": "Data bundle purchased",
    "electricity": "Electricity paid",
    "cable": "Subscription paid",
    "convert": "Converted",
    "savings_create": "Fixed Save created",
    "loan_repay": "Loan repayment completed",
}


def _await_settlement(action_id: int, user, action_type: str = ""):
    """Poll the ledger for this action's outcome, briefly. Returns a tagged
    Outcome once the row is terminal, or None if it is still processing.

    Bounded by WHATSAPP_FLOW_SETTLE_WAIT (default 3s, hard-capped at 6). Meta
    allows the data-exchange about ten seconds before showing the customer
    "Couldn't load content. Try again later." - the failure that moving
    execution off this thread was introduced to fix - so this deliberately stays
    far from that ceiling. It also occupies a gunicorn thread, and the web dyno
    serves /healthz from the same pool of eight.

    Waiting changes NOTHING about the payment: it is queued and executing either
    way, and the chat receipt is sent by the worker regardless. All this decides
    is which heading the closing screen can honestly show.
    """
    from wallet.models import Transaction

    configured_budget = getattr(settings, "WHATSAPP_FLOW_SETTLE_WAIT", 3)
    budget = 3 if configured_budget is None else float(configured_budget)
    budget = max(0, min(budget, 6))
    if budget <= 0:
        return None
    # The key every executor stamps its ledger row with - except FX, which has
    # always used its own prefix. Polling `wa-<id>` for a conversion therefore
    # matched nothing and timed out into "Pending" every single time, however
    # fast the rail answered.
    key = f"wa-fx-{action_id}" if action_type == "convert" else f"wa-{action_id}"
    deadline = time.monotonic() + budget
    while True:
        # .only() because this runs on the request thread and the ledger row is
        # wide; the status and the reference are all that is read.
        txn = (Transaction.objects.filter(user=user, idempotency_key=key)
               .only("transaction_status", "reference", "meta").first())
        if txn is not None and txn.transaction_status in (Transaction.SUCCESS, Transaction.FAILED):
            from wallet.review_state import transaction_review_map
            if txn.pk in transaction_review_map([txn]):
                return Outcome("This payment is under review. Do not send it again; "
                               "we will confirm its final outcome in the chat.", OUTCOME_PENDING)
            if txn.transaction_status == Transaction.SUCCESS:
                verb = _SETTLED_VERB.get(action_type, "Done")
                return Outcome(f"{verb} - the receipt is in your chat.", OUTCOME_SUCCESS)
            # A failure is worth waiting for too: it is the one outcome the
            # customer should see BEFORE the screen closes, not only in a chat
            # message they may scroll past.
            return Outcome("That payment was not completed. Check your balance and the chat "
                           "for any reversal details before trying again.", OUTCOME_FAILED)
        if time.monotonic() >= deadline:
            return None
        time.sleep(_SETTLE_POLL)


def run_flow_execution(pa: PendingAction, user) -> str:
    # Token resolution and PIN verification happen before this call. Re-read and
    # lock both records briefly so a concurrent cancel, replay, expiry or account
    # freeze cannot race past the final execution boundary.
    #
    # Do NOT wrap the provider call below in this transaction. The PENDING ledger
    # row must be committed before the bank call, so a callback, requery or
    # reconciliation that looks the reference up while the call is in flight can
    # see it. Keep only the claim/eligibility check atomic; the executor's own
    # debit() transaction then commits the row before the bank network call.
    with db_transaction.atomic():
        live = PendingAction.objects.select_for_update().filter(
            pk=pa.pk,
            user_id=user.pk,
            msisdn=pa.msisdn,
            state__in=(FLOW_PIN_STATE, "pin", EXECUTING_STATE),
        ).first()
        if live is not None and is_retired_setup(live):
            live.delete()
            return Outcome(RETIRED_SETUP_MESSAGE, OUTCOME_FAILED)
        if live is None:
            return Outcome("This request expired or was cancelled. Start again in the chat.",
                           OUTCOME_FAILED)
        # An action already authorised is past the point where expiry means anything:
        # the PIN was accepted inside the window, and the clock that ran out was the
        # one measuring how long the customer had to confirm. Dropping it here would
        # discard a payment the customer was told was on its way.
        if live.expired and live.state != EXECUTING_STATE:
            live.delete()
            return Outcome("This request expired or was cancelled. Start again in the chat.",
                           OUTCOME_FAILED)

        live_user = get_user_model().objects.select_for_update().filter(pk=user.pk).first()
        if live_user is None or not live_user.is_active:
            live.delete()
            return Outcome("Your Zitch account is currently suspended. Please contact support.",
                           OUTCOME_FAILED)
        # Unlinking cuts off the phone that armed this. An already-authorised
        # payment still completes; an unconfirmed one does not.
        if (live.state != EXECUTING_STATE or live.action_type == "unlock") and not _channel_linked(live_user, live.msisdn):
            live.delete()
            return Outcome(_UNLINKED_CHANNEL, OUTCOME_FAILED)

        proof = getattr(pa, "_unlock_proof", None)
        if proof is not None:
            live._unlock_proof = proof
        pa = live
        user = live_user
    # Getting here means the PIN or a verified biometric just passed, so it
    # starts the re-auth window: someone who just authorised a payment should
    # not be challenged again to read their own balance.
    # Unlock also binds the proof to the exact active WhatsApp link
    # and credential snapshot. Its executor marks the session only after those
    # checks, so a revoked old link cannot authenticate a replacement link.
    if (pa.action_type != "unlock" and pa.state != EXECUTING_STATE
            and _channel_linked(user, pa.msisdn)):
        _mark_verified(pa.msisdn)
    from banking.chat import execute as execute_bank_vas
    executors = {
        "bank_vas": execute_bank_vas,
        "transfer": _exec_transfer, "convert": _exec_convert,
        "unlock": _exec_unlock,
        "savings_create": savings_loans.execute_product,
        "loan_repay": savings_loans.execute_product,
    }
    fn = executors.get(pa.action_type)
    if fn is None:
        PendingAction.objects.filter(pk=pa.pk).delete()
        return Outcome("Sorry, this action can't be completed here. Please try again in the chat.",
                       OUTCOME_FAILED)
    outcome = fn(pa, user, pa.msisdn) or "Done ✅"
    # Inline execution has no authorise_flow_execution wrapper to record the
    # delayed-status pointer. Record it here if the rail is still processing.
    if getattr(outcome, "status", "") == OUTCOME_PENDING:
        from .flows import remember_pending
        remember_pending(pa, user)
    # Remember how this ended, keyed on the action id. The confirm card that armed
    # it is still sitting in the thread with a live "Use PIN instead" button -
    # WhatsApp cannot take that back - so tapping it afterwards has to be able to
    # say "already paid" instead of "expired". Best-effort: a cache miss costs
    # wording on a stale button, never the payment.
    if getattr(outcome, "status", "") == OUTCOME_SUCCESS:
        from .flows import remember_settled

        remember_settled(pa, str(outcome))
    # Only the executor may retire an authorised action. Chat cancel/menu cannot
    # erase a queued payment or misreport cancellation after PIN confirmation.
    PendingAction.objects.filter(pk=pa.pk, user_id=user.pk).delete()
    return outcome
