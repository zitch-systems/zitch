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


# Customers may upload up to 5 MB in any common photo format or a PDF. The bank
# accepts far smaller files, so documents are converted and shrunk here.
UPLOAD_MAX_BYTES = 5 * 1024 * 1024


def _decoded_upload(value, missing):
    if isinstance(value, str) and value.startswith("data:"):
        value = value.partition(",")[2]
    if not isinstance(value, str) or not value:
        raise ValueError(missing)
    if len(value) > (UPLOAD_MAX_BYTES + 2) // 3 * 4:
        raise ValueError("Choose a file smaller than 5 MB")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("This file could not be read. Please try another") from None
    if not raw:
        raise ValueError(missing)
    return value, raw


def _open_image(raw):
    """Any photo format Pillow can read (JPEG, PNG, WEBP, HEIC, BMP, GIF, TIFF), or a PDF's first page."""
    if raw.startswith(b"%PDF-"):
        import pypdfium2
        try:
            pdf = pypdfium2.PdfDocument(raw)
            try:
                if len(pdf) < 1:
                    raise ValueError("This PDF has no pages")
                return pdf[0].render(scale=1.5).to_pil()
            finally:
                pdf.close()
        except pypdfium2.PdfiumError:
            raise ValueError("This PDF could not be read. Please try another") from None
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:  # HEIC support is optional; other formats still work.
        pass
    try:
        image = Image.open(BytesIO(raw))
        if image.width * image.height > 40000000:
            raise ValueError("This image is too large. Please try another")
        image.load()
        return image
    except (OSError, Image.DecompressionBombError):
        raise ValueError("Use a photo (JPEG, PNG, HEIC…) or a PDF") from None


def fit_document(value, *, maximum_chars, missing="A document is required", allow_pdf=False):
    """Return the upload as the bank accepts it: within maximum_chars of base64,
    as the original JPEG/PNG (or PDF when allowed) if it already fits, otherwise
    converted to a JPEG small enough to fit."""
    value, raw = _decoded_upload(value, missing)
    if len(value) <= maximum_chars:
        if allow_pdf and raw.startswith(b"%PDF-"):
            return value
        try:
            with Image.open(BytesIO(raw)) as im:
                if im.format in {"JPEG", "PNG"}:
                    im.verify()
                    return value
        except (OSError, Image.DecompressionBombError):
            pass
    image = _open_image(raw)
    if image.mode not in {"RGB", "L"}:
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.convert("RGBA").split()[-1])
        image = background
    image = image.convert("RGB")
    for side, quality in ((1600, 85), (1280, 80), (1024, 75), (800, 70), (640, 65), (480, 60), (360, 55), (280, 50)):
        copy = image.copy()
        copy.thumbnail((side, side))
        out = BytesIO()
        copy.save(out, format="JPEG", quality=quality, optimize=True)
        encoded = base64.b64encode(out.getvalue()).decode()
        if len(encoded) <= maximum_chars:
            return encoded
    raise ValueError("This file could not be made small enough. Please try a clearer, simpler photo")


def fit_photo(value, *, maximum_chars):
    """A JPEG of the customer's face within the bank's size limit."""
    return fit_document(value, maximum_chars=maximum_chars, missing="Verify your face to continue")


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
    result["idCardFront"] = fit_document(data.get("idCardFront"), maximum_chars=100000, missing="Upload your NIN slip")
    result["utilityBill"] = fit_document(data.get("utilityBill"), maximum_chars=50000, missing="Upload a utility bill",
                                         allow_pdf=True)
    result["customerSignature"] = fit_document(data.get("customerSignature"), maximum_chars=(500 * 1024 + 2) // 3 * 4,
                                               missing="Upload your signature")
    if str(tier) == "3":
        result["proofOfAddressVerification"] = base64_file(data.get("proofOfAddressVerification"), maximum_chars=50000)
    return result
