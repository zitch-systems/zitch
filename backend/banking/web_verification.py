"""Action-scoped form adapter. Authentication, PIN and CSRF live in WhatsApp's shell."""
import base64
import json
from pathlib import Path

from django.utils import timezone
from . import accounts, address, upgrades
from .client import ProviderError, config
from .models import IdentityChallenge, AddressVerification
from cryptography.fernet import InvalidToken

from .secrets import seal, unseal

# Customer-facing labels. The bank partner is never named on this page: the
# customer is verifying with Zitch. Field keys are the bank contract.
PROFILE = [("lastName", "Surname"), ("otherNames", "First and middle names"), ("phoneNo", "Phone number"),
           ("dateOfBirth", "Date of birth"), ("address", "Home address")]
DETAILS = [("accountName", "Full legal name"), ("phoneNumber", "Phone number"), ("email", "Email"),
    ("idIssueDate", "ID issue date"), ("idExpiryDate", "ID expiry date"),
    ("houseNumber", "House number"), ("streetName", "Street"), ("state", "State"), ("city", "City"),
    ("localGovernment", "Local government area"), ("nearestLandmark", "Nearest landmark")]
DATE_DETAILS = {"idIssueDate", "idExpiryDate"}
OPTIONAL_DETAILS = {"idIssueDate", "idExpiryDate"}  # Not asked for a NIN.
# userPhoto is taken with the camera on the page (face capture), never uploaded.
DOCUMENTS = [("userPhoto", "Your face"), ("idCardFront", "ID front"),
    ("idCardBack", "ID back"), ("utilityBill", "Utility bill"), ("customerSignature", "Signature")]
NO_BACK_ID_TYPES = {"1", "4"}  # A NIN slip and a passport have no back side.
ADDRESS = [("house_number", "House number"), ("street", "Street"), ("address", "Full home address"),
    ("city", "City"), ("state", "State"), ("lga", "Local government area"), ("landmark", "Nearest landmark")]
ID_TYPES = [("1", "National ID (NIN)"), ("2", "Driver’s licence"), ("3", "Voter’s card"), ("4", "International passport")]

FACE_REQUEST_SECONDS = 15 * 60

# States and their local government areas, for the State and LGA pickers.
NIGERIA_LGAS = json.loads((Path(__file__).with_name("nigeria_lgas.json")).read_text(encoding="utf-8"))

OPERATION_LIMITS = {
    "identity": ("bank_identity_start", 5, 600),
    "face": ("bank_identity_start", 5, 600),
    "otp": ("bank_identity_confirm", 10, 600),
    "resend": ("bank_identity_start", 5, 600),
    "upgrade": ("bank_upgrade", 3, 600),
    "address": ("address_start", 2, 3600),
    "address_status": ("address_status", 5, 600),
}


def validate_fields(action, data, files):
    """A browser grant authorizes only these server-defined KYC operations."""
    fields = {
        "identity": {key for key, _ in PROFILE} | {"gender", "kind", "identifier", "method"},
        "otp": {"otp"}, "identity_status": set(), "resend": set(), "face": set(),
        "upgrade": {key for key, _ in DETAILS} | {"bvn", "nin", "idType", "pep", "tier"},
        "upgrade_status": set(), "address": {key for key, _ in ADDRESS}, "address_status": set(),
    }
    uploads = {"identity": {"face"}, "face": {"face"}, "upgrade": {key for key, _ in DOCUMENTS}}
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
        # BVN is the default ID; a code is the default way to confirm it.
        "values": {**{key: profile.get(key, "") or "" for key, _ in PROFILE},
                   "gender": profile.get("gender") if profile.get("gender") in (0, 1) else None,
                   "kind": "bvn", "method": "OTP"},
        "details": [{"key": key, "label": label, "value": known.get(key, ""),
                     "type": "date" if key in DATE_DETAILS else "email" if key == "email" else "tel" if key == "phoneNumber" else "text",
                     "required": key not in OPTIONAL_DETAILS} for key, label in DETAILS],
        "id_types": ID_TYPES,
        "documents": [{"key": key, "label": label} for key, label in DOCUMENTS],
        "address_fields": [{"key": key, "label": label} for key, label in ADDRESS],
        "identity_reference": payload.get("identity_reference", ""),
        "challenge": _challenge(user, payload.get("identity_reference", "")),
        # Details confirmed for face verification; the photo is taken on its own page.
        "face_pending": bool(payload.get("face_request")),
        "address_reference": payload.get("address_reference", ""),
        "address_verified": address.verification_status(user)["address_verified"],
        "nigeria_lgas": NIGERIA_LGAS, "states": list(NIGERIA_LGAS)}


def entered(data):
    """What the customer typed on the details form, to show it again after an
    error. The ID number itself is never echoed back."""
    values = {key: str(data.get(key, ""))[:100] for key, _ in PROFILE}
    gender = data.get("gender")
    values["gender"] = int(gender) if gender in ("0", "1") else None
    values["kind"] = data.get("kind") if data.get("kind") in ("bvn", "nin") else "bvn"
    values["method"] = data.get("method") if data.get("method") in ("OTP", "FACIAL") else "OTP"
    return values


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
        # Anything but male (0) or female (1) is refused as "Select your gender".
        profile["gender"] = int(data["gender"]) if data.get("gender") in ("0", "1") else -1
        profile["email"] = user.email
        method = data.get("method", "OTP")
        kind, identifier = data.get("kind"), data.get("identifier", "")
        if method == "FACIAL" and not files.get("face"):
            # Confirm everything now, contact the bank only once the face page
            # sends the photo. The identity number is held encrypted, for this
            # 15-minute link only, and erased when the photo is submitted.
            accounts.check_identity_request(user, kind, identifier, profile, method=method)
            return {"face_request": seal({"kind": kind, "identifier": identifier, "profile": profile}),
                    "message": "Your details are confirmed. Now open face verification."}
        result = accounts.start_identity(user, kind, identifier, profile,
            method=method, image=uploaded(files, "face", missing="Take your photo to continue") if method == "FACIAL" else "")
        return {**result, "identity_reference": result.get("reference", ""), "face_request": ""}
    if action == "face":
        sealed = scope.get("face_request", "")
        try:
            # Usable only within the 15-minute life of the verification link.
            pending = unseal(sealed, ttl=FACE_REQUEST_SECONDS) if sealed else None
        except InvalidToken:
            pending = None
        if not isinstance(pending, dict):
            raise ValueError("Enter your details first, then open face verification")
        result = accounts.start_identity(user, pending.get("kind"), pending.get("identifier", ""), pending.get("profile") or {},
            method="FACIAL", image=uploaded(files, "face", missing="Take your photo to continue"))
        return {**result, "identity_reference": result.get("reference", ""), "face_request": ""}
    if action == "resend":
        result = accounts.resend_code(user, scope.get("identity_reference", ""))
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
        skip = {"idCardBack"} if profile.get("idType") in NO_BACK_ID_TYPES else set()
        profile.update({key: uploaded(files, key, missing="Verify your face to continue" if key == "userPhoto"
                        else "Choose a document within the displayed size limit") for key, _ in DOCUMENTS if key not in skip})
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
