"""Render a branded transaction receipt as a JPEG (for WhatsApp).

The chat sends this image as a downloadable document so the user gets an actual
receipt file — not the biller's logo. Text is drawn with a bundled DejaVu font so
rendering is identical on any host (no reliance on system fonts). No colour-emoji
glyphs are drawn (the check mark is a vector), so nothing renders as tofu.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
_GREEN = (18, 140, 74)
_INK = (17, 24, 28)
_MUTED = (110, 122, 128)
_LINE = (228, 232, 234)
_BG = (255, 255, 255)

# The Naira sign isn't in every font; DejaVu Sans has it, but fall back to "NGN "
# if a stripped build somehow lacks the glyph so an amount never renders as tofu.
_NAIRA = "₦"


def _font(name: str, size: int):
    try:
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), size)
    except OSError:
        return ImageFont.load_default()


def _has_glyph(font, ch: str) -> bool:
    try:
        return font.getmask(ch).getbbox() is not None
    except Exception:
        return False


def render_receipt(title: str, rows: list, ref: str, *, status: str = "Successful") -> bytes:
    """Return JPEG bytes for a receipt titled `title` with `rows` = [(label, value)],
    a status badge, and `ref`/timestamp in the footer."""
    from django.utils import timezone

    W = 820
    pad = 48
    reg = _font("DejaVuSans.ttf", 26)
    bold = _font("DejaVuSans-Bold.ttf", 28)
    big = _font("DejaVuSans-Bold.ttf", 40)
    small = _font("DejaVuSans.ttf", 22)

    naira_ok = _has_glyph(reg, _NAIRA)

    def money_safe(v: str) -> str:
        return v if naira_ok else v.replace(_NAIRA, "NGN ")

    row_h = 58
    header_h = 132
    body_top = header_h + 40
    footer_h = 110
    H = body_top + row_h * len(rows) + 70 + footer_h

    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)

    # Header band with the wordmark.
    d.rectangle([0, 0, W, header_h], fill=_GREEN)
    d.text((pad, 34), "zitch", font=big, fill=(255, 255, 255))
    d.text((pad, 88), "Transaction Receipt", font=small, fill=(230, 245, 236))

    # Title + success badge.
    d.text((pad, body_top - 4), money_safe(title), font=bold, fill=_INK)
    badge = f"✓ {status}"
    bw = d.textlength(badge, font=small)
    d.rounded_rectangle([W - pad - bw - 28, body_top - 2, W - pad, body_top + 34],
                        radius=17, fill=(232, 246, 238))
    d.text((W - pad - bw - 14, body_top + 2), badge, font=small, fill=_GREEN)

    # Rows.
    y = body_top + 62
    for label, value in rows:
        d.text((pad, y), str(label), font=small, fill=_MUTED)
        val = money_safe(str(value))
        vw = d.textlength(val, font=reg)
        # Wrap a long value under the label instead of overflowing.
        if vw > W - 2 * pad - d.textlength(str(label), font=small) - 24:
            d.text((pad, y + 26), val, font=reg, fill=_INK)
            y += row_h + 22
        else:
            d.text((W - pad - vw, y - 2), val, font=reg, fill=_INK)
            y += row_h
        d.line([pad, y - 12, W - pad, y - 12], fill=_LINE, width=1)

    # Footer.
    fy = H - footer_h + 16
    d.line([pad, fy - 8, W - pad, fy - 8], fill=_LINE, width=1)
    d.text((pad, fy), f"Ref: {ref}", font=small, fill=_MUTED)
    d.text((pad, fy + 30), timezone.localtime().strftime("%d %b %Y, %I:%M %p"),
           font=small, fill=_MUTED)
    tag = "Powered by Zitch • zitch.ng"
    d.text((W - pad - d.textlength(tag, font=small), fy + 30), tag, font=small, fill=_GREEN)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
