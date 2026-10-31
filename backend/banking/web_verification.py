"""Action-scoped form adapter. Authentication, PIN and CSRF live in WhatsApp's shell."""
import base64
from django.utils import timezone
from . import accounts, address, upgrades
from .client import ProviderError, config
from .models import IdentityChallenge, AddressVerification
from .secrets import unseal

# Customer-facing labels. The bank partner is never named on this page: the
# customer is verifying with Zitch. Field keys are the bank contract.
PROFILE = [("lastName", "Surname"), ("otherNames", "First and middle names"), ("phoneNo", "Phone number"),
           ("dateOfBirth", "Date of birth"), ("address", "Home address"), ("ninUserId", "NIMC User ID")]
DETAILS = [("accountName", "Full legal name"), ("phoneNumber", "Phone number"), ("email", "Email"),
    ("idNumber", "ID number"), ("idIssueDate", "ID issue date"), ("idExpiryDate", "ID expiry date (optional for NIN)"),
    ("houseNumber", "House number"), ("streetName", "Street"), ("state", "State"), ("city", "City"),
    ("localGovernment", "Local government area"), ("nearestLandmark", "Nearest landmark")]
DATE_DETAILS = {"idIssueDate", "idExpiryDate"}
OPTIONAL_DETAILS = {"idNumber", "idExpiryDate"}
DOCUMENTS = [("userPhoto", "Passport photo (JPEG or PNG, up to 75 KB)"), ("idCardFront", "ID card, front (up to 75 KB)"),
    ("idCardBack", "ID card, back (up to 75 KB)"), ("utilityBill", "Utility bill (up to 37 KB)"), ("customerSignature", "Signature (up to 500 KB)")]
ADDRESS = [("house_number", "House number"), ("street", "Street"), ("address", "Full home address"),
    ("city", "City"), ("state", "State"), ("lga", "Local government area"), ("landmark", "Nearest landmark")]
ID_TYPES = [("1", "National ID (NIN)"), ("2", "Driver’s licence"), ("3", "Voter’s card"), ("4", "International passport")]

OPERATION_LIMITS = {
    "identity": ("bank_identity_start", 5, 600),
    "otp": ("bank_identity_confirm", 10, 600),
    "upgrade": ("bank_upgrade", 3, 600),
    "address": ("address_start", 2, 3600),
    "address_status": ("address_status", 5, 600),
}


def validate_fields(action, data, files):
    """A browser grant authorizes only these server-defined KYC operations."""
    fields = {
        "identity": {key for key, _ in PROFILE} | {"gender", "kind", "identifier", "method"},
        "otp": {"otp"}, "identity_status": set(),
        "upgrade": {key for key, _ in DETAILS} | {"bvn", "nin", "idType", "pep", "tier"},
        "upgrade_status": set(), "address": {key for key, _ in ADDRESS}, "address_status": set(),
    }
    uploads = {"identity": {"face"}, "upgrade": {key for key, _ in DOCUMENTS}}
    if (action not in fields or set(data) - fields[action] - {"action", "csrfmiddlewaretoken"}
            or set(files) - uploads.get(action, set())):
        raise ValueError("Use the fields and actions shown on this verification page")


def _challenge(user, reference):
    """Read-only view of the started identity check, for choosing what to show."""
    if not reference:
        return None
    row = IdentityChallenge.objects.filter(user=user, environment=config().get("ENVIRONMENT", "sandbox"),
                                           reference=reference).first()
    if not row:
        return None
    live = row.expires_at > timezone.now()
    state = row.state if live or row.verified_at else "expired"
    return {"code_required": live and row.method == "OTP" and row.state == "pending",
            "active": state not in {"complete", "failed", "expired"},
            "state": state, "method": row.method}


def context(user, payload):
    bank = accounts.account_for(user)
    profile = unseal(bank.encrypted_profile) if bank and bank.encrypted_profile else {
        "lastName": user.last_name, "otherNames": user.first_name, "phoneNo": user.phone}
    page_tier = payload.get("tier") if payload.get("tier") in (1, 2, 3) else 1
    account = accounts.account_payload(user)
    # Prefill what Zitch already holds so the upgrade form asks only for what is new.
    known = {"accountName": account.get("account_name") or user.get_full_name(),
             "phoneNumber": user.phone or "", "email": user.email or ""}
    return {"tier": user.tier, "page_tier": page_tier, "bank": account,
        "profile": [{"key": key, "label": label, "value": profile.get(key, "")} for key, label in PROFILE],
        "values": {**{key: profile.get(key, "") or "" for key, _ in PROFILE},
                   "gender": profile.get("gender") if profile.get("gender") in (0, 1) else None},
        "details": [{"key": key, "label": label, "value": known.get(key, ""),
                     "type": "date" if key in DATE_DETAILS else "email" if key == "email" else "tel" if key == "phoneNumber" else "text",
                     "required": key not in OPTIONAL_DETAILS} for key, label in DETAILS],
        "id_types": ID_TYPES,
        "documents": [{"key": key, "label": label} for key, label in DOCUMENTS],
        "address_fields": [{"key": key, "label": label} for key, label in ADDRESS],
        "identity_reference": payload.get("identity_reference", ""),
        "challenge": _challenge(user, payload.get("identity_reference", "")),
        "address_reference": payload.get("address_reference", "")}


def uploaded(files, key, *, missing="Choose a document within the displayed size limit"):
    item = files.get(key)
    if not item:
        raise ValueError(missing)
    if item.size > 2_000_000:
        raise ValueError("Choose a file within the displayed size limit")
    return base64.b64encode(item.read()).decode("ascii")


def submit(user, action, data, files, scope):
    accounts.require_enabled(user)
    if action == "identity":
        profile = {key: data.get(key, "") for key, _ in PROFILE}
        profile["gender"] = int(data.get("gender", "-1"))
        profile["email"] = user.email
        method = data.get("method", "OTP")
        result = accounts.start_identity(user, data.get("kind"), data.get("identifier", ""), profile,
            method=method, image=uploaded(files, "face", missing="Take a selfie to continue") if method == "FACIAL" else "")
        return {**result, "identity_reference": result.get("reference", "")}
    if action in {"otp", "identity_status"}:
        ref = scope.get("identity_reference", "")
        if action == "otp":
            return accounts.confirm_identity(user, ref, data.get("otp", ""))
        row = IdentityChallenge.objects.filter(user=user, environment=config().get("ENVIRONMENT", "sandbox"), reference=ref).first()
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
        return {"success": passed, "message": "Upgrade approved." if passed else "Your upgrade hasn’t been approved yet. Check again later."}
    if action == "address":
        row = address.start(user, {key: data.get(key, "") for key, _ in ADDRESS})
        return {"pending": True, "address_reference": row.reference, "message": "We’re checking your address. Check its status here later."}
    if action == "address_status":
        row = AddressVerification.objects.filter(user=user, reference=scope.get("address_reference", "")).first()
        if not row:
            raise ValueError("Start address verification first")
        address.refresh(row)
        return {"success": row.state == "verified", "message": "Address check: " + row.state.replace("_", " ") + "."}
    raise ValueError("Choose an action from this page")
