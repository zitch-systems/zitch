"""Payment QR codes — decoding the image, and reading what is inside it.

Two separable jobs, kept separate because they fail differently:

  * `decode_image` turns photo bytes into the string a QR encodes. It fails when
    the photo is blurry, cropped or dark — a user problem with a user fix.
  * `parse_payment` turns that string into a payment intent. It fails when the code
    is a website, a WiFi credential or a scheme we do not implement — not the
    customer's fault and not fixable by taking a better photo.

Telling those apart is most of the value here: "hold the camera steadier" and "that
QR isn't a payment code" are different sentences, and a scanner that says the wrong
one sends people round in circles.

WHAT WE READ. Nigerian bank QRs are EMVCo TLV (the NQR scheme rides on it), the
same layout used across most of the world. We also read the two informal shapes that
show up constantly in practice: a Zitch pay link, and a bare account or phone number
somebody pasted into a generator.

WHAT WE DO NOT DO. Parsing a merchant QR is not the same as being able to PAY it.
NQR settlement runs through NIBSS and needs scheme membership; nothing here moves
money on its own. Where a code resolves to an ordinary NUBAN we can pay it as an
ordinary transfer, and where it does not we say so rather than implying we tried.
"""
import logging
import re

log = logging.getLogger("zitch")

#: Decoded strings above this are not payment codes — they are payloads someone is
#: probing us with. EMVCo caps a QR at 512 bytes; this leaves generous headroom.
MAX_PAYLOAD = 4096

#: EMVCo primitive tags we care about.
_TAG_PAYLOAD_FORMAT = "00"
_TAG_INITIATION = "01"
_TAG_CURRENCY = "53"
_TAG_AMOUNT = "54"
_TAG_COUNTRY = "58"
_TAG_MERCHANT_NAME = "59"
_TAG_MERCHANT_CITY = "60"
_TAG_ADDITIONAL = "62"
_TAG_CRC = "63"

#: Merchant-account-information templates. EMVCo reserves 02..51; each holds a
#: nested TLV whose tag 00 names the scheme.
_MERCHANT_TEMPLATES = tuple(f"{n:02d}" for n in range(2, 52))

#: ISO 4217 numeric -> code, for the currencies a Nigerian customer can meet on a
#: QR. An unlisted currency is reported by its number rather than guessed at.
_CURRENCIES = {"566": "NGN", "840": "USD", "826": "GBP", "978": "EUR"}


def decode_image(data: bytes):
    """Photo bytes -> the string the QR encodes, or None.

    None means "no readable QR in this image" and nothing more. It is deliberately
    not an exception: a customer photographing a poster in bad light is the normal
    case, not an error condition.
    """
    if not data:
        return None
    try:
        import io

        import zxingcpp
        from PIL import Image
    except ImportError:  # pragma: no cover - dependency missing on this deploy
        log.warning("qr_decoder_unavailable — install zxing-cpp to read payment QRs")
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            # Greyscale: the decoder wants luminance, and converting here means a
            # PNG with an alpha channel (every screenshot) doesn't fail on format.
            results = zxingcpp.read_barcodes(img.convert("L"))
    except Exception:  # noqa: BLE001 — a corrupt upload must not raise into the channel
        log.info("qr_decode_failed — unreadable image")
        return None
    for res in results or []:
        text = (res.text or "").strip()
        if text and len(text) <= MAX_PAYLOAD:
            return text
    return None


def _tlv(payload: str) -> dict:
    """Parse EMVCo TLV into {tag: value}. Returns {} on anything malformed.

    Strict on purpose. A length that overruns the buffer, or a tag that isn't two
    digits, means this is not an EMVCo payload — and guessing past the damage is how
    a parser starts inventing account numbers out of a WiFi QR.
    """
    out, i, n = {}, 0, len(payload)
    while i + 4 <= n:
        tag, raw_len = payload[i:i + 2], payload[i + 2:i + 4]
        if not (tag.isdigit() and raw_len.isdigit()):
            return {}
        length = int(raw_len)
        start, end = i + 4, i + 4 + length
        if end > n:
            return {}
        out[tag] = payload[start:end]
        i = end
    return out if i == n else {}


def _crc_ok(payload: str) -> bool:
    """EMVCo CRC-16/CCITT-FALSE over everything up to and including the '6304' tag.

    Checked because a QR photographed off a screen or a crumpled poster can decode
    to *almost* the right string, and one flipped digit inside an account number is
    both undetectable by eye and the worst possible outcome here. A failed CRC means
    we refuse to read the payment rather than pay a number nobody printed.
    """
    marker = payload.rfind(f"{_TAG_CRC}04")
    if marker < 0 or len(payload) - marker != 8:
        return False
    body, given = payload[:marker + 4], payload[marker + 4:]
    crc = 0xFFFF
    for byte in body.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}" == given.upper()


def _merchant_accounts(tags: dict) -> list:
    """The nested merchant-account templates, as [{scheme, values{}}]."""
    found = []
    for tag in _MERCHANT_TEMPLATES:
        raw = tags.get(tag)
        if not raw:
            continue
        nested = _tlv(raw)
        found.append({"tag": tag,
                      "scheme": (nested.get("00") or "").strip(),
                      "values": {k: v for k, v in nested.items() if k != "00"}})
    return found


def _nuban(values) -> str:
    """The first value that looks like a Nigerian NUBAN (exactly 10 digits).

    Length-exact rather than "contains ten digits in a row": a merchant id, a
    terminal id and a phone number all carry long digit runs, and slicing one out of
    the middle of a longer field would produce a plausible account number that
    belongs to somebody else.
    """
    for v in values:
        digits = (v or "").strip()
        if len(digits) == 10 and digits.isdigit():
            return digits
    return ""


def parse_payment(text: str) -> dict:
    """A decoded QR string -> a payment intent.

    Always returns a dict with a `kind`:
      "emv"     — a standards-compliant payment QR (NQR and friends)
      "zitch"   — one of our own pay links
      "account" — a bare NUBAN or phone number
      "other"   — decoded fine, but it is not a payment code

    `payable` says whether we can actually send money to it as an ordinary transfer.
    A merchant QR routinely parses perfectly and is still not payable by us, and the
    caller must be able to tell those apart to say something honest.
    """
    raw = (text or "").strip()
    if not raw or len(raw) > MAX_PAYLOAD:
        return {"kind": "other", "payable": False, "raw": ""}

    if raw.startswith(("00020", "0002")) and _TAG_CRC in raw:
        emv = _parse_emv(raw)
        if emv:
            return emv

    link = re.search(r"[?&](?:account|acct|phone|identifier)=(\d{10,11})", raw, re.I)
    if link:
        return {"kind": "zitch", "payable": True, "account": link.group(1),
                "merchant_name": "", "amount": None, "currency": "NGN", "raw": raw}

    digits = re.sub(r"\D", "", raw)
    # Only when the WHOLE string is that number. A URL with ten digits somewhere in
    # its path is not an account number, and treating it as one would offer to send
    # money to a fragment of a tracking id.
    if digits == raw and len(digits) in (10, 11):
        return {"kind": "account", "payable": True, "account": digits,
                "merchant_name": "", "amount": None, "currency": "NGN", "raw": raw}

    return {"kind": "other", "payable": False, "raw": raw}


def _parse_emv(raw: str):
    """EMVCo payload -> intent, or None when it is not really EMVCo."""
    tags = _tlv(raw)
    if not tags or _TAG_PAYLOAD_FORMAT not in tags:
        return None
    if not _crc_ok(raw):
        # Decoded cleanly but the checksum disagrees: the payload is damaged, and
        # every field in it — including the account number — is suspect.
        log.info("qr_emv_crc_mismatch")
        return {"kind": "emv", "payable": False, "corrupt": True, "raw": raw}

    accounts = _merchant_accounts(tags)
    account = ""
    scheme = ""
    for acc in accounts:
        account = _nuban(acc["values"].values())
        if account:
            scheme = acc["scheme"]
            break
    if not scheme and accounts:
        scheme = accounts[0]["scheme"]

    amount = None
    raw_amount = (tags.get(_TAG_AMOUNT) or "").strip()
    if raw_amount:
        try:
            from decimal import Decimal
            parsed = Decimal(raw_amount)
            # A static merchant QR carries no amount; a zero or negative one is a
            # malformed dynamic code, and treating it as "pay ₦0" is worse than
            # asking the customer what to send.
            amount = parsed if parsed > 0 else None
        except Exception:  # noqa: BLE001
            amount = None

    additional = _tlv(tags.get(_TAG_ADDITIONAL) or "")
    currency_num = (tags.get(_TAG_CURRENCY) or "").strip()
    return {
        "kind": "emv",
        # Only a code that names an ordinary NUBAN can be paid by an ordinary
        # transfer. A merchant id settles through the scheme, which we are not a
        # member of, so it parses and reports but never claims to be payable.
        "payable": bool(account),
        "corrupt": False,
        "account": account,
        "scheme": scheme,
        "merchant_name": (tags.get(_TAG_MERCHANT_NAME) or "").strip(),
        "merchant_city": (tags.get(_TAG_MERCHANT_CITY) or "").strip(),
        "amount": amount,
        "currency": _CURRENCIES.get(currency_num, currency_num or "NGN"),
        "country": (tags.get(_TAG_COUNTRY) or "").strip(),
        "reference": (additional.get("05") or additional.get("01") or "").strip(),
        # 12 = dynamic (one payment, usually amount-bearing); 11 = static poster.
        "dynamic": (tags.get(_TAG_INITIATION) or "").strip() == "12",
        "raw": raw,
    }
