"""Validate the documented bank contract before transmitting personal data."""
import base64
import binascii
from datetime import datetime
from io import BytesIO

from PIL import Image

from .client import phone


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
    if data["gender"] not in (0, 1) or isinstance(data["gender"], bool):
        raise ValueError("Select the gender required by the bank")
    datetime.strptime(data["dateOfBirth"], "%d/%m/%Y")
    result = {k: data[k] for k in required}
    result["phoneNo"] = phone(data["phoneNo"])
    if len(str(result["address"])) > 100:
        raise ValueError("Address must be at most 100 characters")
    for key in ("email", "accountName", "placeOfBirth", "nextOfKinName", "nextOfKinPhoneNo", "ninUserId"):
        if data.get(key):
            result[key] = str(data[key])
    # BVN/NIN belong to the verified challenge. A client cannot replace them here.
    return result


def upgrade_profile(tier, data):
    if not isinstance(data, dict):
        raise ValueError("Provide account upgrade fields")
    import re
    if str(tier) not in {"2", "3"}:
        raise ValueError("Choose Tier 2 or Tier 3")
    required = ("bvn", "nin", "accountName", "phoneNumber", "email", "idType", "idIssueDate",
                "houseNumber", "streetName", "state", "city", "localGovernment", "pep", "nearestLandmark")
    if any(data.get(k) in (None, "") for k in required):
        raise ValueError("Complete all required bank KYC details")
    for key in ("bvn", "nin"):
        if not re.fullmatch(r"[0-9]{11}", str(data[key])):
            raise ValueError("Both BVN and NIN must contain eleven digits")
    if str(data["idType"]) not in {"1", "2", "3", "4"} or data["pep"] not in {"YES", "NO"}:
        raise ValueError("Invalid ID type or PEP declaration")
    datetime.strptime(data["idIssueDate"], "%Y-%m-%d")
    if str(data["idType"]) != "1" and not data.get("idExpiryDate"):
        raise ValueError("ID expiry date is required for this document")
    if str(tier) == "3" and not data.get("idNumber"):
        raise ValueError("ID number is required for Tier 3")
    if data.get("idExpiryDate"):
        datetime.strptime(data["idExpiryDate"], "%Y-%m-%d")
    result = {k: data[k] for k in required}
    result.update(tier=str(tier), phoneNumber=phone(data["phoneNumber"]))
    for key in ("idNumber", "idExpiryDate", "placeOfBirth"):
        if data.get(key):
            result[key] = data[key]
    for key, chars in (("userPhoto", 100000), ("idCardFront", 100000), ("idCardBack", 100000), ("utilityBill", 50000)):
        result[key] = base64_file(data.get(key), maximum_chars=chars, image_only=key == "userPhoto")
    result["customerSignature"] = base64_file(data.get("customerSignature"), maximum_bytes=500 * 1024)
    if str(tier) == "3":
        result["proofOfAddressVerification"] = base64_file(data.get("proofOfAddressVerification"), maximum_chars=50000)
    return result
