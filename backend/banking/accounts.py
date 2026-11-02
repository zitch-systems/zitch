"""Bank account and identity lifecycle shared by app and WhatsApp."""
import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from cryptography.fernet import InvalidToken
from django.db import transaction
from django.utils import timezone

from accounts.models import User, hash_identifier, record_identity_proof
from wallet.models import Wallet

from .client import (Client, ProviderError, affirmative_code, config, customer_enabled, sandbox, phone,
    require_environment, sandbox_auto_verify, wallet_number)
from .models import BankAccount, IdentityChallenge, PreviousBankArchive
from .secrets import seal, unseal
from .validation import wallet_profile

log = logging.getLogger(__name__)


def sandbox_tester(user):
    """Allow only explicit IDs or a verified phone enrolled for sandbox use."""
    conf = config()
    if str(user.pk) in conf.get("SANDBOX_USER_IDS", []):
        return True
    if not user.phone_verified:
        return False
    try:
        number = phone(user.phone)
    except ValueError:
        return False
    for enrolled in conf.get("SANDBOX_PHONES", []):
        try:
            if number == phone(enrolled):
                return True
        except ValueError:
            continue
    return False


def require_enabled(user):
    if not user.is_active or not customer_enabled():
        raise ValueError("Banking setup is temporarily unavailable")
    if sandbox() and not sandbox_tester(user):
        raise ValueError("This bank connection is available to enrolled sandbox testers only")
    existing = BankAccount.objects.filter(wallet__user_id=user.pk).first()
    if existing:
        require_environment(existing.environment)


def same_name(first, second):
    if not isinstance(first, str) or not isinstance(second, str):
        return False
    def parts(value):
        return set(re.findall(r"[a-z]+", str(value).casefold())) - {"mr", "mrs", "miss", "ms", "dr"}
    a, b = parts(first), parts(second)
    return len(a) >= 2 and a == b


def account_for(user):
    return BankAccount.objects.filter(wallet__user_id=user.pk,
        environment=config().get("ENVIRONMENT", "sandbox")).first()


def historical_balance(wallet):
    from django.db.models import Sum
    archive = PreviousBankArchive.objects.filter(wallet=wallet).first()
    retained = archive.balance if archive else Decimal("0")
    if archive:
        retained += archive.adjustments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return retained


def spendable(wallet):
    return max(Decimal("0"), wallet.balance - historical_balance(wallet))


def retain_historical_refund(txn, amount=None):
    """Run under the existing transaction/wallet locks before refunding."""
    from .models import HistoricalBalanceAdjustment
    if (txn.meta or {}).get("provider") == "9psb":
        return
    archive = PreviousBankArchive.objects.filter(wallet__user_id=txn.user_id).first()
    if archive:
        retained = txn.amount if amount is None else Decimal(str(amount))
        row, _ = HistoricalBalanceAdjustment.objects.get_or_create(archive=archive, transaction=txn,
            defaults={"amount": retained})
        if row.amount != retained:
            raise ValueError("Historical settlement amount conflicts with retained evidence")


def account_payload(user):
    bank = account_for(user)
    allowed = customer_enabled() and user.is_active
    if sandbox():
        allowed = allowed and sandbox_tester(user)
    ready = bool(bank and allowed and bank.status == "active")
    can_spend = bool(ready and bank.pnd is False and bank.frozen is False
        and not bank.opening_review_required and bank.available_balance is not None
        and bank.checked_at and bank.checked_at >= timezone.now() - timedelta(minutes=5)
        and user.email_verified and user.phone_verified and user.tier >= 1)
    if can_spend:
        can_spend = not PreviousBankArchive.objects.filter(wallet=bank.wallet, pending_transactions__gt=0).exists()
    from .fees import transfer_fee
    opening_review = bool(ready and bank.opening_review_required)
    return {"provider": "9psb", "bank_name": "9PSB" if ready else "",
        "identity_available": bool(allowed and user.email_verified and user.phone_verified),
        "address_verification_available": False,
        "account_number": bank.number if ready else "", "account_name": bank.name if ready else "",
        "bank_accounts": ([{"account_number": bank.number, "account_name": bank.name, "bank_name": "9PSB"}] if ready else []),
        "has_account": ready, "available": ready, "can_fund": ready,
        "bank_tier": bank.tier if bank else 0, "sandbox": sandbox(),
        "transfers_available": can_spend and transfer_fee(1) is not None, "spending_available": can_spend,
        "bill_payments_available": can_spend and config().get("VAS_ENABLED") is True,
        "account_setup_state": "bank_history_review" if opening_review else "ready" if ready else "setup_required" if allowed else "banking_unavailable",
        "maximum_single_deposit": str(bank.maximum_deposit) if bank and bank.maximum_deposit is not None else None,
        "maximum_balance": str(bank.maximum_balance) if bank and bank.maximum_balance is not None else None,
        "pnd": bank.pnd if bank else None,
        "migration_message": "Your bank account balance needs review. Contact support before spending." if opening_review else "" if ready else "Complete your Zitch account setup to continue banking."}


def check_identity_request(user, kind, identifier, profile, *, method="OTP"):
    """Every check made before contacting the bank, without contacting it.

    Returns the normalized profile and identity digest. The face page uses this
    to confirm details before the photo step, so a rejected name or identity is
    reported before the customer is sent to the camera.
    """
    if not user.email_verified or not user.phone_verified:
        raise ValueError("Verify your email address and phone number first")
    # Tier 1 is opened with the BVN only; the NIN is given with the Tier 2 upgrade.
    if kind != "bvn":
        raise ValueError("Use your BVN to open your account")
    if not re.fullmatch(r"[0-9]{11}", identifier or ""):
        raise ValueError("Enter your eleven-digit BVN")
    if method not in {"OTP", "FACIAL"}:
        raise ValueError("Choose OTP or face verification")
    profile = wallet_profile(profile)
    profile["email"] = user.email
    if phone(profile["phoneNo"]) != phone(user.phone):
        raise ValueError("Use your verified Zitch phone number")
    if not same_name(user.get_full_name(), profile["otherNames"] + " " + profile["lastName"]):
        raise ValueError("Use the legal name on your Zitch profile")
    digest = hash_identifier(identifier)
    if User.objects.exclude(pk=user.pk).filter(**{kind + "_hash": digest}).exists():
        raise ValueError("This identity is already linked to an account")
    if IdentityChallenge.objects.exclude(user=user).filter(identity_hash=digest, verified_at__isnull=False).exists():
        raise ValueError("This identity is already linked to an account")
    return profile, digest


RESEND_AFTER = timedelta(seconds=60)


def start_identity(user, kind, identifier, profile, *, method="OTP", image=""):
    require_enabled(user)
    profile, digest = check_identity_request(user, kind, identifier, profile, method=method)
    if method == "FACIAL":
        from .validation import base64_file
        image = base64_file(image, maximum_bytes=5*1024*1024, image_only=True)
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        previous = IdentityChallenge.objects.filter(user=user, expires_at__gt=timezone.now(),
            state__in=["initiating", "pending", "verifying", "verified", "opening", "unconfirmed"]).first()
        if previous and sandbox_auto_verify() and not previous.verified_at:
            # A sandbox tester may be stuck on a check the sandbox can never
            # finish. Retire it; the auto-verified attempt below replaces it.
            IdentityChallenge.objects.filter(pk=previous.pk).update(state="expired", encrypted_input="")
            previous = None
        if previous:
            require_environment(previous.environment)
        if (previous and previous.state == "pending" and previous.method == "OTP" == method
                and previous.identity_hash == digest and not previous.verified_at):
            # "Resend code": replace the unanswered code with a new one, at most once a minute.
            if previous.created > timezone.now() - RESEND_AFTER:
                return {**challenge_payload(previous), "message": "Please wait a minute before requesting a new code."}
            IdentityChallenge.objects.filter(pk=previous.pk).update(state="expired", encrypted_input="")
            previous = None
        if previous:
            require_environment(previous.environment)
            if previous.identity_hash == digest and previous.method == method:
                return challenge_payload(previous)
            raise ValueError("Finish the current identity check before starting another")
        challenge = IdentityChallenge.objects.create(user=user, reference="ZI" + secrets.token_hex(10).upper(),
            kind=kind, method=method, identity_hash=digest, last4=identifier[-4:],
            environment=config().get("ENVIRONMENT", "sandbox"),
            encrypted_input=seal({"profile": profile, "identifier": identifier}),
            expires_at=timezone.now() + timedelta(minutes=10))
    if sandbox_auto_verify():
        # Sandbox testing: any well-formed BVN/NIN is accepted. The bank is not
        # asked to check it; opening the wallet still goes to the bank sandbox.
        # Server-generated values only in logs; the challenge row holds the ID type.
        if method == "OTP":
            # Keep the real customer journey: text a real code to the tester's
            # verified phone. confirm_identity accepts any six-digit code here.
            IdentityChallenge.objects.filter(pk=challenge.pk).update(state="pending")
            challenge.refresh_from_db()
            _send_sandbox_code(user, challenge.reference)
            return challenge_payload(challenge)
        IdentityChallenge.objects.filter(pk=challenge.pk).update(state="verified", verified_at=timezone.now())
        challenge.refresh_from_db()
        log.warning("sandbox_identity_auto_verified user=%s ref=%s", user.pk, challenge.reference)
        return complete_account(challenge)
    try:
        response = Client().initiate_identity(challenge.reference, kind, identifier,
            method=method, mobile=profile["phoneNo"], image=image)
    except (ProviderError, ValueError):
        # An uncertain identity request must not be automatically repeated.
        IdentityChallenge.objects.filter(pk=challenge.pk).update(state="unconfirmed")
        raise
    state = response.identity_state
    challenge.state = {"SUCCESS": "verified", "PENDING": "pending", "FAILED": "failed"}[state]
    if state == "SUCCESS":
        challenge.verified_at = timezone.now()
    if state == "FAILED":
        challenge.encrypted_input = ""
    challenge.save(update_fields=["state", "verified_at", "encrypted_input", "updated"])
    if state == "SUCCESS":
        return complete_account(challenge)
    return challenge_payload(challenge)


def resend_code(user, reference):
    """Text a new code for an identity check that is still waiting for one."""
    challenge = IdentityChallenge.objects.filter(user=user, reference=reference, method="OTP",
        state="pending", verified_at__isnull=True).exclude(encrypted_input="").first()
    if not challenge:
        raise ValueError("Start your verification again")
    stored = unseal(challenge.encrypted_input)
    return start_identity(user, challenge.kind, stored["identifier"], stored["profile"], method="OTP")


def _send_sandbox_code(user, reference):
    """Sandbox only: text a real code so testers see the normal OTP step."""
    from utility.providers import send_sms
    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        delivery = send_sms(user.phone, f"Your Zitch verification code is {code}")
    except Exception:  # The code is a courtesy here; any code is accepted.
        delivery = {"success": False}
    log.warning("sandbox_identity_code_sent user=%s ref=%s delivered=%s",
                user.pk, reference, bool(delivery.get("success")) and not delivery.get("mock"))


def holder_matches(user, bank_name):
    """The bank's account holder must be the customer.

    Sandbox auto-verify is the one exception: a made-up test BVN carries the
    sandbox's own dummy name, so the wallet the sandbox opens is accepted in
    that name. The read-back below still has to match the name the bank gave.
    """
    return sandbox_auto_verify() or same_name(user.get_full_name(), bank_name)


def challenge_payload(challenge):
    # In sandbox auto-verify mode an attempt the sandbox never finished must not
    # block the tester for good; outside it, a verified attempt may still be
    # opening a real account and is never expired here.
    stale = challenge.expires_at <= timezone.now() and (not challenge.verified_at or sandbox_auto_verify())
    if stale and challenge.state not in {"complete", "failed", "expired"}:
        challenge.state, challenge.encrypted_input = "expired", ""
        challenge.save(update_fields=["state", "encrypted_input", "updated"])
    return {"success": challenge.state == "complete", "pending": challenge.state not in {"complete", "failed", "expired"},
        "tracking_id": challenge.reference, "reference": challenge.reference,
        "otp_required": challenge.method == "OTP" and challenge.state == "pending",
        "account_setup_state": challenge.state, "expires_at": challenge.expires_at.isoformat(),
        "message": "Enter the 6-digit code we sent by SMS." if challenge.state == "pending" and challenge.method == "OTP"
                   else "This verification has expired. Start again." if challenge.state == "expired"
                   else "Verification was declined. Check your details." if challenge.state == "failed"
                   else "Verification is being processed."}


def confirm_identity(user, ref, otp):
    require_enabled(user)
    challenge = IdentityChallenge.objects.filter(user=user, reference=ref).first()
    if not challenge:
        raise ValueError("Verification request not found")
    require_environment(challenge.environment)
    if challenge.state == "complete":
        return {"success": True, **account_payload(user)}
    if challenge.verified_at and challenge.state in {"verified", "opening"}:
        return complete_account(challenge)
    if challenge.expires_at <= timezone.now() or challenge.failures >= 5:
        IdentityChallenge.objects.filter(pk=challenge.pk).update(state="expired", encrypted_input="")
        raise ValueError("This verification request has expired. Start again")
    if not re.fullmatch(r"[0-9]{6}", otp or ""):
        raise ValueError("Enter the six-digit code")
    claimed = IdentityChallenge.objects.filter(pk=challenge.pk, state="pending", method="OTP").update(state="verifying")
    if not claimed:
        return challenge_payload(challenge)
    if sandbox_auto_verify():
        # Sandbox testing: any six-digit code passes; the bank is not asked.
        challenge.verified_at, challenge.state = timezone.now(), "verified"
        challenge.save(update_fields=["state", "verified_at", "updated"])
        log.warning("sandbox_identity_code_accepted user=%s ref=%s", user.pk, challenge.reference)
        return complete_account(challenge)
    try:
        response = Client().verify_identity(ref, otp)
    except ProviderError:
        IdentityChallenge.objects.filter(pk=challenge.pk).update(state="unconfirmed")
        raise
    if response.identity_state == "SUCCESS":
        challenge.verified_at = timezone.now()
        challenge.state = "verified"
    elif response.identity_state == "FAILED":
        challenge.failures += 1
        challenge.state = "pending" if challenge.failures < 5 else "failed"
        if challenge.state == "failed":
            challenge.encrypted_input = ""
    else:
        challenge.state = "unconfirmed"
    challenge.save(update_fields=["state", "verified_at", "failures", "encrypted_input", "updated"])
    return complete_account(challenge) if challenge.verified_at else challenge_payload(challenge)


def complete_account(challenge, *, known_number=None):
    """Attach the bank account for a verified identity check.

    known_number (sandbox only) attaches a test wallet the bank already opened
    for this tester, read back by its account number.
    """
    require_environment(challenge.environment)
    if known_number and not sandbox():
        raise ValueError("Attaching a known wallet is for the bank sandbox only")
    if (not known_number and sandbox_auto_verify() and challenge.state != "complete"
            and challenge.expires_at <= timezone.now()):
        return challenge_payload(challenge)  # Retires a sandbox attempt that never finished.
    if not challenge.verified_at or not challenge.encrypted_input:
        raise ValueError("Complete bank identity verification first")
    user = challenge.user
    existing = BankAccount.objects.filter(wallet__user_id=user.pk).first()
    if existing:
        require_environment(existing.environment)
    stored = unseal(challenge.encrypted_input)
    current = account_for(user)
    if current:
        # A second BVN/NIN/face challenge verifies ownership; it does not open a
        # second account. Tier 2 still requires both identities and bank approval.
        return _finish_identity(challenge, current)
    claimed = IdentityChallenge.objects.filter(pk=challenge.pk, state="verified").update(state="opening")
    if known_number:
        response = Client().wallet(known_number)
    elif not claimed:
        if challenge.state != "opening":
            return {**challenge_payload(challenge), "pending": True, "otp_required": False}
        response = Client().lookup_wallet(challenge.kind, stored["identifier"])
    else:
        response = Client().open_wallet(challenge.reference, stored["profile"],
            kind=challenge.kind, identifier=stored["identifier"])
    if sandbox() and (not claimed or "already exist" in str(response.body.get("message") or "").lower()) and not (
            response.ok and re.fullmatch(r"[0-9]{10}", wallet_number(response.data if isinstance(response.data, dict) else {}))):
        response = _sandbox_existing_wallet(challenge, stored) or response
    data = response.data if isinstance(response.data, dict) else {}
    number = wallet_number(data)
    name = data.get("fullName") or data.get("name") or ""
    if not response.ok or not re.fullmatch(r"[0-9]{10}", number):
        # Record why the bank did not return an account, without personal data.
        bank_message = str(response.body.get("message") or response.body.get("responseMessage") or "")[:160]
        log.warning("bank_wallet_open_not_confirmed ref=%s http=%s code=%s message=%r",
                    challenge.reference, response.http_status, response.body.get("responseCode"), bank_message)
        payload = {**challenge_payload(challenge), "pending": True, "otp_required": False}
        if sandbox() and bank_message:
            # Testers need the bank's reason; customers keep the neutral message.
            payload["message"] = "Your account could not be opened yet: " + bank_message
        return payload
    if not holder_matches(user, name):
        IdentityChallenge.objects.filter(pk=challenge.pk).update(state="review")
        raise ValueError("Bank account details need review. Contact support")
    # Read back the new account before attaching any funding destination.
    inquiry = Client().wallet(number)
    remote = inquiry.data if isinstance(inquiry.data, dict) else {}
    if not inquiry.ok or wallet_number(remote) != number or not same_name(name, remote.get("name", "")):
        return {"success": False, "pending": True, "otp_required": False, "message": "Your bank account is being confirmed."}
    with transaction.atomic():
        wallet, _ = Wallet.objects.get_or_create(user=user)
        wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
        existing = BankAccount.objects.filter(wallet=wallet).first()
        if existing and existing.number != number:
            raise ValueError("A different bank account is already attached")
        if not existing:
            if wallet.account_number and not PreviousBankArchive.objects.filter(wallet=wallet).exists():
                raise ValueError("Previous account archive must complete before bank setup")
            current = BankAccount.objects.create(wallet=wallet, number=number, name=name,
                environment=config().get("ENVIRONMENT", "sandbox"), encrypted_profile=seal(stored["profile"]),
                encrypted_bvn=seal(stored["identifier"]) if challenge.kind == "bvn" else "")
            wallet.account_number, wallet.account_name, wallet.bank_name = number, name, "9PSB"
            wallet.account_reference = "9PSB-" + str(user.pk)
            wallet.bank_accounts = []
            wallet.bank_tier = 0
            wallet.pnd_lifted = False
            wallet.save(update_fields=["account_number", "account_name", "bank_name", "account_reference", "bank_accounts", "bank_tier", "pnd_lifted", "updated"])
        else:
            current = existing
    update_account(current, inquiry)
    if current.ledger_balance == 0:
        current.opening_review_required = False
        current.save(update_fields=["opening_review_required"])
        update_account(current, inquiry)
    return _finish_identity(challenge, current)


def _sandbox_existing_wallet(challenge, stored):
    """Sandbox only: find the test wallet an earlier attempt already opened.

    The test bank keeps one wallet per tester. A retry with another test BVN is
    refused as "A Wallet Already Exists", and looking it up needs the identity
    it was opened with, so try this tester's earlier test identities too.
    Production never uses another challenge's identity.
    """
    candidates = [(challenge.kind, stored["identifier"])]
    earlier = IdentityChallenge.objects.filter(user=challenge.user, environment=challenge.environment,
        verified_at__isnull=False).exclude(pk=challenge.pk).exclude(encrypted_input="").order_by("-created")[:5]
    for row in earlier:
        try:
            candidates.append((row.kind, unseal(row.encrypted_input)["identifier"]))
        except (InvalidToken, KeyError, TypeError, ValueError):  # Unreadable: not a candidate.
            continue
    client, seen = Client(), set()
    for kind, identifier in candidates:
        if (kind, identifier) in seen:
            continue
        seen.add((kind, identifier))
        try:
            reply = client.lookup_wallet(kind, identifier)
        except (ProviderError, ValueError) as exc:
            log.warning("sandbox_existing_wallet_lookup ref=%s error=%s", challenge.reference, getattr(exc, "code", "invalid"))
            continue
        data = reply.data if isinstance(reply.data, dict) else {}
        if reply.ok and re.fullmatch(r"[0-9]{10}", wallet_number(data)):
            log.info("sandbox_existing_wallet_found ref=%s", challenge.reference)
            return reply
        log.warning("sandbox_existing_wallet_lookup ref=%s http=%s message=%r", challenge.reference,
                    reply.http_status, str(reply.body.get("message") or "")[:120])
    return None


@transaction.atomic
def _finish_identity(challenge, bank):
    require_environment(challenge.environment)
    require_environment(bank.environment)
    wallet = Wallet.objects.select_for_update().get(pk=bank.wallet_id)
    user = User.objects.select_for_update().get(pk=wallet.user_id)
    challenge = IdentityChallenge.objects.select_for_update().get(pk=challenge.pk)
    require_environment(challenge.environment)
    if challenge.state == "complete":
        return {"success": True, **account_payload(user)}
    if not challenge.verified_at or not holder_matches(user, bank.name):
        raise ValueError("Bank identity requires review")
    if User.objects.exclude(pk=user.pk).filter(**{challenge.kind + "_hash": challenge.identity_hash}).exists():
        raise ValueError("This identity is already linked to an account")
    prior_hash = getattr(user, challenge.kind + "_hash")
    if prior_hash and not hmac.compare_digest(prior_hash, challenge.identity_hash):
        raise ValueError("Identity changes require support review")
    record_identity_proof(user, challenge.kind, challenge.identity_hash, prehashed=True,
        source="9psb_face" if challenge.method == "FACIAL" else "9psb_otp",
        # Marked so sandbox shortcuts are never mistaken for real checks.
        provider_reference=("SANDBOX-AUTO-" if sandbox_auto_verify() else "") + challenge.reference,
        verified_name=bank.name)
    setattr(user, challenge.kind + "_hash", challenge.identity_hash)
    setattr(user, challenge.kind + "_last4", challenge.last4)
    setattr(user, challenge.kind + "_verified", True)
    if challenge.method == "FACIAL":
        user.face_verified = True
    user.recompute_tier()
    user.save(update_fields=[challenge.kind + "_hash", challenge.kind + "_last4", challenge.kind + "_verified", "face_verified", "tier"])
    challenge.state, challenge.encrypted_input = "complete", ""
    challenge.consumed_at = timezone.now()
    challenge.save(update_fields=["state", "encrypted_input", "consumed_at", "updated"])
    return {"success": True, **account_payload(user), "tier": user.tier,
            "bvn_verified": user.bvn_verified, "nin_verified": user.nin_verified, "face_verified": user.face_verified}


def _amount(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _restriction(value):
    if value is True or str(value).strip().upper() in {"TRUE", "YES", "Y", "1", "RESTRICTED", "FROZEN", "ACTIVE"}:
        return True
    if value is False or str(value).strip().upper() in {"FALSE", "NO", "N", "0", "UNRESTRICTED", "INACTIVE"}:
        return False
    return None


def update_account(bank, reply=None):
    require_environment(bank.environment)
    reply = reply or Client().wallet(bank.number)
    data = reply.data if isinstance(reply.data, dict) else {}
    if not reply.ok or wallet_number(data) != bank.number:
        raise ProviderError("account_confirmation_failed")
    successful = data.get("isSuccessful", True)
    if (not affirmative_code(reply.body.get("responseCode"))
            or not affirmative_code(data.get("responseCode"))
            or (successful is not True and successful not in ("true", "True"))):
        raise ProviderError("account_confirmation_failed")
    if not same_name(bank.name, data.get("name", "")):
        raise ProviderError("account_holder_mismatch")
    tier = str(data.get("tier", ""))
    bank.tier = int(tier) if tier in {"1", "2", "3"} else 0
    bank.pnd = _restriction(data.get("pndstatus"))
    bank.frozen = _restriction(data.get("freezeStatus"))
    bank.available_balance = _amount(data.get("availableBalance"))
    bank.ledger_balance = _amount(data.get("ledgerBalance"))
    bank.maximum_deposit = _amount(data.get("maximumDeposit"))
    bank.maximum_balance = _amount(data.get("maximumBalance"))
    bank.status = ("review" if bank.opening_review_required else
        "active" if str(data.get("status", "")).upper() == "ACTIVE" else "restricted")
    bank.checked_at = timezone.now()
    bank.save()
    Wallet.objects.filter(pk=bank.wallet_id).update(bank_tier=bank.tier,
        pnd_lifted=bank.pnd is False and bank.frozen is False)
    return bank
