"""Action-scoped form adapter. Authentication, PIN and CSRF live in WhatsApp's shell."""
import base64
from django.utils import timezone
from . import accounts, address, upgrades
from .client import ProviderError
from .models import IdentityChallenge, AddressVerification
from .secrets import unseal

PROFILE = [("lastName", "Surname"), ("otherNames", "First and middle names"), ("phoneNo", "Phone number"),
           ("dateOfBirth", "Date of birth (DD/MM/YYYY)"), ("address", "Residential address"), ("ninUserId", "NIN user ID (NIN-only account)")]
DETAILS = [("accountName", "Full legal name"), ("phoneNumber", "Phone number"), ("email", "Email"),
    ("idNumber", "ID number"), ("idIssueDate", "ID issue date (YYYY-MM-DD)"), ("idExpiryDate", "ID expiry date (if applicable)"),
    ("houseNumber", "House number"), ("streetName", "Street"), ("state", "State"), ("city", "City"),
    ("localGovernment", "Local government area"), ("nearestLandmark", "Nearest landmark")]
DOCUMENTS = [("userPhoto", "Passport photograph (JPEG/PNG, 75 KB)"), ("idCardFront", "Identity card front (75 KB)"),
    ("idCardBack", "Identity card back (75 KB)"), ("utilityBill", "Utility bill (37 KB)"), ("customerSignature", "Signature (500 KB)")]
ADDRESS = [("house_number", "House number"), ("street", "Street"), ("address", "Full residential address"),
    ("city", "City"), ("state", "State"), ("lga", "Local government area"), ("landmark", "Nearest landmark")]


def context(user, payload):
    bank = accounts.account_for(user)
    profile = unseal(bank.encrypted_profile) if bank and bank.encrypted_profile else {
        "lastName": user.last_name, "otherNames": user.first_name, "phoneNo": user.phone}
    return {"tier": user.tier, "bank": accounts.account_payload(user),
        "profile": [{"key": key, "label": label, "value": profile.get(key, "")} for key, label in PROFILE],
        "details": [{"key": key, "label": label} for key, label in DETAILS],
        "documents": [{"key": key, "label": label} for key, label in DOCUMENTS],
        "address_fields": [{"key": key, "label": label} for key, label in ADDRESS],
        "identity_reference": payload.get("identity_reference", ""),
        "address_reference": payload.get("address_reference", "")}


def uploaded(files, key):
    item = files.get(key)
    if not item or item.size > 2_000_000:
        raise ValueError("Choose a document within the displayed size limit")
    return base64.b64encode(item.read()).decode("ascii")


def submit(user, action, data, files, scope):
    accounts.require_enabled(user)
    if action == "identity":
        profile = {key: data.get(key, "") for key, _ in PROFILE}
        profile["gender"] = int(data.get("gender", "-1"))
        profile["email"] = user.email
        method = data.get("method", "OTP")
        result = accounts.start_identity(user, data.get("kind"), data.get("identifier", ""), profile,
            method=method, image=uploaded(files, "face") if method == "FACIAL" else "")
        return {**result, "identity_reference": result.get("reference", "")}
    if action in {"otp", "identity_status"}:
        ref = scope.get("identity_reference", "")
        if action == "otp":
            return accounts.confirm_identity(user, ref, data.get("otp", ""))
        row = IdentityChallenge.objects.filter(user=user, reference=ref).first()
        if not row:
            raise ValueError("Start an identity verification first")
        if row.state in {"verified", "opening"} and row.verified_at:
            return accounts.complete_account(row)
        return accounts.challenge_payload(row)
    if action == "upgrade":
        profile = {key: data.get(key, "") for key, _ in DETAILS}
        profile.update({key: data.get(key, "") for key in ("bvn", "nin", "idType", "pep")})
        profile.update({key: uploaded(files, key) for key, _ in DOCUMENTS})
        return upgrades.submit_upgrade(user, data.get("tier"), profile, address_reference=scope.get("address_reference", ""))
    if action == "upgrade_status":
        bank = accounts.account_for(user)
        if not bank:
            raise ValueError("Complete account setup first")
        passed = upgrades.refresh_upgrade(bank)
        return {"success": passed, "message": "Upgrade approved." if passed else "The bank has not approved this upgrade yet."}
    if action == "address":
        row = address.start(user, {key: data.get(key, "") for key, _ in ADDRESS})
        return {"pending": True, "address_reference": row.reference, "message": "Address verification is awaiting a confirmed report."}
    if action == "address_status":
        row = AddressVerification.objects.filter(user=user, reference=scope.get("address_reference", "")).first()
        if not row:
            raise ValueError("Start address verification first")
        address.refresh(row)
        return {"success": row.state == "verified", "message": "Address verification status: " + row.state}
    raise ValueError("Choose an action from this page")
