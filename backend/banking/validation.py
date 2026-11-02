"""Validate the documented bank contract before transmitting personal data."""
import base64
import binascii
from datetime import datetime
from io import BytesIO

from PIL import Image

from .client import phone


def _date(value, fmt, message, *, past=False):
    """Parse a customer-entered date with a readable error, never strptime's."""
    try:
        parsed = datetime.strptime(str(value).strip(), fmt).date()
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if past and (parsed >= datetime.now().date() or parsed.year < 1900):
        raise ValueError(message)
    return parsed


def base64_file(value, *, maximum_bytes=None, maximum_chars=None, image_only=False):
    if not isinstance(value, str) or not value:
        raise ValueError("A document is required")
    if value.startswith("data:"):
        value = value.partition(",")[2]
    if maximum_chars and len(value) > maximum_chars:
        raise ValueError("Document exceeds the bank's size limit")
    if maximum_bytes and len(value) > (maximum_bytes + 2) // 3 * 4:
        raise ValueError("Document exceeds the bank's size limit")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid document encoding") from None
    if not raw or (maximum_bytes and len(raw) > maximum_bytes):
        raise ValueError("Document exceeds the bank's size limit")
    if image_only:
        try:
            with Image.open(BytesIO(raw)) as im:
                if im.format not in {"JPEG", "PNG"} or im.width * im.height > 20000000:
                    raise ValueError("Use a JPEG or PNG photo")
                im.verify()
        except (OSError, Image.DecompressionBombError):
            raise ValueError("Use a valid JPEG or PNG photo") from None
    elif not raw.startswith(b"%PDF-"):
        base64_file(value, maximum_chars=maximum_chars, maximum_bytes=maximum_bytes, image_only=True)
    return value


def wallet_profile(data):
    if not isinstance(data, dict):
        raise ValueError("Provide account profile fields")
    required = ("lastName", "otherNames", "phoneNo", "gender", "dateOfBirth", "address")
    if any(data.get(k) in (None, "") for k in required):
        raise ValueError("Name, phone, gender, date of birth and residential address are required")
    for key, limit in (("lastName", 80), ("otherNames", 80), ("dateOfBirth", 10), ("address", 100)):
        if not isinstance(data[key], str) or not data[key].strip() or len(data[key]) > limit:
            raise ValueError("Provide valid name, date of birth and address fields within the bank limits")
    if len(data["lastName"] + " " + data["otherNames"]) > 160:
        raise ValueError("The full account name exceeds the bank limit")
    if data["gender"] not in (0, 1) or isinstance(data["gender"], bool):
        raise ValueError("Select your gender")
    _date(data["dateOfBirth"], "%d/%m/%Y", "Enter a valid date of birth as DD/MM/YYYY", past=True)
    result = {k: data[k] for k in required}
    result["phoneNo"] = phone(data["phoneNo"])
    if len(str(result["address"])) > 100:
        raise ValueError("Address must be at most 100 characters")
    # Only the bank's mandatory fields are sent; its optional ones are never collected.
    # BVN/NIN belong to the verified challenge. A client cannot replace them here.
    return result


def fit_photo(value, *, maximum_chars):
    """Return a JPEG of the customer's face that fits the bank's size limit.

    Phone cameras produce photos far larger than the bank accepts, so the
    in-app face capture is re-encoded here rather than refused.
    """
    if isinstance(value, str) and value.startswith("data:"):
        value = value.partition(",")[2]
    if not isinstance(value, str) or not value:
        raise ValueError("Verify your face to continue")
    if len(value) <= maximum_chars:
        return base64_file(value, maximum_chars=maximum_chars, image_only=True)
    if len(value) > 8 * 1024 * 1024:
        raise ValueError("Your photo is too large. Please retake it")
    base64_file(value, maximum_bytes=6 * 1024 * 1024, image_only=True)
    with Image.open(BytesIO(base64.b64decode(value))) as im:
        im = im.convert("RGB")
        for side, quality in ((640, 80), (480, 75), (400, 65), (320, 60), (240, 55)):
            copy = im.copy()
            copy.thumbnail((side, side))
            out = BytesIO()
            copy.save(out, format="JPEG", quality=quality, optimize=True)
            encoded = base64.b64encode(out.getvalue()).decode()
            if len(encoded) <= maximum_chars:
                return encoded
    raise ValueError("Your photo is too large. Please retake it")


def upgrade_profile(tier, data):
    """The bank's mandatory upgrade fields. The NIN is the ID: its number is the
    ID number, the NIN slip photo is the ID image, and a NIN has no dates."""
    if not isinstance(data, dict):
        raise ValueError("Provide account upgrade fields")
    import re
    if str(tier) not in {"2", "3"}:
        raise ValueError("Choose Tier 2 or Tier 3")
    required = ["bvn", "nin", "accountName", "phoneNumber", "email",
                "houseNumber", "streetName", "state", "city", "localGovernment", "pep", "nearestLandmark"]
    if any(data.get(k) in (None, "") for k in required):
        raise ValueError("Complete all required bank KYC details")
    if not re.fullmatch(r"[0-9]{11}", str(data["nin"])):
        raise ValueError("Enter your eleven-digit NIN")
    if not re.fullmatch(r"[0-9]{11}", str(data["bvn"])):
        raise ValueError("Your BVN is missing; contact support")
    if data["pep"] not in {"YES", "NO"}:
        raise ValueError("Answer the politically exposed person question")
    result = {k: data[k] for k in required}
    result.update(tier=str(tier), phoneNumber=phone(data["phoneNumber"]), idType="1", idNumber=str(data["nin"]))
    result["userPhoto"] = fit_photo(data.get("userPhoto"), maximum_chars=100000)
    result["idCardFront"] = base64_file(data.get("idCardFront"), maximum_chars=100000, image_only=True)
    result["utilityBill"] = base64_file(data.get("utilityBill"), maximum_chars=50000)
    result["customerSignature"] = base64_file(data.get("customerSignature"), maximum_bytes=500 * 1024, image_only=True)
    if str(tier) == "3":
        result["proofOfAddressVerification"] = base64_file(data.get("proofOfAddressVerification"), maximum_chars=50000)
    return result
