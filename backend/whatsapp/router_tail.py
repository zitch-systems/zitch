
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
        if get_or_create_wallet(user).balance < amount:
            return reply(msisdn, f"Insufficient balance. You have {_money(get_or_create_wallet(user).balance)}. "
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
        bank = Bank.objects.filter(code=choices[idx]).first()
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
                  f"{bank.name} • {acct}")


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


def _exec_transfer(pa: PendingAction, user, msisdn: str) -> str:
    """Execute a PIN-confirmed transfer (called by the chat PIN path AND the
    secure Flow endpoint). Sends the chat receipt and returns a short outcome
    line for the Flow success screen."""
    if velocity_exceeded(user):  # same fraud brake the app enforces (parity)
        _clear_actions(msisdn)
        msg = "Too many transactions in a short time. Please wait a few minutes and try again."
        reply(msisdn, msg)
        return Outcome(msg, OUTCOME_FAILED)
    amount = Decimal(pa.payload["amount"])
    bank = Bank.objects.filter(bank_code=pa.payload["bank_code"]).first()
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
            idempotency_key=f"wa-{pa.id}", channel="whatsapp",
        )
    except PayoutError as exc:
        _clear_actions(msisdn)
        who = pa.payload["name"].upper()
        if exc.kind == "insufficient":
            msg = f"Insufficient balance for the {_money(amount)} transfer to {who} - cancelled."
            reply(msisdn, msg)
            return Outcome(msg, OUTCOME_FAILED)
        if exc.kind == "duplicate":
            msg = f"That {_money(amount)} transfer to {who} was already processed."
            reply(msisdn, msg)
            return Outcome(msg, OUTCOME_FAILED)
        msg = f"Transfer of {_money(amount)} to {who} failed: {exc.message}"
        reply(msisdn, msg)
        return Outcome(msg, OUTCOME_FAILED)

    _clear_actions(msisdn)

    # execute_payout returns a PENDING row for a queued (PROCESSING) transfer
    # AND for the ambiguous send-timeout / lost-response case where the rail may
    # or may not have paid the recipient (transfers/services.py holds the debit
    # rather than refunding a maybe-delivered transfer). Only a SETTLED-success
    # row may be announced as "Successful": a receipt is a forwardable proof of
    # payment, and issuing one for a transfer that is only pending - or may have
    # failed - is the single worst thing a banking channel can tell a customer.
    # Mirror _run_vtu's pending branch and the app path (transfers/views.py),
    # which both report "processing" and let the webhook / reconciler settle it.
    from wallet.alerts import mark_awaiting_settlement
    from wallet.models import Transaction

    if txn.transaction_status != Transaction.SUCCESS:
        # "processing" is not an outcome, so the settlement alert has to be let
        # through when the row finally resolves - otherwise this line is the last
        # word the customer ever gets on the money.
        mark_awaiting_settlement(txn)
        line = (f"⏳ Your transfer of {_money(amount)} to {pa.payload['name'].upper()} "
                f"is processing - we'll confirm once it settles. Ref {txn.reference}.")
        reply(msisdn, line)
        return Outcome(line, OUTCOME_PENDING)

    wallet = get_or_create_wallet(user)
    reply_receipt(msisdn, "Transfer receipt", _with_narration(pa, [
        ("To", pa.payload["name"].upper()),
        ("Bank", pa.payload["bank_name"]),
        ("Account", pa.payload["account"]),
        ("Amount", _money(amount)),
        ("Reference", txn.reference),
        ("Date", timezone.now().strftime("%d %b %Y, %H:%M")),
    ]), ref=txn.reference, user=user, balance_after=wallet.balance)
    # Offered against the exact row execute_payout just wrote, so the tap that
    # follows needs no account number and no second name enquiry to know who it
    # means. Silent for a recipient already saved or already declined.
    _offer_to_save(user, msisdn, getattr(txn, "beneficiary_id", None))
    # A settled transfer is the one case that may be CALLED successful, and it is
    # now said rather than inferred: this used to return whatever reply_receipt
    # gave back, which run_flow_execution turned into a bare "Done ✅" - the same
    # words a cancelled transfer closed on.
    return Outcome(f"{_money(amount)} sent to {pa.payload['name'].upper()} - "
                   f"the receipt is in your chat.", OUTCOME_SUCCESS)


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
    if _blocked_from_spending(user, msisdn):
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
        reply(msisdn, f"Insufficient balance. You have {_money(get_or_create_wallet(user).balance)}.")
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


# --------------------------------------------------------------------------- #
# VTU + bills (airtime / data / electricity / cable) - reuse run_provider_purchase
# --------------------------------------------------------------------------- #
NETWORK_PROMPT = "Which network?\n" + "\n".join(f"{k}  {v}" for k, v in NETWORK_NAMES.items())
DISCO_PROMPT = "Which disco?\n" + "\n".join(f"{k}  {v}" for k, v in DISCO_NAMES.items())
CABLE_PROMPT = "Which provider?\n" + "\n".join(f"{k}  {v}" for k, v in CABLE_NAMES.items())

# What customers actually call their disco. DISCO_NAMES holds the short label the
# menu shows ("Ikeja"), but nobody says that: they say IKEDC, or "Ikeja Electric",
# and a bill arrives with the abbreviation on it. Without these, naming your disco
# in the message got you the same "Which disco?" list as saying nothing at all.
#
# Note what is deliberately ABSENT: "nepa" (and "phcn"). Both mean electricity in
# general, not a company - "load my nepa bill" says nothing about which disco, and
# guessing one from it would put the wrong meter in front of a payment.
DISCO_ALIASES = {
    "ikedc": "1", "ikejaelectric": "1", "ikejaelectricity": "1", "ikejadisco": "1",
    "ikejadistribution": "1", "ikejaelectriccompany": "1",
    "ekedc": "2", "ekoelectric": "2", "ekoelectricity": "2", "ekodisco": "2",
    "ekodistribution": "2", "ekoelectricitydistributioncompany": "2",
    "aedc": "3", "abujaelectric": "3", "abujaelectricity": "3", "abujadisco": "3",
    "abujadistribution": "3",
    "kedco": "4", "kanoelectric": "4", "kanoelectricity": "4", "kanodisco": "4",
    "kanodistribution": "4",
    "phed": "5", "phedc": "5", "portharcourtelectric": "5", "portharcourtdisco": "5",
    "portharcourtelectricity": "5", "phdisco": "5", "phelectric": "5",
    "jed": "6", "jedc": "6", "jedplc": "6", "joselectric": "6", "josdisco": "6",
    "joselectricity": "6",
    "kaedco": "7", "kadunaelectric": "7", "kadunadisco": "7", "kadunaelectricity": "7",
    "eedc": "8", "enuguelectric": "8", "enugudisco": "8", "enuguelectricity": "8",
    "ibedc": "9", "ibadanelectric": "9", "ibadandisco": "9", "ibadanelectricity": "9",
}
# The cable equivalents. "gotv"/"dstv" already match CABLE_NAMES exactly; these are
# the spellings and parent-brand names that do not.
CABLE_ALIASES = {
    "go": "1", "gotvnigeria": "1", "multichoicegotv": "1",
    "dstvnigeria": "2", "multichoice": "2", "dstvng": "2",
    "startime": "3", "startimesnigeria": "3", "startimestv": "3",
}


def _alias_id(text, names: dict, aliases: dict) -> str | None:
    """Resolve a biller the customer NAMED to its menu id - the number, the menu
    label, or any of the names the thing is actually known by."""
    direct = _choice_id(str(text or ""), names)
    if direct is not None:
        return direct
    key = re.sub(r"[^a-z0-9]", "", str(text or "").lower())
    return aliases.get(key) if key else None


def _disco_id(text) -> str | None:
    return _alias_id(text, DISCO_NAMES, DISCO_ALIASES)


def _cable_id(text) -> str | None:
    return _alias_id(text, CABLE_NAMES, CABLE_ALIASES)


def _meter_type(text) -> str | None:
    """Prepaid or postpaid, however it arrives: the menu digit, the word typed
    out, or the LLM's `variation` field ("prepaid meter", "POSTPAID")."""
    raw = str(text or "").strip().lower()
    if raw in ("1", "2"):
        return "prepaid" if raw == "1" else "postpaid"
    t = re.sub(r"[^a-z]", "", raw)
    if "postpaid" in t:
        return "postpaid"
    if "prepaid" in t:
        return "prepaid"
    return None


#: The narration the CURRENT message asked for, for the duration of dispatching
#: it. Set once by dispatch_intent and read once by _new_flow.
#:
#: A context variable rather than a parameter because the alternative is
#: threading an optional string through every _begin_*/_start_* entry point and
#: every _new_flow call inside them - eight signatures to carry one word that
#: only the AI path can supply, where each new money flow added later is another
#: chance to forget it. Scoped to one dispatch and cleared in a finally, so it
#: cannot leak into the next message; a ContextVar (not a global) so concurrent
#: requests on the same worker cannot read each other's.
_ai_narration: contextvars.ContextVar[str] = contextvars.ContextVar("ai_narration", default="")

#: The bank the CURRENT message named, for the length of dispatching it.
#:
#: Same mechanism and same reason as the narration above. A customer who says
#: "send 5k to my Kuda account" has already told us the bank; opening the transfer
#: form on the full list of every Nigerian bank asks them again, which is the
#: channel telling them it did not read their message. The AI extracts bank_name
#: whenever it is stated, and _start_transfer used to throw it away - the same
#: discard that lost the narration on this exact path.
_ai_bank: contextvars.ContextVar[str] = contextvars.ContextVar("ai_bank", default="")

#: Action types with money and a receipt behind them - the only ones a narration
#: means anything for. An unlock or a PIN reset has nothing to narrate.
_NARRATABLE = ("transfer", "airtime", "data", "electricity", "cable", "exam")


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
    digits = re.sub(r"\D", "", str(getattr(user, "phone", "") or ""))
    if len(digits) == 13 and digits.startswith("234"):
        digits = "0" + digits[3:]
    elif len(digits) == 10:
        digits = "0" + digits
    return digits if len(digits) >= 10 else None


def _phone_from(text: str, user) -> str | None:
    """'me' -> the user's own number; else the digits typed (>= 10)."""
    if text.strip().lower() in ("me", "self", "mine"):
        return _own_phone(user)
    digits = re.sub(r"\D", "", text)
    return digits if len(digits) >= 10 else None


def _insufficient(user, amount: Decimal) -> bool:
    return get_or_create_wallet(user).balance < amount


def _vtu_detail(pa: PendingAction, amount: Decimal) -> str:
    """The amount + who/what this VTU purchase was for, as one parenthetical -
    the same recipient/details the confirm screen showed. Every outcome line
    (pending, failed, or an early refusal) quotes this so a customer reading
    only the LAST message in the thread still knows what was being paid for,
    not just that something was."""
    fields = _flow_fields(pa)
    recip = " · ".join(x for x in (fields.get("recipient", ""), fields.get("details", "")) if x)
    return f"{_money(amount)}{' - ' + recip if recip else ''}"


def _run_vtu(pa: PendingAction, user, msisdn: str, amount: Decimal, label: str,
             provider_call, receipt) -> str:
    """Debit -> provider -> settle via the shared run_provider_purchase, then send
    the outcome. On success `receipt(txn, result)` returns ``(title, rows)`` and we
    send a branded, downloadable receipt JPEG. Returns the outcome text (also used
    verbatim on the secure Flow's success screen)."""
    detail = _vtu_detail(pa, amount)
    # Enforce the per-txn tier ceiling + large-transfer face step-up here, so EVERY
    # VTU path is gated regardless of entry point (the AI-prefilled fast-paths reach
    # this without the guided flow's own send_limit_error check, which would
    # otherwise let a Tier-3-without-face user skip the >=₦100k face requirement).
    send_msg = send_limit_error(user, amount)
    if send_msg:
        _clear_actions(msisdn)
        _limit_reply(msisdn, user, send_msg)
        return Outcome(send_msg, OUTCOME_FAILED)
    bill_limit_msg = daily_limit_error(user, amount, "bill")
    if bill_limit_msg:
        _clear_actions(msisdn)
        _limit_reply(msisdn, user, bill_limit_msg)
        return Outcome(bill_limit_msg, OUTCOME_FAILED)
    if velocity_exceeded(user):  # same fraud brake the app enforces (parity)
        _clear_actions(msisdn)
        msg = "Too many transactions in a short time. Please wait a few minutes and try again."
        reply(msisdn, msg)
        return Outcome(msg, OUTCOME_FAILED)
    try:
        # The note rides the ledger row so the settlement alert can quote it
        # later - by then the pending action is long gone, and the alert is
        # often the only thing the customer reads about a bill they paid.
        purchase_meta = {**pa.payload.get("meta", {}), "channel": "whatsapp"}
        if _narration(pa):
            purchase_meta["narration"] = _narration(pa)
        status, txn, result = run_provider_purchase(
            user, amount, label, purchase_meta, provider_call,
            idempotency_key=f"wa-{pa.id}",
        )
    except InsufficientFunds:
        _clear_actions(msisdn)
        line = f"Insufficient balance for {label} ({detail}) - cancelled."
        reply(msisdn, line)
        return Outcome(line, OUTCOME_FAILED)
    except LimitExceeded as exc:
        _clear_actions(msisdn)
        line = f"{exc} ({label}, {detail})"
        reply(msisdn, line)
        return Outcome(line, OUTCOME_FAILED)
    except DuplicateTransaction:
        _clear_actions(msisdn)
        line = f"That {label} ({detail}) was already processed."
        reply(msisdn, line)
        return Outcome(line, OUTCOME_FAILED)
    _clear_actions(msisdn)
    if status == "success":
        title, rows = receipt(txn, result)
        reply_receipt(msisdn, title, _with_narration(pa, rows), ref=txn.reference,
                      user=user, balance_after=get_or_create_wallet(user).balance)
        # Named rather than inferred, for the same reason as the transfer path.
        return Outcome(f"{label} successful - the receipt is in your chat.", OUTCOME_SUCCESS)
    if status == "pending":
        # Same as the transfer path: the chat can only say "processing", so the
        # alert on the eventual settlement must not be de-duped away as an echo
        # of a receipt this branch never sent.
        from wallet.alerts import mark_awaiting_settlement

        mark_awaiting_settlement(txn)
        line = (f"⏳ Your {label} ({detail}) is processing - we'll confirm shortly. "
                f"Ref {txn.reference}.")
        reply(msisdn, line)
        return Outcome(line, OUTCOME_PENDING)
    # Not result["message"]: an empty provider float comes back phrased as the
    # CUSTOMER's balance being too low, and this line is also what the Flow's
    # "Not completed" page renders. See wallet.services.customer_safe_failure.
    line = (f"❌ {label} ({detail}) failed: "
            f"{customer_safe_failure(result, service=pa.action_type)}. "
            f"You were not charged.")
    reply(msisdn, line)
    return Outcome(line, OUTCOME_FAILED)


# ---- service sub-menus (tap 1 or 2 - no need to type "airtime"/"data" etc.) ----
# Each entry: (prompt, [(id, label, desc), ...]). The row ids ("1"/"2") are what
# the router receives back from a tap or a typed number, mirroring the network menu.
SERVICE_MENUS = {
    "airtime_data": ("What would you like to buy?",
                     [("1", "Airtime", ""), ("2", "Data", "")]),
    "bill": ("Which bill would you like to pay?",
             [("1", "Electricity", ""), ("2", "Cable TV", "")]),
}


def _start_vtu(user, msisdn: str) -> None:
    """Menu 3 - airtime and data as ONE Flow instead of four chat round-trips.

    The chat ladder asked what to buy, then the network, then the number, then
    the amount, each its own message and each a place to get stuck; the customer
    in the screenshot was four taps in and still had two to go. The Flow asks the
    same four things as four pages of one session and chains into the PIN, so the
    whole purchase is one interaction.

    Same fallback as the transfer form: if the Flow cannot be sent, the guided
    chat sub-menu still works. Failing closed would take airtime away from every
    deploy without Flows configured, and nothing here is secret - the PIN is
    still collected on its own encrypted page either way.
    """
    if _blocked_from_spending(user, msisdn):
        return None
    _clear_actions(msisdn)
    if flows_live():
        # action_type is provisional: the FIRST page decides airtime vs data and
        # rewrites it, because the executor is chosen by action_type later.
        pa = _new_flow(user, msisdn, "airtime", FLOW_VTU_STATE,
                       {"vtu_step": "kind", "pin_attempts": 0})
        res = send_flow(
            msisdn, sign_flow_token(pa),
            header="Airtime & data",
            body="Pick what you need and we'll take it from there - your PIN stays private.",
            screen=VTU_SCREEN, screen_data={"error": ""},
            cta="Buy",
        )
        if res.get("success"):
            return reply(msisdn, "📱 Tap *Buy* on the secure form above.")
        log.warning("wa_vtu_flow_send_failed pa=%s detail=%r", pa.id, res.get("error_detail", ""))
        _clear_actions(msisdn)
    return _start_service_menu(user, msisdn, "airtime_data")


def _start_service_menu(user, msisdn: str, kind: str) -> None:
    """Open a category sub-menu so the user picks 1 or 2 (or taps the row) instead
    of having to type the word. Stored as a `pick_service` flow so a bare "1"/"2"
    is read as the sub-menu choice, not the main-menu balance/transfer number."""
    body, rows = SERVICE_MENUS[kind]
    _new_flow(user, msisdn, "pick_service", kind)
    reply_list(msisdn, body, rows, button_label="Choose")


def _advance_pick_service(pa: PendingAction, user, msisdn: str, text: str) -> None:
    """Route a sub-menu pick to the matching guided flow. Accepts the number
    (1/2) or the word, so a tap, a typed number and typed text all work. Each
    _start_* clears this pending action (via _new_flow) as it opens its own flow."""
    choice = text.strip().lower()
    if pa.state == "airtime_data":
        if choice in ("1", "airtime"):
            return _start_airtime(user, msisdn)
        if choice in ("2", "data"):
            return _start_data(user, msisdn)
        _, rows = SERVICE_MENUS["airtime_data"]
        return reply_list(msisdn, "Reply 1 for Airtime or 2 for Data.", rows, button_label="Choose")
    if choice in ("1", "electricity", "light", "nepa", "power"):
        return _start_electricity(user, msisdn)
    if choice in ("2", "cable", "tv", "dstv", "gotv", "startimes"):
        return _start_cable(user, msisdn)
    _, rows = SERVICE_MENUS["bill"]
    return reply_list(msisdn, "Reply 1 for Electricity or 2 for Cable TV.", rows, button_label="Choose")


def _choice_id(text: str, names: dict) -> str | None:
    """Resolve a menu reply to its id: the number itself ("1") or the option's
    name typed out ("MTN", "gotv", "Port Harcourt"). A tapped list row sends the
    id, but over the text fallback users type the name they can see - both must
    work (case/space-insensitive)."""
    t = text.strip()
    if t in names:
        return t
    tl = re.sub(r"\s", "", t.lower())
    for k, v in names.items():
        if tl and re.sub(r"\s", "", v.lower()) == tl:
            return k
    return None


# ---- airtime ----
def _start_airtime(user, msisdn: str) -> None:
    if _blocked_from_spending(user, msisdn):
        return None
    phone = _own_phone(user)
    payload = {"pin_attempts": 0, **({"phone": phone} if phone else {})}
    _new_flow(user, msisdn, "airtime", "network", payload)
    _ask_network(msisdn)


def _advance_airtime(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "network":
        net = _choice_id(text, NETWORK_NAMES)
        if net is None:
            return _ask_network(msisdn)
        pa.payload["net"] = net
        # The number may already be known (the AI path collects it first when the
        # message named a person). Asking for it again reads as not listening.
        if pa.payload.get("phone"):
            _touch(pa, state="amount", payload=pa.payload)
            known = pa.payload.get("amount")
            if known:
                return _advance_airtime(pa, user, msisdn, known)
            return reply(msisdn, "How much airtime? Type the amount (e.g. 200). Minimum ₦50.")
        _touch(pa, state="phone", payload=pa.payload)
        return reply(msisdn, "What phone number? Reply \"me\" to use your own.")
    if st == "phone":
        phone = _phone_from(text, user)
        if not phone:
            return reply(msisdn, "Enter a valid phone number (or \"me\").")
        pa.payload["phone"] = phone
        # A number reached this rung from "recharge tobi 2k" - the amount was in
        # the message and the prefix names the network, so asking for either
        # again is asking twice for something already said.
        pa.payload.setdefault("net", _network_from_prefix(phone) or "")
        _touch(pa, state="amount", payload=pa.payload)
        known = pa.payload.get("amount")
        if known and pa.payload.get("net"):
            return _advance_airtime(pa, user, msisdn, known)
        if not pa.payload.get("net"):
            _touch(pa, state="network", payload=pa.payload)
            return _ask_network(msisdn)
        return reply(msisdn, "How much airtime? Type the amount (e.g. 200). Minimum ₦50.")
    if st == "amount":
        amount = parse_amount(text)
        if amount is None or amount < MIN_AIRTIME:
            return reply(msisdn, f"Enter a valid amount, at least ₦{MIN_AIRTIME:,.0f}.")
        if _insufficient(user, amount):
            return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
        # "bill", not "transfer" - airtime accrues against the bill cap, and
        # checking the wrong bucket both refused purchases the app allows and
        # let through ones it blocks (caught only later, after the OTP).
        limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "bill")
        if limit_msg:
            _clear_actions(msisdn)
            return _limit_reply(msisdn, user, limit_msg)
        net = NETWORK_NAMES[pa.payload["net"]]
        pa.payload["amount"] = str(amount)
        pa.payload["meta"] = {"phone": pa.payload["phone"], "network": pa.payload["net"]}
        if not _arm_confirm(pa, user):
            return
        return _send_confirm(
            pa, msisdn,
            f"📱 *Confirm airtime*\n{_money(amount)} {net} -> {pa.payload['phone']}",
            logo=provider_logo(net))
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        return _exec_airtime(pa, user, msisdn)
    _clear_actions(msisdn)
    return send_menu(msisdn)


def _exec_airtime(pa: PendingAction, user, msisdn: str) -> str:
    amount = Decimal(pa.payload["amount"])
    net = NETWORK_NAMES[pa.payload["net"]]
    phone = pa.payload["phone"]
    return _run_vtu(
        pa, user, msisdn, amount, f"Airtime - {net}",
        lambda ref: vtu_purchase(f"{net.lower()}-airtime",
                                 {"amount": str(amount), "phone": phone}, reference=ref),
        lambda txn, res: ("Airtime receipt", [
            ("Network", net), ("Phone", phone), ("Amount", _money(amount)),
            ("Reference", txn.reference), ("Date", timezone.now().strftime("%d %b %Y, %H:%M"))]),
    )


# ---- data ----
def _start_data(user, msisdn: str, phone=None, network=None) -> None:
    """Start data purchase with every safe detail already available.

    "Data for me" means the linked Zitch line, so asking for that number again is
    redundant. The number's Nigerian prefix can also preselect a network; number
    portability makes that a hint, not an authority, so an explicitly stated
    network always wins and the final confirmation still shows both values.
    Unknown prefixes continue to the network picker.
    """
    if _blocked_from_spending(user, msisdn):
        return None
    own_or_given = _phone_from(str(phone), user) if phone else _own_phone(user)
    payload = {"pin_attempts": 0, **({"phone": own_or_given} if own_or_given else {})}
    pa = _new_flow(user, msisdn, "data", "network", payload)
    net = _network_id(network) or _network_from_prefix(own_or_given)
    if net:
        # Reuse the normal network step so catalogue lookup, plan choices and all
        # later balance/limit/PIN checks stay on the exact same path.
        return _advance_data(pa, user, msisdn, net)
    return _ask_network(msisdn)


def _advance_data(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "network":
        net = _choice_id(text, NETWORK_NAMES)
        if net is None:
            return _ask_network(msisdn)
        plans = list(DataPlan.objects.filter(network=net, active=True)[:8])
        if not plans:
            _clear_actions(msisdn)
            return reply(msisdn, "No data plans available for that network right now.")
        pa.payload["net"] = net
        pa.payload["plan_choices"] = [p.plan_code for p in plans]
        _touch(pa, state="plan", payload=pa.payload)
        lines = "\n".join(f"{i+1}  {p.name} • {p.validity} • {_money(p.price)}" for i, p in enumerate(plans))
        return reply(msisdn, "Choose a plan:\n" + lines)
    if st == "plan":
        plan = _pick(text, pa.payload.get("plan_choices", []), lambda c: DataPlan.objects.filter(plan_code=c).first())
        if plan is None:
            return _reroute_or_reprompt(pa, msisdn, text,
                                       "Reply with a plan number from the list, or \"cancel\".")
        pa.payload.update({"plan_code": plan.plan_code, "price": str(plan.price), "plan_name": plan.name})
        _touch(pa, state="phone", payload=pa.payload)
        if pa.payload.get("phone"):
            return _advance_data(pa, user, msisdn, pa.payload["phone"])
        return reply(msisdn, "What phone number? Reply \"me\" to use your own.")
    if st == "phone":
        phone = _phone_from(text, user)
        if not phone:
            return reply(msisdn, "Enter a valid phone number (or \"me\").")
        price = Decimal(pa.payload["price"])
        if _insufficient(user, price):
            _clear_actions(msisdn)
            return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
        limit_msg = send_limit_error(user, price) or daily_limit_error(user, price, "bill")
        if limit_msg:
            _clear_actions(msisdn)
            return _limit_reply(msisdn, user, limit_msg)
        net = NETWORK_NAMES[pa.payload["net"]]
        pa.payload["phone"] = phone
        pa.payload["meta"] = {"phone": phone, "network": pa.payload["net"], "plan_code": pa.payload["plan_code"]}
        if not _arm_confirm(pa, user):
            return
        return _send_confirm(
            pa, msisdn,
            f"🌐 *Confirm data*\n{pa.payload['plan_name']} ({net}) -> {phone}\n{_money(price)}",
            logo=provider_logo(net))
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        return _exec_data(pa, user, msisdn)
    _clear_actions(msisdn)
    return send_menu(msisdn)


def _exec_data(pa: PendingAction, user, msisdn: str) -> str:
    net = NETWORK_NAMES[pa.payload["net"]]
    phone, plan_code, price = pa.payload["phone"], pa.payload["plan_code"], Decimal(pa.payload["price"])
    return _run_vtu(
        pa, user, msisdn, price, f"Data - {net} {pa.payload['plan_name']}",
        lambda ref: vtu_purchase(f"{net.lower()}-data",
                                 {"billersCode": phone, "variation_code": plan_code, "phone": phone}, reference=ref),
        lambda txn, res: ("Data receipt", [
            ("Network", net), ("Plan", pa.payload["plan_name"]), ("Phone", phone),
            ("Amount", _money(price)), ("Reference", txn.reference),
            ("Date", timezone.now().strftime("%d %b %Y, %H:%M"))]),
    )


# ---- electricity ----
def _start_electricity(user, msisdn: str) -> None:
    if _blocked_from_spending(user, msisdn):
        return None
    pa = _new_flow(user, msisdn, "electricity", "disco")
    _electricity_next(pa, user, msisdn)


def _begin_electricity(user, msisdn: str, biller, customer_id, variation, amount) -> None:
    """Start an electricity payment from details the customer already gave.

    "Load my nepa bill. 2000010657 5,000 to IKEDC" names the disco, the meter and
    the amount in one line. Discarding all three and opening with "Which disco?"
    is the channel telling someone it did not read their message - so whatever
    arrives is put in the flow's payload up front and the flow asks only for what
    is genuinely still missing.

    Nothing is trusted for being pre-filled: the meter is verified with the
    provider exactly as a typed one is, the amount goes through the same minimum,
    balance and limit checks, and the payment still ends at the same confirm +
    PIN. Pre-filling changes which QUESTIONS get asked, never which CHECKS run.
    """
    if _blocked_from_spending(user, msisdn):
        return None
    payload = {"pin_attempts": 0}
    disco = _disco_id(biller)
    if disco:
        payload["disco"] = disco
    mt = _meter_type(variation)
    if mt:
        payload["meter_type"] = mt
    meter = re.sub(r"\D", "", str(customer_id or ""))
    if len(meter) >= 6:
        payload["meter"] = meter
    amt = parse_amount(str(amount)) if amount is not None else None
    if amt is not None and amt >= MIN_ELECTRICITY:
        payload["amount"] = str(amt)
    pa = _new_flow(user, msisdn, "electricity", "disco", payload)
    _electricity_next(pa, user, msisdn)


def _electricity_next(pa: PendingAction, user, msisdn: str) -> None:
    """Ask for the first detail still missing - or, when nothing is, confirm.

    The order of the questions is unchanged; what changed is that each one is now
    conditional on not already knowing the answer. A customer who types every
    answer walks the same path they always did.
    """
    p = pa.payload
    if not p.get("disco"):
        _touch(pa, state="disco", payload=p)
        return reply(msisdn, DISCO_PROMPT)
    if not p.get("meter_type"):
        _touch(pa, state="meter_type", payload=p)
        return reply(msisdn, "Prepaid or postpaid? Reply 1 Prepaid or 2 Postpaid.")
    if not p.get("meter"):
        _touch(pa, state="meter", payload=p)
        return reply(msisdn, "Enter the meter number.")

    note = ""
    if not p.get("meter_verified"):
        disco = DISCO_NAMES[p["disco"]].lower()
        res = vtu_verify_customer(f"{disco}-electric", p["meter"], p["meter_type"])
        if not res.get("success"):
            # Drop the number that failed. Keeping it would re-verify the same bad
            # meter on the next message and never let the customer past it.
            p.pop("meter", None)
            _touch(pa, state="meter", payload=p)
            # Plain re-prompt: this branch runs from _electricity_next, which has no
            # customer message in scope - the meter came from an earlier turn, so
            # there is nothing here that could be a new instruction.
            return reply(msisdn, "Couldn't validate that meter. Check the number and try "
                                 "again, or \"cancel\".")
        cust = res.get("customer_name", "")
        address = res.get("customer_address", "")
        p.update({"customer": cust, "customer_address": address,
                  "meter_verified": True})
        note = f"Meter verified{f' ({cust})' if cust else ''}. "

    if not p.get("amount"):
        _touch(pa, state="amount", payload=p)
        return reply(msisdn, note + "How much do you want to buy? (e.g. 5000)")
    return _electricity_confirm(pa, user, msisdn, note)


def _electricity_confirm(pa: PendingAction, user, msisdn: str, note: str = "") -> None:
    """The amount is known: run every money check, then arm the confirm."""
    p = pa.payload
    amount = parse_amount(p.get("amount", ""))
    if amount is None or amount < MIN_ELECTRICITY:
        p.pop("amount", None)
        _touch(pa, state="amount", payload=p)
        return reply(msisdn, f"Enter a valid amount, at least ₦{MIN_ELECTRICITY:,.0f}.")
    if _insufficient(user, amount):
        # Asked-for amounts stay recoverable: drop it and let them name another
        # rather than end the flow they are halfway through.
        p.pop("amount", None)
        _touch(pa, state="amount", payload=p)
        return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
    limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "bill")
    if limit_msg:
        _clear_actions(msisdn)
        return _limit_reply(msisdn, user, limit_msg)
    disco_name = DISCO_NAMES[p["disco"]]
    p["amount"] = str(amount)
    p["meta"] = {"meter": p["meter"], "disco": p["disco"], "meter_type": p["meter_type"],
                 "customer_name": p.get("customer", ""),
                 "customer": p.get("customer", ""),
                 "customer_address": p.get("customer_address", ""),
                 "address": p.get("customer_address", "")}
    if not _arm_confirm(pa, user):
        return
    cust = p.get("customer") or "-"
    address = p.get("customer_address") or "Not provided by electricity provider"
    return _send_confirm(
        pa, msisdn,
        note +
        f"💡 *Confirm electricity*\n{disco_name} ({p['meter_type']}) • "
        f"Meter {p['meter']}\nCustomer: {cust}\nAddress: {address}\n{_money(amount)}")


def _advance_electricity(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "disco":
        # Named, not just numbered: "IKEDC" at this prompt now works too.
        d = _disco_id(text)
        if d is None:
            return reply(msisdn, "Reply with the disco number.\n" + DISCO_PROMPT)
        pa.payload["disco"] = d
        return _electricity_next(pa, user, msisdn)
    if st == "meter_type":
        mt = _meter_type(text)
        if not mt:
            return reply(msisdn, "Reply 1 Prepaid or 2 Postpaid.")
        pa.payload["meter_type"] = mt
        return _electricity_next(pa, user, msisdn)
    if st == "meter":
        meter = re.sub(r"\s", "", text)
        if len(meter) < 6:
            return reply(msisdn, "Enter a valid meter number.")
        pa.payload.update({"meter": meter, "meter_verified": False})
        return _electricity_next(pa, user, msisdn)
    if st == "amount":
        amount = parse_amount(text)
        if amount is None or amount < MIN_ELECTRICITY:
            return reply(msisdn, f"Enter a valid amount, at least ₦{MIN_ELECTRICITY:,.0f}.")
        pa.payload["amount"] = str(amount)
        return _electricity_confirm(pa, user, msisdn)
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        return _exec_electricity(pa, user, msisdn)
    _clear_actions(msisdn)
    return send_menu(msisdn)


def _exec_electricity(pa: PendingAction, user, msisdn: str) -> str:
    amount = Decimal(pa.payload["amount"])
    disco = DISCO_NAMES[pa.payload["disco"]].lower()
    disco_name = DISCO_NAMES[pa.payload["disco"]]
    meter, mt = pa.payload["meter"], pa.payload["meter_type"]

    def _receipt_rows(txn, res):
        token = res.get("token") or res.get("provider_reference", "")
        rows = [("Disco", disco_name), ("Meter", f"{meter} ({mt})"),
                ("Customer", pa.payload.get("customer") or "Verified customer"),
                ("Address", pa.payload.get("customer_address")
                            or "Not provided by electricity provider"),
                ("Amount", _money(amount))]
        if token:
            rows.append(("Token", token))
        rows += [("Reference", txn.reference), ("Date", timezone.now().strftime("%d %b %Y, %H:%M"))]
        return ("Electricity receipt", rows)

    return _run_vtu(
        pa, user, msisdn, amount, f"Electricity - {disco_name}",
        lambda ref: vtu_purchase(f"{disco}-electric",
                                 {"billersCode": meter, "variation_code": mt, "amount": str(amount)}, reference=ref),
        _receipt_rows,
    )


# ---- cable ----
def _start_cable(user, msisdn: str) -> None:
    if _blocked_from_spending(user, msisdn):
        return None
    _new_flow(user, msisdn, "cable", "provider")
    reply(msisdn, CABLE_PROMPT)


def _begin_cable(user, msisdn: str, biller, customer_id) -> None:
    """Cable's half of the same courtesy: a named provider skips the provider
    list, and a smartcard number given up front is not asked for again. The
    package still has to be chosen - it carries the price - and the card is still
    verified with the provider before anything is confirmed."""
    if _blocked_from_spending(user, msisdn):
        return None
    payload = {"pin_attempts": 0}
    card = re.sub(r"\D", "", str(customer_id or ""))
    if len(card) >= 6:
        payload["iuc_given"] = card
    prov = _cable_id(biller)
    pa = _new_flow(user, msisdn, "cable", "provider", payload)
    if not prov:
        return reply(msisdn, CABLE_PROMPT)
    return _cable_packages(pa, user, msisdn, prov)


def _cable_packages(pa: PendingAction, user, msisdn: str, prov: str) -> None:
    """Show the chosen provider's packages (the step every cable payment reaches,
    whether the provider was tapped from the list or named in the message)."""
    plans = list(CablePlan.objects.filter(provider=prov, active=True)[:8])
    if not plans:
        _clear_actions(msisdn)
        return reply(msisdn, "No packages available for that provider right now.")
    pa.payload["prov"] = prov
    pa.payload["plan_choices"] = [pl.cable_plan_code for pl in plans]
    _touch(pa, state="plan", payload=pa.payload)
    lines = "\n".join(f"{i+1}  {pl.name} • {_money(pl.price)}" for i, pl in enumerate(plans))
    return reply(msisdn, "Choose a package:\n" + lines)


def _advance_cable(pa: PendingAction, user, msisdn: str, text: str) -> None:
    st = pa.state
    if st == "provider":
        p = _cable_id(text)
        if p is None:
            return reply(msisdn, "Reply with the provider number.\n" + CABLE_PROMPT)
        return _cable_packages(pa, user, msisdn, p)
    if st == "plan":
        plan = _pick(text, pa.payload.get("plan_choices", []),
                     lambda c: CablePlan.objects.filter(cable_plan_code=c).first())
        if plan is None:
            return _reroute_or_reprompt(pa, msisdn, text,
                                       "Reply with a package number from the list, or \"cancel\".")
        pa.payload.update({"plan_code": plan.cable_plan_code, "price": str(plan.price), "plan_name": plan.name})
        _touch(pa, state="iuc", payload=pa.payload)
        given = pa.payload.get("iuc_given")
        if given:
            # Already told us the card - verify it now instead of asking again.
            return _advance_cable(pa, user, msisdn, given)
        return reply(msisdn, "Enter your smartcard / IUC number.")
    if st == "iuc":
        iuc = re.sub(r"\s", "", text)
        if len(iuc) < 6:
            return reply(msisdn, "Enter a valid smartcard / IUC number.")
        # A card that fails verification must not be re-tried from the payload on
        # the customer's next message.
        pa.payload.pop("iuc_given", None)
        prov = CABLE_NAMES[pa.payload["prov"]].lower()
        res = vtu_verify_customer(prov, iuc)
        if not res.get("success"):
            return _reroute_or_reprompt(pa, msisdn, text,
                                       "Couldn't validate that smartcard. Check the number and try again, or \"cancel\".")
        price = Decimal(pa.payload["price"])
        if _insufficient(user, price):
            _clear_actions(msisdn)
            return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
        limit_msg = send_limit_error(user, price) or daily_limit_error(user, price, "bill")
        if limit_msg:
            _clear_actions(msisdn)
            return _limit_reply(msisdn, user, limit_msg)
        prov_name = CABLE_NAMES[pa.payload["prov"]]
        cust = res.get("customer_name", "")
        pa.payload.update({"iuc": iuc, "customer": cust})
        pa.payload["meta"] = {"iuc": iuc, "provider": pa.payload["prov"], "plan_code": pa.payload["plan_code"]}
        if not _arm_confirm(pa, user):
            return
        cust = cust or "-"
        return _send_confirm(
            pa, msisdn,
            f"📺 *Confirm cable*\n{prov_name} • {pa.payload['plan_name']}\n"
            f"Card {iuc} • {cust} • {_money(price)}",
            logo=provider_logo(prov_name))
    if st == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return
        return _exec_cable(pa, user, msisdn)
    _clear_actions(msisdn)
    return send_menu(msisdn)


def _exec_cable(pa: PendingAction, user, msisdn: str) -> str:
    prov = CABLE_NAMES[pa.payload["prov"]].lower()
    prov_name = CABLE_NAMES[pa.payload["prov"]]
    iuc, plan_code, price = pa.payload["iuc"], pa.payload["plan_code"], Decimal(pa.payload["price"])
    return _run_vtu(
        pa, user, msisdn, price, f"Cable - {prov_name} {pa.payload['plan_name']}",
        lambda ref: vtu_purchase(prov, {"billersCode": iuc, "variation_code": plan_code}, reference=ref),
        lambda txn, res: ("Cable receipt", [
            ("Provider", prov_name), ("Package", pa.payload["plan_name"]), ("Smartcard", iuc),
            ("Amount", _money(price)), ("Reference", txn.reference),
            ("Date", timezone.now().strftime("%d %b %Y, %H:%M"))]),
    )


# ---- exam PINs ----
def _start_exam(user, msisdn: str) -> None:
    if _blocked_from_spending(user, msisdn):
        return None
    products = list(ExamProduct.objects.filter(active=True).order_by("name")[:8])
    if not products:
        return reply(msisdn, "Exam PINs are unavailable right now. Please try again later.")
    pa = _new_flow(user, msisdn, "exam", "product", {
        "pin_attempts": 0,
        "product_choices": [product.code for product in products],
    })
    lines = "\n".join(
        f"{index + 1}  {product.name} · {product.description} · {_money(product.price)}"
        for index, product in enumerate(products)
    )
    _touch(pa, state="product", payload=pa.payload)
    return reply(msisdn, "🎓 *Buy an Exam PIN*\nChoose a product:\n" + lines)


def _exam_product(text: str, choices: list[str]):
    product = _pick(text, choices, lambda code: ExamProduct.objects.filter(
        code=code, active=True).first())
    if product is not None:
        return product
    wanted = re.sub(r"\s", "", text.lower())
    if not wanted:
        return None
    return next((item for item in ExamProduct.objects.filter(code__in=choices, active=True)
                 if wanted in (re.sub(r"\s", "", item.code.lower()),
                               re.sub(r"\s", "", item.name.lower()))), None)


def _advance_exam(pa: PendingAction, user, msisdn: str, text: str) -> None:
    state = pa.state
    if state == "product":
        product = _exam_product(text, pa.payload.get("product_choices", []))
        if product is None:
            return _reroute_or_reprompt(pa, msisdn, text,
                                       "Reply with an exam product number from the list, or \"cancel\".")
        pa.payload.update({
            "exam_code": product.code,
            "exam_name": product.name,
            "description": product.description,
            "unit_price": str(product.price),
            "service_id": product.service_id,
        })
        _touch(pa, state="quantity", payload=pa.payload)
        return reply(msisdn, "How many PINs? Reply with a number from 1 to 10.")
    if state == "quantity":
        try:
            quantity = int(text.strip())
        except (TypeError, ValueError):
            quantity = 0
        if not 1 <= quantity <= 10:
            return reply(msisdn, "Enter a quantity from 1 to 10.")
        pa.payload["quantity"] = quantity
        own = _own_phone(user)
        if own:
            pa.payload["phone"] = own
        _touch(pa, state="phone", payload=pa.payload)
        return reply(msisdn, "What phone number should receive the PIN details? Reply \"me\" to use your own.")
    if state == "phone":
        phone = _phone_from(text, user)
        if not phone:
            return reply(msisdn, "Enter a valid phone number, or reply \"me\".")
        quantity = int(pa.payload["quantity"])
        amount = Decimal(pa.payload["unit_price"]) * quantity
        if _insufficient(user, amount):
            return reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
        limit_msg = send_limit_error(user, amount) or daily_limit_error(user, amount, "bill")
        if limit_msg:
            _clear_actions(msisdn)
            return _limit_reply(msisdn, user, limit_msg)
        pa.payload.update({
            "phone": phone,
            "amount": str(amount),
            "meta": {"exam": pa.payload["exam_code"], "phone": phone,
                     "quantity": quantity},
        })
        if not _arm_confirm(pa, user):
            return None
        return _send_confirm(
            pa, msisdn,
            f"🎓 *Confirm Exam PIN*\n{pa.payload['exam_name']} · "
            f"{pa.payload['description']} ×{quantity}\nTo {phone}\n{_money(amount)}")
    if state == "pin":
        if not _flow_pin_ok(pa, user, msisdn, text):
            return None
        return _exec_exam(pa, user, msisdn)
    _clear_actions(msisdn)
    return send_menu(msisdn)


def _exec_exam(pa: PendingAction, user, msisdn: str) -> str:
    amount = Decimal(pa.payload["amount"])
    quantity = int(pa.payload["quantity"])
    phone = pa.payload["phone"]
    name = pa.payload["exam_name"]
    service_id = pa.payload.get("service_id") or f"{pa.payload['exam_code']}-pin"

    def receipt(txn, result):
        pins = result.get("pins") or result.get("Pin") or []
        if isinstance(pins, (str, int)):
            pins = [pins]
        pin_text = ", ".join(str(value) for value in pins) or "Delivered"
        return ("Exam PIN receipt", [
            ("Exam", name), ("Product", pa.payload["description"]),
            ("Quantity", str(quantity)), ("Phone", phone),
            ("PIN", pin_text), ("Amount", _money(amount)),
            ("Reference", txn.reference),
            ("Date", timezone.now().strftime("%d %b %Y, %H:%M")),
        ])

    return _run_vtu(
        pa, user, msisdn, amount, f"Exam PIN - {name} x{quantity}",
        lambda ref: vtu_purchase(service_id, {
            "billersCode": phone, "quantity": quantity, "phone": phone,
        }, reference=ref),
        receipt,
    )


def _pick(text: str, choices: list, fetch):
    """Map a '1'-based reply to an item via `fetch(code)`; None if out of range."""
    try:
        idx = int(text.strip()) - 1
    except ValueError:
        return None
    if not (0 <= idx < len(choices)):
        return None
    return fetch(choices[idx])


# --------------------------------------------------------------------------- #
# AI intent layer - the LLM proposes; these map its intent to the SAME flows
# --------------------------------------------------------------------------- #
NET_BY_NAME = {v.lower(): k for k, v in NETWORK_NAMES.items()}  # "mtn" -> "1"


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


def _network_id(network) -> str | None:
    return NET_BY_NAME.get(str(network).strip().lower()) if network else None


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
    if name == "buy_airtime":
        return _begin_airtime(user, msisdn, p.get("amount"), p.get("phone"),
                              p.get("network"), p.get("recipient_ref"))
    if name == "buy_data":
        # Keep the target/network the customer stated. When they said "me", the
        # model intentionally leaves phone null and _start_data uses the linked
        # Zitch line, then infers its network when the prefix is recognised.
        _start_data(user, msisdn, p.get("phone"), p.get("network"))
        return True
    if name == "pay_bill":
        cat = (p.get("category") or "").lower()
        # The model extracts biller/customer_id/variation/amount; this branch used
        # to throw all four away and open the flow at question one, which is how a
        # message naming the disco, the meter AND the amount still got "Which
        # disco?". Pass them on - the flows validate everything they are given.
        if "electric" in cat:
            _begin_electricity(user, msisdn, p.get("biller"), p.get("customer_id"),
                               p.get("variation"), p.get("amount"))
        elif "cable" in cat or "tv" in cat:
            _begin_cable(user, msisdn, p.get("biller"), p.get("customer_id"))
        else:
            _start_service_menu(user, msisdn, "bill")
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


def _begin_airtime(user, msisdn: str, amount, phone, network, recipient_ref=None) -> bool:
    """LLM airtime: if amount + network + phone are all known, jump to confirm;
    otherwise start the guided flow."""
    if _blocked_from_spending(user, msisdn):
        return True
    # "recharge tobi 2k" names WHO, not what number. Falling through to the
    # default below would have read the missing number as "me" and topped up the
    # sender's own line - the wrong number, already paid for, and nothing on the
    # confirm card to reveal it was wrong because the card shows the number it
    # guessed. Zitch cannot read the phone's contacts, so the only honest move is
    # to ask.
    if recipient_ref and not phone:
        who = str(recipient_ref)[:40]
        payload = {"pin_attempts": 0, "recipient_ref": who}
        try:
            if amount is not None:
                payload["amount"] = str(Decimal(str(amount)))
        except (InvalidOperation, TypeError):
            pass
        netid = _network_id(network)
        if netid:
            payload["net"] = netid
        _new_flow(user, msisdn, "airtime", "phone", payload)
        reply(msisdn, f"I can't look up {who}'s number - Zitch can't read your contacts. "
                      "What number should I recharge?")
        return True
    # "2k airtime for me" carries neither a number nor a network. Falling back to
    # the guided flow for that made the AI look useless on the single most common
    # sentence customers actually send - so both are inferred rather than asked
    # for: no target means the customer's own line, and a Nigerian number's
    # prefix names its network. Neither guess moves money; the confirm screen
    # still shows what was inferred and still needs biometrics or the PIN.
    ph = _phone_from(str(phone), user) if phone else _own_phone(user)
    netid = _network_id(network) or _network_from_prefix(ph)
    try:
        amt = Decimal(str(amount)) if amount is not None else None
    except (InvalidOperation, TypeError):
        amt = None
    if amt and amt >= 50 and netid and ph:
        if _insufficient(user, amt):
            reply(msisdn, f"Insufficient balance ({_money(get_or_create_wallet(user).balance)}).")
            return True
        net = NETWORK_NAMES[netid]
        pa = _new_flow(user, msisdn, "airtime", "pin",
                       {"pin_attempts": 0, "net": netid, "phone": ph, "amount": str(amt.quantize(Decimal("0.01"))),
                        "meta": {"phone": ph, "network": netid}})
        if not _arm_confirm(pa, user):   # failure is already explained in-chat
            return True
        _send_confirm(pa, msisdn, f"Confirm airtime\n{_money(amt)} {net} -> {ph}")
        return True
    _start_airtime(user, msisdn)
    return True


# --------------------------------------------------------------------------- #
# currency conversion (FX) - quote -> PIN-within-TTL -> settle (Fincra rail)
# --------------------------------------------------------------------------- #
CONVERT_CCYS = ["NGN", "USD", "GBP", "CAD"]  # settle-able; CNY is quote-only (blocked)


def _start_convert(user, msisdn: str) -> None:
    if _blocked_from_spending(user, msisdn):
        return None
    _new_flow(user, msisdn, "convert", "from")
    reply(msisdn, "Convert currency.\nWhich currency are you selling? (NGN, USD, GBP, CAD)")


def _advance_convert(pa: PendingAction, user, msisdn: str, text: str) -> None:
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

    # Held for the worker: `executing` says authorised-not-yet-done, and the
    # deadline is pushed out because the short PIN window governs how long the
    # customer has to CONFIRM, not how long the rail has to settle.
    pa.state = EXECUTING_STATE
    pa.expires_at = timezone.now() + EXECUTION_TTL
    pa.save(update_fields=["state", "expires_at"])
    enqueue_flow_execution(pa)
    # Same safety net the webhook uses for a worker that isn't running, and off
    # the request thread so Meta's answer is not held up by it. Not under the
    # test runner, where the suite drives the queue itself and a second thread
    # would only race it.
    if not getattr(settings, "TESTING", False):
        drain_in_background()
    # Unlock is an authentication result, not a money movement. There is no
    # transaction row for _await_settlement to find, so sending it through the
    # payment-status fallback mislabeled every successful unlock as "Pending".
    # The requested account/details command still runs on the durable worker;
    # report only what is already true here: identity was confirmed.
    if pa.action_type == "unlock":
        return Outcome("Identity confirmed - your requested details will appear "
                       "in the chat.", "done")

    # Give the rail a moment to answer before closing the Flow. Without this,
    # "Successful" was unreachable in production: every money Flow closed on
    # "Pending" because the endpoint replied the instant the job was queued, so
    # the customer's last word from the Flow was always about a payment that had
    # not been attempted yet.
    settled = _await_settlement(pa.id, user, pa.action_type)
    if settled is not None:
        return settled
    # Still working. PENDING, emphatically not success: the rail has not answered
    # yet, and a tick here would read as "done" for a payment that may still
    # fail - the one thing a banking channel must never say. The receipt in the
    # chat remains the authoritative outcome.
    return Outcome("Confirmed - I'm completing your payment now. The receipt will "
                   "arrive in this chat in a few seconds.", OUTCOME_PENDING)


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
    "exam": "Exam PIN purchased",
    "convert": "Converted",
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

    budget = float(getattr(settings, "WHATSAPP_FLOW_SETTLE_WAIT", 3) or 3)
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
               .only("transaction_status", "reference").first())
        if txn is not None and txn.transaction_status != Transaction.PENDING:
            if txn.transaction_status == Transaction.SUCCESS:
                verb = _SETTLED_VERB.get(action_type, "Done")
                return Outcome(f"{verb} - the receipt is in your chat.", OUTCOME_SUCCESS)
            # A failure is worth waiting for too: it is the one outcome the
            # customer should see BEFORE the screen closes, not only in a chat
            # message they may scroll past.
            return Outcome("That didn't go through. You were not charged - "
                           "see the chat for details.", OUTCOME_FAILED)
        if time.monotonic() >= deadline:
            return None
        time.sleep(_SETTLE_POLL)


def run_flow_execution(pa: PendingAction, user) -> str:
    # Token resolution and PIN verification happen before this call. Re-read and
    # lock both records briefly so a concurrent cancel, replay, expiry or account
    # freeze cannot race past the final execution boundary.
    #
    # Do NOT wrap the provider call below in this transaction. Wema's debit-wallet
    # rail calls our Authentication Callback while ProcessClientTransfer is still
    # in flight. If the PENDING ledger row is created inside an outer transaction
    # that has not committed yet, the callback cannot see it and correctly denies
    # the payout as "unknown_reference", which Wema surfaces as "Authentication
    # Failed". Keep only the claim/eligibility check atomic; the executor's own
    # debit() transaction then commits the row before the Wema network call.
    with db_transaction.atomic():
        live = PendingAction.objects.select_for_update().filter(
            pk=pa.pk,
            user_id=user.pk,
            msisdn=pa.msisdn,
            state__in=(FLOW_PIN_STATE, "pin", EXECUTING_STATE),
        ).first()
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
            _clear_actions(live.msisdn)
            return Outcome("Your Zitch account is currently suspended. Please contact support.",
                           OUTCOME_FAILED)

        pa = live
        user = live_user
    # Getting here means the PIN or a verified biometric just passed, so it
    # starts the re-auth window: someone who just authorised a payment should
    # not be challenged again to read their own balance.
    _mark_verified(pa.msisdn)
    executors = {
        "transfer": _exec_transfer, "airtime": _exec_airtime, "data": _exec_data,
        "electricity": _exec_electricity, "cable": _exec_cable, "convert": _exec_convert,
        "exam": _exec_exam,
        "unlock": _exec_unlock,
    }
    fn = executors.get(pa.action_type)
    if fn is None:
        _clear_actions(pa.msisdn)
        return Outcome("Sorry, this action can't be completed here. Please try again in the chat.",
                       OUTCOME_FAILED)
    outcome = fn(pa, user, pa.msisdn) or "Done ✅"
    # Remember how this ended, keyed on the action id. The confirm card that armed
    # it is still sitting in the thread with a live "Use PIN instead" button -
    # WhatsApp cannot take that back - so tapping it afterwards has to be able to
    # say "already paid" instead of "expired". Best-effort: a cache miss costs
    # wording on a stale button, never the payment.
    if getattr(outcome, "status", "") == OUTCOME_SUCCESS:
        from .flows import remember_settled

        remember_settled(pa, str(outcome))
    return outcome
