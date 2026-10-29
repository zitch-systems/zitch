"""9PSB catalog checkout using WhatsApp's existing scoped PIN authorization.

Only product choices and recipient details travel through chat. The existing
encrypted Flow / one-time SMS challenge owns payment authorization. App and chat
share the same price validation, reservation and durable provider instruction.
"""
from decimal import Decimal

from wallet.models import Transaction
from wallet.services import DuplicateTransaction, InsufficientFunds, LimitExceeded

from . import bills
from .client import ProviderError, config, money, phone
from .payments import spending_account
from .vas import fields_schema, text_value

ACTION = "bank_vas"
PAGE_SIZE = 8


def router():
    from whatsapp import router as implementation
    return implementation


def _choices(pa, state, rows):
    r = router()
    if not rows:
        r._clear_actions(pa.msisdn)
        return r.reply(pa.msisdn, "The bank has no available options for this selection. Please try again later.")
    pa.payload.update(choices=rows, page=0)
    r._touch(pa, state=state, payload=pa.payload)
    return _show_choices(pa)


def _show_choices(pa):
    rows = pa.payload["choices"]
    offset = pa.payload.get("page", 0) * PAGE_SIZE
    page = rows[offset:offset+PAGE_SIZE]
    lines = [f"{i+1}. {row['label']}" for i, row in enumerate(page)]
    if offset + PAGE_SIZE < len(rows):
        lines.append('Reply "next" for more options.')
    if offset:
        lines.append('Reply "back" for the previous page.')
    return router().reply(pa.msisdn, "Choose an option:\n" + "\n".join(lines) + '\nOr reply "cancel".')


def _pick(pa, text):
    r = router()
    value = str(text).strip().casefold()
    rows = pa.payload.get("choices", [])
    page = pa.payload.get("page", 0)
    if value == "next" and (page+1)*PAGE_SIZE < len(rows):
        pa.payload["page"] = page+1
    elif value == "back" and page > 0:
        pa.payload["page"] = page-1
    else:
        current = rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]
        if value.isascii() and value.isdigit() and 1 <= int(value) <= len(current):
            return current[int(value)-1]
        matches = [row for row in current if row["label"].casefold() == value]
        if len(matches) == 1:
            return matches[0]
    r._touch(pa, payload=pa.payload)
    _show_choices(pa)
    return None


def _named(rows):
    return [{"id": str(row["id"]), "label": str(row["name"])[:160]}
            for row in rows if isinstance(row, dict) and row.get("id") is not None and row.get("name")]


def start(user, msisdn, kind="bill", *, mobile=None, amount=None):
    r = router()
    try:
        if config().get("VAS_ENABLED") is not True:
            raise ValueError("Bill payments are temporarily unavailable")
        spending_account(user, refresh=True)
        if kind not in {"airtime", "data", "bill"}:
            raise ValueError("Choose airtime, data or bill payments")
        payload = {"kind": kind, "pin_attempts": 0}
        if kind == "airtime" and amount is not None:
            payload["requested_amount"] = money(amount)
        pa = r._new_flow(user, msisdn, ACTION, "phone", payload)
        if kind == "bill":
            return _choices(pa, "categories", _named(bills.catalog("categories")))
        if mobile:
            return advance(pa, user, msisdn, mobile)
        return r.reply(msisdn, 'What phone number should receive it? Reply "me" for your verified number.')
    except (ValueError, LimitExceeded, ProviderError):
        return r.reply(msisdn, "Bill payments are unavailable for this account right now. Please try again later.")


def _confirm(pa, user):
    r = router()
    if pa.payload["kind"] == "bill":
        result = bills.validate(user, pa.payload["biller_id"], pa.payload["item_id"],
                                pa.payload["customer"], pa.payload["amount"],
                                inputs=pa.payload.get("validation_inputs", {}))
        pa.payload.update(validation_reference=result["validation_reference"], customer_name=result["customer_name"])
    else:
        network = bills.catalog("network", phone=pa.payload["phone"]).get("network")
        if not isinstance(network, str) or not network:
            raise ValueError("The phone network could not be confirmed")
        pa.payload["network"] = network
    if r._arm_confirm(pa, user):
        details = fields(pa)
        return r._send_confirm(pa, pa.msisdn, "Confirm payment\n" + "\n".join(details.values()))


def fields(pa):
    p = pa.payload
    kind = p["kind"]
    return {"amount": router()._money(Decimal(p["amount"])),
        "recipient": p.get("customer_name") or p.get("phone", ""),
        "details": p.get("item_name") or p.get("biller_name") or p.get("plan_name") or (p.get("network", "") + " " + kind).strip()}


def _customer_prompt(pa):
    router()._touch(pa, state="customer", payload=pa.payload)
    field = pa.payload["biller_fields"]["customerId"]
    return router().reply(pa.msisdn, field["fieldDescription"] or "Enter your customer, meter or subscription number.")


def _next_customer_input(pa):
    r = router()
    for key in ("firstname", "lastname"):
        field = pa.payload["biller_fields"].get(key)
        if field and field["validation"] == "Y" and not pa.payload["validation_inputs"].get(key):
            r._touch(pa, state="input_" + key, payload=pa.payload)
            return r.reply(pa.msisdn, field["fieldDescription"] or ("Enter your " + key))
    r._touch(pa, state="phone", payload=pa.payload)
    return r.reply(pa.msisdn, 'Enter your contact phone number, or reply "me".')


def advance(pa, user, msisdn, text):
    r = router()
    if pa.state == "pin":
        if r._flow_pin_ok(pa, user, msisdn, text):
            return execute(pa, user, msisdn)
        return
    try:
        kind = pa.payload["kind"]
        if pa.state == "categories":
            selected = _pick(pa, text)
            if selected:
                return _choices(pa, "billers", _named(bills.catalog("billers", category=selected["id"])))
        elif pa.state == "billers":
            selected = _pick(pa, text)
            if selected:
                schema = fields_schema(bills.catalog("fields", biller=selected["id"]))
                pa.payload.update(biller_id=selected["id"], biller_name=selected["label"],
                    biller_fields=schema, validation_inputs={}, item_id="", amount="0")
                if "itemId" not in schema:
                    return _customer_prompt(pa)
                rows = schema["itemId"]["items"]
                choices = [{"id": str(row["itemId"]), "label": str(row["itemName"])[:160], "amount": str(row.get("amount", "0"))}
                           for row in rows if row.get("itemId") is not None and row.get("itemName")]
                return _choices(pa, "items", choices)
        elif pa.state == "items":
            selected = _pick(pa, text)
            if selected:
                amount = Decimal(selected["amount"])
                if not amount.is_finite() or amount < 0:
                    raise ValueError("This bill item has an invalid price")
                pa.payload.update(item_id=selected["id"], item_name=selected["label"], amount=str(amount))
                return _customer_prompt(pa)
        elif pa.state == "customer":
            customer = text_value(text, maximum=100)
            if not customer:
                raise ValueError("Enter a valid customer number")
            pa.payload["customer"] = customer
            return _next_customer_input(pa)
        elif pa.state in {"input_firstname", "input_lastname"}:
            value = text_value(text, maximum=100)
            if not value:
                raise ValueError("Enter the required customer name")
            pa.payload["validation_inputs"][pa.state.removeprefix("input_")] = value
            return _next_customer_input(pa)
        elif pa.state == "phone":
            mobile = phone(user.phone if str(text).strip().casefold() == "me" else text)
            pa.payload["phone"] = mobile
            if kind == "data":
                choices = []
                for row in bills.catalog("data", phone=mobile):
                    if isinstance(row, dict) and row.get("productId") is not None:
                        value = money(row.get("amount"))
                        choices.append({"id": str(row["productId"]), "amount": value,
                            "label": f"{row.get('dataBundle', 'Data')} {row.get('validity', '')} - {r._money(Decimal(value))}"[:160]})
                return _choices(pa, "plans", choices)
            if kind == "bill" and Decimal(pa.payload["amount"]) > 0:
                return _confirm(pa, user)
            r._touch(pa, state="amount", payload=pa.payload)
            if pa.payload.get("requested_amount"):
                return advance(pa, user, msisdn, pa.payload.pop("requested_amount"))
            return r.reply(msisdn, "How much would you like to pay (NGN)?")
        elif pa.state == "plans":
            selected = _pick(pa, text)
            if selected:
                pa.payload.update(product_id=selected["id"], plan_name=selected["label"], amount=selected["amount"])
                return _confirm(pa, user)
        elif pa.state == "amount":
            pa.payload["amount"] = money(text)
            return _confirm(pa, user)
        else:
            return r.reply(msisdn, "Use the secure confirmation card, or cancel and start again.")
    except (ValueError, ArithmeticError, KeyError, ProviderError):
        return r.reply(msisdn, "We could not validate that selection. Check the details or cancel and try again.")


def execute(pa, user, msisdn):
    r = router()
    p, key = pa.payload, f"wa-{pa.pk}"
    amount = Decimal(p["amount"])
    prior = Transaction.objects.filter(user=user, idempotency_key=key).first()
    label = {"airtime": "Airtime", "data": "Data", "bill": "Bill payment"}[p["kind"]]
    def receipt(txn, result):
        return label + " receipt", [("Amount", r._money(amount)),
            ("Recipient", p.get("customer_name") or p["phone"]),
            ("Details", p.get("item_name") or p.get("biller_name") or p.get("plan_name") or p.get("network", "")),
            *([("Token", result["token"])] if result.get("token") else []),
            *([("Units", result["electricity_units"])] if result.get("electricity_units") else []),
            *([("Token", "Being retrieved. Check transaction history shortly.")] if result.get("receipt_pending") else []),
            ("Reference", txn.reference)]
    if prior:
        return r._vtu_outcome(pa, user, msisdn, amount, label, prior, prior.meta, receipt, replay=True)
    try:
        if p["kind"] == "bill":
            txn = bills.purchase_bill(user, p["validation_reference"], p["phone"], key, channel="whatsapp")
        else:
            txn = bills.purchase_topup(user, p["kind"], p["phone"], amount, product_id=p.get("product_id", ""),
                                       idempotency_key=key, channel="whatsapp")
    except DuplicateTransaction:
        prior = Transaction.objects.filter(user=user, idempotency_key=key).first()
        if prior:
            return r._vtu_outcome(pa, user, msisdn, amount, label, prior, prior.meta, receipt, replay=True)
        return r.Outcome("This payment is already processing. Check history before paying again.", r.OUTCOME_PENDING)
    except (ProviderError, ValueError, KeyError, LimitExceeded, InsufficientFunds, bills.BillerValidation.DoesNotExist):
        r._clear_actions(msisdn)
        message = "This payment could not be submitted. Refresh the bill details and try again."
        r.reply(msisdn, message)
        return r.Outcome(message, r.OUTCOME_FAILED)
    return r._vtu_outcome(pa, user, msisdn, amount, label, txn, txn.meta, receipt)


def payment_receipt(user, msisdn, reference):
    """Recover a confirmed purchase in the owner's authenticated chat."""
    from .models import Instruction
    from wallet.review_state import transaction_review_map

    r = router()
    if not user.is_active or not r._channel_linked(user, msisdn):
        return r.reply(msisdn, "Sign in to your linked account to view this receipt.")
    instruction = Instruction.objects.select_related("transaction").filter(
        transaction__user=user, transaction__reference=reference, product="vas",
        account__environment=config().get("ENVIRONMENT", "sandbox")).first()
    if instruction is None:
        return r.reply(msisdn, "No bill payment was found for that reference on your account.")
    txn = instruction.transaction
    if transaction_review_map([txn]) or txn.transaction_status == Transaction.PENDING:
        return r.reply(msisdn, "This payment is still being confirmed. Do not pay again. "
            f"Reply *receipt {txn.reference}* to check later.")
    if txn.transaction_status == Transaction.FAILED and instruction.state == "failed":
        return r.reply(msisdn, f"This payment failed and was refunded. Reference: {txn.reference}.")
    if txn.transaction_status != Transaction.SUCCESS or instruction.state != "success":
        return r.reply(msisdn, "The final payment outcome needs review. Please contact support.")
    meta = txn.meta if isinstance(txn.meta, dict) else {}
    rows = [("Service", txn.service), ("Amount", r._money(txn.amount))]
    if instruction.receipt_pending:
        rows.append(("Token", f"Being retrieved. Reply receipt {txn.reference} to check again."))
    rows.extend((label, meta[key]) for key, label in (("token", "Token"), ("electricity_units", "Units"))
                if isinstance(meta.get(key), str) and meta[key])
    return r.reply_receipt(msisdn, "Bill payment receipt", rows, ref=txn.reference, user=user)
