import base64
import binascii
import hmac
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from common.http import api, fail, idempotent_replay, ok, require_user, spend_key, verify_transaction_pin, provider_purchase_response
from common.ratelimit import ratelimit
from wallet.services import DuplicateTransaction, InsufficientFunds, LimitExceeded, existing_for_key

from . import accounts, address, bills, notifications, upgrades
from .client import ProviderError, config, money, phone
from .models import AddressVerification, IdentityChallenge, BillerValidation


def _error(exc):
    if isinstance(exc, ProviderError):
        return fail("The verification or payment service is temporarily unavailable.", status=503,
                    code=exc.code, pending=exc.uncertain)
    if isinstance(exc, InsufficientFunds):
        return fail("Insufficient available balance", status=402)
    return fail(str(exc), status=400)


@api
@require_user
def status(request):
    user = request.user_obj
    bank = accounts.account_for(user)
    from .secrets import unseal
    from accounts.models import IdentityProof
    proofs = set(IdentityProof.objects.filter(user=user, source__in=["9psb_otp", "9psb_face"]).values_list("identity_type", flat=True))
    profile = unseal(bank.encrypted_profile) if bank and bank.encrypted_profile else {
        "lastName": user.last_name, "otherNames": user.first_name, "phoneNo": user.phone, "email": user.email}
    challenge = IdentityChallenge.objects.filter(user=user, environment=config().get("ENVIRONMENT", "sandbox")).exclude(state__in=["complete", "failed", "expired"]).order_by("-created").first()
    progress = accounts.challenge_payload(challenge) if challenge else None
    if progress and progress["account_setup_state"] == "expired":
        progress = None
    return ok(success=True, **accounts.account_payload(user), tier=user.tier, profile=profile,
        identity_challenge=({**progress, "kind": challenge.kind} if progress else None),
        bvn_verified="bvn" in proofs, nin_verified="nin" in proofs,
        face_verified=IdentityProof.objects.filter(user=user, source="9psb_face").exists(),
        address_verified=user.address_verified,
        identity_verification_provider="9psb", face_rail="9psb", address_rail="prembly",
        upgrade_requirements={"2": ["BVN", "NIN", "Utility bill", "Identity documents", "Signature"],
                              "3": ["BVN", "NIN", "Utility bill", "Prembly address verification"]})


@api
@ratelimit("bank_identity_start", limit=5, window=600)
@require_user
def identity_start(request):
    body = request.data
    try:
        result = accounts.start_identity(request.user_obj, body.get("kind"), body.get("identifier", ""),
            body.get("profile", {}), method=body.get("method", "OTP"), image=body.get("image", ""))
        return ok(result)
    except (ValueError, ProviderError) as exc:
        return _error(exc)


@api
@ratelimit("bank_identity_confirm", limit=10, window=600)
@require_user
def identity_confirm(request):
    try:
        return ok(accounts.confirm_identity(request.user_obj, request.data.get("reference", ""), request.data.get("otp", "")))
    except (ValueError, ProviderError) as exc:
        return _error(exc)


@api
@require_user
def identity_status(request):
    challenge = IdentityChallenge.objects.filter(user=request.user_obj, environment=config().get("ENVIRONMENT", "sandbox"), reference=request.data.get("reference", "")).first()
    if not challenge:
        return fail("Verification request not found", status=404)
    if challenge.state == "opening" and challenge.verified_at:
        try:
            return ok(accounts.complete_account(challenge))
        except (ValueError, ProviderError) as exc:
            return _error(exc)
    return ok(accounts.challenge_payload(challenge))


@api
@ratelimit("bank_upgrade", limit=3, window=600)
@require_user
def upgrade(request):
    try:
        return ok(upgrades.submit_upgrade(request.user_obj, request.data.get("tier", 0), request.data.get("profile", {}),
            address_reference=request.data.get("address_reference", "")))
    except (ValueError, ProviderError) as exc:
        return _error(exc)


@api
@ratelimit("address_start", limit=2, window=3600)
@require_user
def address_start(request):
    try:
        row = address.start(request.user_obj, request.data.get("address", {}))
        return ok(success=False, pending=True, reference=row.reference, state=row.state,
                  message="Your address has been submitted for physical verification.")
    except (ValueError, ProviderError) as exc:
        return _error(exc)


@api
@ratelimit("address_status", limit=5, window=600)
@require_user
def address_status(request):
    row = AddressVerification.objects.filter(user=request.user_obj, reference=request.data.get("reference", "")).first()
    if not row:
        return fail("Address verification not found", status=404)
    try:
        address.refresh(row)
        return ok(success=row.state == "verified", pending=row.state not in {"verified", "declined"},
                  state=row.state, reference=row.reference)
    except ProviderError as exc:
        return _error(exc)


@api
@ratelimit("bank_catalog", limit=30, window=60)
@require_user
def catalog(request):
    try:
        accounts.require_enabled(request.user_obj)
        body = request.data
        result = bills.catalog(body.get("kind"), **{k: body[k] for k in ("phone", "category", "biller") if k in body})
        return ok(success=True, data=result)
    except (ProviderError, ValueError, KeyError) as exc:
        return _error(exc)


@api
@ratelimit("bank_biller_validate", limit=10, window=60)
@require_user
def bill_validate(request):
    body = request.data
    try:
        return ok(bills.validate(request.user_obj, body.get("biller_id", ""), body.get("item_id", ""),
            body.get("customer", ""), body.get("amount")))
    except (ProviderError, ValueError) as exc:
        return _error(exc)


@api
@ratelimit("bank_purchase", limit=15, window=60)
@require_user
def purchase(request):
    user, body = request.user_obj, request.data
    if not isinstance(body.get("idempotency_key"), str) or not body["idempotency_key"].strip():
        return fail("A payment reference is required", status=400)
    try:
        mobile, amount = phone(body.get("phone")), money(body.get("amount"))
        binding = [body.get("product_id", "")]
        if body.get("kind") == "bill":
            validation = BillerValidation.objects.get(user=user, reference=body.get("validation_reference", ""))
            if money(validation.amount) != amount:
                return fail("Review the validated bill amount before paying.", status=409, code="price_changed")
            # A renewed validation token must not create another payment after a
            # lost response. Bind the stable request to its economic details.
            binding = [validation.biller_id, validation.item_id, validation.customer_digest]
        key = spend_key(body["idempotency_key"], user, "9psb", body.get("kind"), mobile, amount, *binding)
    except (ValueError, BillerValidation.DoesNotExist):
        return fail("Verify the payment details before paying.", status=400)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay is not None:
        return replay
    denied = verify_transaction_pin(user, body.get("transaction_pin"))
    if denied is not None:
        return denied
    try:
        if body.get("kind") == "bill":
            txn = bills.purchase_bill(user, body.get("validation_reference", ""), body.get("phone"), key)
        else:
            txn = bills.purchase_topup(user, body.get("kind"), body.get("phone"), body.get("amount"),
                product_id=body.get("product_id", ""), idempotency_key=key)
        from wallet.models import Transaction
        state = "success" if txn.transaction_status == Transaction.SUCCESS else "failed" if txn.transaction_status == Transaction.FAILED else "pending"
        return provider_purchase_response(state, txn, txn.meta, success_message="Payment completed.", token=txn.meta.get("token", ""))
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(user, key)) or fail("Payment is already processing", status=409)
    except (ProviderError, ValueError, LimitExceeded, InsufficientFunds) as exc:
        return _error(exc)
    except bills.BillerValidation.DoesNotExist:
        return fail("Customer validation not found", status=400)


@csrf_exempt
def webhook(request):
    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)
    if len(request.body) > 65536:
        return JsonResponse({"success": False}, status=413)
    user, password = config().get("WEBHOOK_USERNAME", ""), config().get("WEBHOOK_PASSWORD", "")
    authorization = request.headers.get("Authorization", "")
    try:
        scheme, encoded = authorization.split(" ", 1)
        decoded = base64.b64decode(encoded, validate=True).decode()
    except (ValueError, UnicodeError, binascii.Error):
        return JsonResponse({"success": False}, status=401)
    if not user or not password or scheme.lower() != "basic" or not hmac.compare_digest(decoded, user + ":" + password):
        return JsonResponse({"success": False}, status=401)
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError
        notifications.ingest(request.GET.get("event", ""), payload)
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "code": "INVALID_EVENT"}, status=400)
    # Durable inbox is committed. The worker confirms with the bank, so the
    # callback remains fast even when the bank's read API is unavailable.
    return JsonResponse(notifications.ACK)
