"""Dynamic 9PSB catalog and server-bound customer/price validation."""
import secrets
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from wallet.services import debit

from .accounts import require_enabled
from .client import Client, ProviderError, config, money, phone, require_environment
from .models import BillerValidation
from .payments import bind_instruction, digest, dispatch, spending_account
from .secrets import seal, unseal
from .vas import fields_schema, selection_amount, text_value


def require_vas(user):
    require_enabled(user)
    if config().get("VAS_ENABLED") is not True:
        raise ValueError("Bill payments are temporarily unavailable")


def catalog(kind, **params):
    client = Client("vas")
    key = "9psb:catalog:" + digest([client._cache_key(), kind, params])
    existing = cache.get(key)
    if existing is not None:
        return existing
    if kind == "categories":
        response = client.categories()
    elif kind == "billers":
        response = client.billers(params["category"])
    elif kind == "fields":
        response = client.fields(params["biller"])
    elif kind == "data":
        response = client.data_plans(params["phone"])
    elif kind == "network":
        response = client.network(params["phone"])
    else:
        raise ValueError("Unknown catalog")
    if not response.ok or str(response.body.get("responseCode")) != "200":
        raise ProviderError("catalog_unavailable")
    expected = dict if kind == "network" else list
    if not isinstance(response.data, expected):
        raise ProviderError("invalid_catalog")
    data = list(fields_schema(response.data).values()) if kind == "fields" else response.data
    cache.set(key, data, timeout=300)
    return data


def validate(user, biller, item, customer, amount, *, inputs=None):
    require_vas(user)
    fields = fields_schema(catalog("fields", biller=biller))
    amount = selection_amount(fields, item, amount)
    customer = text_value(customer, maximum=100)
    if not customer:
        raise ValueError("Enter a valid customer number")
    inputs = {} if inputs is None else inputs
    if not isinstance(inputs, dict) or set(inputs) - {"firstname", "lastname"}:
        raise ValueError("Refresh the biller fields before validating")
    params = {}
    for key, value in (("itemId", item), ("amount", amount)):
        if key in fields and fields[key]["validation"] == "Y":
            params["item" if key == "itemId" else key] = value
    for key in ("firstname", "lastname"):
        if key in fields and fields[key]["validation"] == "Y":
            value = text_value(inputs.get(key, ""), maximum=100)
            if not value:
                raise ValueError("Enter the customer's " + ("first name" if key == "firstname" else "last name"))
            params[key] = value
        elif inputs.get(key):
            raise ValueError("Refresh the biller fields before validating")
    reply = Client("vas").validate_customer(biller, customer, **params)
    result = reply.data if isinstance(reply.data, dict) else {}
    name = result.get("customerName")
    if (not reply.ok or str(reply.body.get("responseCode")) != "200"
            or not isinstance(name, str) or not name.strip() or len(name) > 160):
        raise ValueError("The biller could not validate this customer")
    if "amount" in result and money(result["amount"]) != money(amount):
        raise ValueError("The bill price changed. Refresh and try again")
    other_field = result.get("otherField", "")
    if not isinstance(other_field, str) or len(other_field) > 4096:
        raise ProviderError("invalid_biller_validation")
    row = BillerValidation.objects.create(user=user, reference=secrets.token_urlsafe(24),
        environment=config().get("ENVIRONMENT", "sandbox"),
        biller_id=biller, item_id=item, customer_digest=digest(customer), amount=amount,
        encrypted_result=seal({"customer": customer, "name": name.strip(), "other_field": other_field}),
        expires_at=timezone.now() + timedelta(minutes=10))
    return {"success": True, "validation_reference": row.reference, "customer_name": str(result["customerName"]),
            "amount": str(amount), "expires_at": row.expires_at.isoformat()}


def purchase_bill(user, validation_ref, mobile, idempotency_key, *, channel="app"):
    require_vas(user)
    bank = spending_account(user, refresh=True)
    mobile = phone(mobile)
    with transaction.atomic():
        row = BillerValidation.objects.select_for_update().get(reference=validation_ref, user=user)
        require_environment(row.environment)
        if row.consumed or row.expires_at <= timezone.now():
            raise ValueError("Customer validation expired. Verify again")
        result = unseal(row.encrypted_result)
        txn = debit(user, row.amount, "Utility bill payment", meta={"provider": "9psb", "provider_purchase": True,
            "vas_rail": "9psb", "vas_type": "bill", "reconcile": True, "biller_id": row.biller_id,
            "item_id": row.item_id, "customer_name": result["name"], "channel": channel}, idempotency_key=idempotency_key)
        instruction = bind_instruction(txn, "vas", "bill", {"amount": str(row.amount), "source": bank.number,
            "phone": mobile, "biller_id": row.biller_id, "item_id": row.item_id,
            "customer": result["customer"], "customer_name": result["name"], "other_field": result["other_field"]})
        row.consumed = True
        row.save(update_fields=["consumed"])
    dispatch(instruction)
    txn.refresh_from_db()
    return txn


def purchase_topup(user, kind, mobile, amount, *, product_id="", idempotency_key, channel="app"):
    require_vas(user)
    bank = spending_account(user, refresh=True)
    mobile = phone(mobile)
    network = catalog("network", phone=mobile).get("network")
    if not isinstance(network, str) or not network:
        raise ValueError("We could not identify this phone network")
    amount = money(amount)
    if kind == "data":
        plans = catalog("data", phone=mobile)
        match = [p for p in plans if isinstance(p, dict) and str(p.get("productId")) == str(product_id)]
        if len(match) != 1 or money(match[0].get("amount")) != amount:
            raise ValueError("The selected data plan changed. Refresh and try again")
    elif kind != "airtime":
        raise ValueError("Unknown top-up product")
    with transaction.atomic():
        txn = debit(user, Decimal(amount), kind.title(), meta={"provider": "9psb", "provider_purchase": True,
            "vas_rail": "9psb", "vas_type": kind, "reconcile": True, "phone": mobile,
            "network": network, "product_id": product_id, "channel": channel}, idempotency_key=idempotency_key)
        instruction = bind_instruction(txn, "vas", kind, {"amount": amount, "source": bank.number,
            "phone": mobile, "network": network, "product_id": product_id})
    dispatch(instruction)
    txn.refresh_from_db()
    return txn
