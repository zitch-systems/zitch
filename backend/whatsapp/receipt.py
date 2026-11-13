"""Render a branded transaction receipt as a JPEG (for WhatsApp), and the
transaction-history statement as a PDF.

The receipt is the document a customer forwards as proof of payment, so it is
laid out the way Nigerian customers already read one: the amount large at the
top, the outcome in a coloured pill under it, then who was paid, who paid and
the reference, on a white ticket under a Zitch header. It is set in Manrope, the
app's own typeface (lib/theme `font`), so the WhatsApp receipt and the app's
read as the same company's document.

Everything is laid out in *design units*, an 820-unit-wide sheet, and rendered
through `SCALE`. A receipt is screenshotted, zoomed and forwarded, so it is
rasterised well above display size: at SCALE the sheet is ~2200px wide and the
type stays crisp when someone pinches in. Change SCALE alone to move the output
resolution; no coordinate in this file needs to know.

No glyph is drawn that the bundled fonts lack. Manrope has no naira sign, check
or cross, so the naira sign is built from Manrope's own N (its weight then
matches the digits beside it) and the status marks are vectors. Nothing renders
as tofu.
"""
import io
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

_ASSETS = os.path.join(os.path.dirname(__file__), "assets")
_FONT_DIR = os.path.join(_ASSETS, "fonts")
_MARK_PATH = os.path.join(_ASSETS, "zitch-mark.png")

# Brand teal, matching the app's receipt (lib/receipt.ts) exactly. Also used by
# the statement below.
_BRAND = (15, 162, 149)
_BRAND_DEEP = (11, 122, 112)
_INK = (17, 24, 28)
_MUTED = (92, 104, 110)
_LINE = (230, 234, 236)
_BG = (255, 255, 255)
_WASH = (247, 251, 251)
# The app's own success green (lib/theme palette.lime): the status mark is the
# same colour on both surfaces.
_LIME = (0, 181, 29)

# The receipt's palette is the app's light theme (lib/theme.tsx), token for token.
_TEAL_50 = (230, 247, 244)     # palette.teal50
_TEAL_700 = (6, 110, 102)      # palette.teal700, the light theme's `brand`
_TEAL_800 = (12, 82, 73)       # palette.teal800, the light theme's `brandDeep`
_INK_1 = (6, 35, 31)           # light.ink1
_INK_3 = (82, 104, 99)         # light.ink3
_HAIRLINE = (226, 238, 235)    # light.line
_PAPER = (244, 249, 248)       # light.surface2
_CARD = (255, 255, 255)        # light.surface
_TEAR = (205, 224, 220)        # the dashed tear line, a step darker than a hairline

# 820 design units x 2.7 is about 2214px wide, so the long edge always clears
# 2160 (4K UHD's short edge).
SCALE = 2.7

_NAIRA = "₦"

_MEDIUM = "Manrope-Medium.ttf"
_BOLD = "Manrope-Bold.ttf"
_HEAVY = "Manrope-ExtraBold.ttf"
# If a Manrope file ever goes missing, DejaVu still has every character the
# receipt uses. A plainer receipt beats none.
_FALLBACK = {_MEDIUM: "DejaVuSans.ttf", _BOLD: "DejaVuSans-Bold.ttf",
             _HEAVY: "DejaVuSans-Bold.ttf"}


def _font(name: str, size: float):
    """Fonts are sized in design units and scaled here, so callers never deal in
    pixels."""
    for candidate in (name, _FALLBACK.get(name)):
        if not candidate:
            continue
        try:
            return ImageFont.truetype(os.path.join(_FONT_DIR, candidate), _px(size))
        except OSError:
            continue
    return ImageFont.load_default()


def _has_glyph(font, ch: str) -> bool:
    """Whether `font` really draws `ch`. A missing glyph still has a bounding
    box (the font's empty box), so compare it against a character no Latin font
    defines rather than asking whether anything was drawn."""
    try:
        return bytes(font.getmask(ch)) != bytes(font.getmask(""))
    except Exception:
        return False


def _px(v: float) -> int:
    return int(round(v * SCALE))


def _load_mark(height_px: int):
    """The Z ribbon, scaled to `height_px`. Returns None if the asset is missing:
    a receipt without the mark is still a valid receipt, so this never raises."""
    try:
        mark = Image.open(_MARK_PATH).convert("RGBA")
    except (OSError, ValueError):
        return None
    w = max(1, int(mark.width * (height_px / mark.height)))
    return mark.resize((w, height_px), Image.LANCZOS)


def _white(img: Image.Image) -> Image.Image:
    """A white silhouette of `img`, keeping its alpha. The mark sits on the teal
    band, where the teal-on-teal original would disappear."""
    solid = Image.new("RGBA", img.size, (255, 255, 255, 255))
    solid.putalpha(img.getchannel("A"))
    return solid


def _tinted(img: Image.Image, rgb, opacity: float) -> Image.Image:
    """`img`'s silhouette in one colour at `opacity` of its own alpha."""
    solid = Image.new("RGBA", img.size, tuple(rgb) + (255,))
    solid.putalpha(img.getchannel("A").point(lambda a: int(a * opacity)))
    return solid


def _gradient(w: int, h: int, top, bottom) -> Image.Image:
    """Vertical gradient, built one row at a time on a 1px-wide strip and stretched.
    Cheap, and free of the banding a per-pixel loop at this size would cost."""
    strip = Image.new("RGB", (1, h))
    d = ImageDraw.Draw(strip)
    for y in range(h):
        t = y / max(1, h - 1)
        d.point((0, y), fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return strip.resize((w, h), Image.BILINEAR)


def _diagonal(w: int, h: int, start, end) -> Image.Image:
    """A top-left to bottom-right gradient, computed on a small tile and scaled
    up. A smooth ramp loses nothing in the resize, and it costs a few hundred
    pixel writes instead of millions."""
    tw, th = 48, max(2, int(48 * h / max(1, w)))
    tile = Image.new("RGB", (tw, th))
    for y in range(th):
        for x in range(tw):
            t = (x / (tw - 1) + y / max(1, th - 1)) / 2
            tile.putpixel((x, y), tuple(int(start[i] + (end[i] - start[i]) * t) for i in range(3)))
    return tile.resize((w, h), Image.BICUBIC)


def _watermark(size, font) -> Image.Image:
    """Tiled diagonal ZITCH wordmarks, for the statement.

    Deliberately faint. A watermark that competes with the figures makes the
    document harder to read, which defeats it; this is here to mark provenance
    on a screenshot, not to defend against a determined forger. The reference
    and our records are what actually prove a payment.
    """
    W, H = size
    # Drawn oversized, then rotated and centre-cropped, so the rotation has no
    # bare corners.
    diag = int((W ** 2 + H ** 2) ** 0.5) + _px(80)
    layer = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    word = "ZITCH  •  ZITCH  •  "
    step_x = int(d.textlength(word, font=font))
    step_y = _px(150)
    for row, y in enumerate(range(0, diag, step_y)):
        # Offset alternate rows so the tiling reads as a texture, not a grid.
        x0 = -(row % 2) * step_x // 2
        for x in range(x0, diag, max(1, step_x)):
            d.text((x, y), word, font=font, fill=(15, 162, 149, 16))
    layer = layer.rotate(-30, resample=Image.BILINEAR)
    left, top = (diag - W) // 2, (diag - H) // 2
    return layer.crop((left, top, left + W, top + H))


# --- text, with a naira sign Manrope does not have --------------------------

def _naira_overhang(font) -> float:
    """How far the naira sign's bars run past its N on each side."""
    return font.size * 0.05


def _naira_advance(font) -> float:
    return font.getlength("N") + 2 * _naira_overhang(font)


def _text_width(text: str, font) -> float:
    parts = text.split(_NAIRA)
    return sum(font.getlength(p) for p in parts) + (len(parts) - 1) * _naira_advance(font)


def _cap_height(font) -> float:
    return -font.getbbox("H", anchor="ls")[1]


def _draw_naira(d: ImageDraw.ImageDraw, x: float, baseline: float, font, fill) -> float:
    """Draw a naira sign at `x` and return where the next character starts.

    Built from the font's own N with two bars through it. The app borrows the
    phone's system font for this one character; a server has no such font, and a
    sign from another typeface sits visibly lighter or heavier than the digits
    after it. The bars are a third of the font's stem, as a typeface's own are.
    """
    over = _naira_overhang(font)
    d.text((x + over, baseline), "N", font=font, fill=fill, anchor="ls")
    cap = _cap_height(font)
    left, _top, right, _bottom = font.getbbox("I", anchor="ls")
    half = max(1.0, (right - left) * 0.17)
    end = x + _naira_advance(font)
    for frac in (0.355, 0.645):
        mid = baseline - cap * frac
        d.rectangle([x, mid - half, end, mid + half], fill=fill)
    return end


def _draw_text(d: ImageDraw.ImageDraw, x: float, baseline: float, text: str, font, fill) -> float:
    """Draw `text` with its baseline at `baseline`; returns the end x."""
    for index, part in enumerate(text.split(_NAIRA)):
        if index:
            x = _draw_naira(d, x, baseline, font, fill)
        if part:
            d.text((x, baseline), part, font=font, fill=fill, anchor="ls")
            x += font.getlength(part)
    return x


def _wrap(text: str, font, width: float) -> list:
    """Break `text` into lines no wider than `width`, at spaces where it can and
    inside a word where it must: a reference has no spaces, and one that ran off
    the ticket would be the line someone needed."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if _text_width(trial, font) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        while _text_width(word, font) > width and len(word) > 1:
            cut = len(word) - 1
            while cut > 1 and _text_width(word[:cut], font) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        line = word
    if line:
        lines.append(line)
    return lines or [""]


# --- what goes where -------------------------------------------------------

def _status_art(status: str):
    """(pill colour, mark colour, glyph) for the status pill under the amount.

    The app draws a lime disc for a success. A failure or a pending payment must
    NOT borrow that: the mark is the first thing read, at a glance, often without
    reading the word beside it, so a red payment wearing a green tick is the
    receipt telling a lie faster than it can be corrected.

    `glyph` is None for pending: it is drawn as three dots, not typeset.
    """
    key = (status or "").strip().lower()
    if key.startswith("fail") or key.startswith("not"):
        return (253, 236, 236), (200, 46, 38), "✕"
    if key.startswith("pend") or key.startswith("process"):
        return (253, 243, 224), (185, 119, 14), None
    return (232, 246, 238), _LIME, "✓"


def _status_ink(status: str):
    """The pill's text colour: the app PDF badge's, dark enough to read on its tint."""
    key = (status or "").strip().lower()
    if key.startswith("fail") or key.startswith("not"):
        return (168, 45, 38)
    if key.startswith("pend") or key.startswith("process"):
        return (150, 96, 8)
    return (18, 140, 74)


def receipt_layout(title: str, rows: list, reference: str = "", *,
                   status: str = "Successful") -> dict:
    """Where each value the caller passed goes on the receipt.

    * The amount is the headline, and is not repeated as a row.
    * The date and time sit under the status, and are not repeated as rows.
    * The recipient's name, bank and account read as one entry, as do the
      sender's name and account: a reader checks "who" as one question.
    * An electricity token or exam PIN gets a box of its own above the rest.
      It is the one value someone types somewhere else, so it must be found
      first and copied without error.
    * Everything else is an ordinary row, in the caller's order, with the
      reference last. It is supplied from `reference` when no row carries it.

    No row reads "sent" or claims an outcome: the status pill alone states it,
    from `status`.

    Separate from the drawing so these choices can be asserted directly.
    """
    pairs = [(str(k).strip(), str(v).strip()) for k, v in rows]

    def take(*labels) -> str:
        for index, (key, value) in enumerate(pairs):
            if key.lower() in labels:
                del pairs[index]
                return value
        return ""

    amount = take("amount")
    when = ", ".join(x for x in (take("date"), take("time")) if x)
    parties = []
    name = take("to", "recipient", "beneficiary")
    if name:
        detail = " · ".join(x for x in (take("bank"), take("account")) if x)
        parties.append(("Recipient", name, detail))
    payer = take("from")
    if payer:
        parties.append(("Sender", payer, take("sender")))
    ref = take("reference") or (reference or "").strip()
    highlights = [(k, v) for k, v in pairs if k.lower() in ("token", "pin")]
    table = [(k, v) for k, v in pairs if k.lower() not in ("token", "pin")]
    if ref:
        table.append(("Reference", ref))
    label = (status or "").strip() or "Successful"
    return {
        "title": (title or "").strip() or "Transaction receipt",
        "amount": amount,
        "status": label[:1].upper() + label[1:],
        "when": when,
        "parties": parties,
        "highlights": highlights,
        "rows": table,
    }


def tabulated_rows(rows: list, reference: str = "") -> list:
    """The ordinary label/value rows the receipt draws, in order."""
    return receipt_layout("", rows, reference)["rows"]


# --- drawing ---------------------------------------------------------------

def _supersampled(img: Image.Image, box, paint, factor: int = 4) -> None:
    """Paint shapes into `box` with anti-aliased edges.

    Pillow draws ellipses, rounded rectangles and lines without anti-aliasing,
    and on a receipt people zoom into, a stair-stepped tick reads as cheap. The
    shapes are drawn `factor` times larger on a layer the size of `box` alone,
    then shrunk onto the image. `paint(draw, s)` receives the layer and a
    function mapping receipt pixels inside the box to layer pixels.
    """
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    layer = Image.new("RGBA", (w * factor, h * factor), (0, 0, 0, 0))

    def s(*points):
        return [((px - x0) * factor, (py - y0) * factor)
                for px, py in zip(points[::2], points[1::2])]

    paint(ImageDraw.Draw(layer), s, factor)
    img.alpha_composite(layer.resize((w, h), Image.LANCZOS), (x0, y0))


def _paint_glyph(d, s, factor, cx, cy, size, glyph):
    """A white tick, cross or three dots centred on (cx, cy), `size` across."""
    white = (255, 255, 255, 255)
    stroke = size * 0.13

    def stroke_line(points):
        pts = s(*points)
        width = max(1, int(stroke * factor))
        d.line(pts, fill=white, width=width, joint="curve")
        for px, py in pts:  # round caps
            r = width / 2
            d.ellipse([px - r, py - r, px + r, py + r], fill=white)

    if glyph == "✓":
        stroke_line([cx - size * 0.24, cy + size * 0.01, cx - size * 0.07, cy + size * 0.18,
                     cx + size * 0.25, cy - size * 0.17])
    elif glyph == "✕":
        k = size * 0.19
        stroke_line([cx - k, cy - k, cx + k, cy + k])
        stroke_line([cx - k, cy + k, cx + k, cy - k])
    else:
        r = size * 0.075
        for offset in (-size * 0.25, 0, size * 0.25):
            (px, py), = s(cx + offset, cy)
            d.ellipse([px - r * factor, py - r * factor, px + r * factor, py + r * factor],
                      fill=white)


def _pill_size(label: str, font):
    disc = _px(26)
    return _px(10) + disc + _px(11) + _text_width(label, font) + _px(20), _px(46)


def _draw_pill(img: Image.Image, cx: float, top: float, label: str, status: str, font) -> None:
    """The outcome, in words and colour: a tinted pill with a white mark on a
    solid disc, then the status word."""
    tint, solid, glyph = _status_art(status)
    w, h = _pill_size(label, font)
    x0 = cx - w / 2
    disc = _px(26)
    mx, my = x0 + _px(10) + disc / 2, top + h / 2

    def paint(d, s, factor):
        (ax, ay), (bx, by) = s(x0, top, x0 + w, top + h)
        d.rounded_rectangle([ax, ay, bx, by], radius=(by - ay) / 2, fill=tuple(tint) + (255,))
        (ax, ay), (bx, by) = s(mx - disc / 2, my - disc / 2, mx + disc / 2, my + disc / 2)
        d.ellipse([ax, ay, bx, by], fill=tuple(solid) + (255,))
        _paint_glyph(d, s, factor, mx, my, disc, glyph)

    _supersampled(img, (x0 - 2, top - 2, x0 + w + 2, top + h + 2), paint)
    d = ImageDraw.Draw(img)
    _draw_text(d, mx + disc / 2 + _px(11), my + _cap_height(font) / 2, label, font,
               _status_ink(status))


def _ticket_mask(size, radius: int, notch_y: int, notch_r: int) -> Image.Image:
    """The ticket's shape: a rounded card with a half-circle bitten out of each
    side at the tear line. Softened by a pixel so the curves are not jagged."""
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    for cx in (0, size[0] - 1):
        d.ellipse([cx - notch_r, notch_y - notch_r, cx + notch_r, notch_y + notch_r], fill=0)
    return mask.filter(ImageFilter.GaussianBlur(0.8))


def _composite_clipped(img: Image.Image, overlay: Image.Image, xy, clip=None) -> None:
    """alpha_composite `overlay` at `xy`, which may lie partly outside `img` (or
    outside `clip`, a box in `img`). Pillow refuses a negative destination."""
    x, y = int(xy[0]), int(xy[1])
    cx0, cy0, cx1, cy1 = clip or (0, 0, img.width, img.height)
    left, top = max(x, cx0), max(y, cy0)
    right, bottom = min(x + overlay.width, cx1), min(y + overlay.height, cy1)
    if right <= left or bottom <= top:
        return
    piece = overlay.crop((left - x, top - y, right - x, bottom - y))
    img.alpha_composite(piece, (left, top))


def _mark_texture(size, mark) -> Image.Image:
    """The Z mark tiled faintly across the ticket, on alternate offsets.

    Laid over the rows, so a crop cannot lift a clean region out of the middle
    of the document, and faint enough that it never competes with them: it is
    there to mark provenance on a screenshot, not to defend against a forger.
    The reference and our records are what prove a payment.
    """
    W, H = size
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if mark is None:
        return layer
    tile = _tinted(mark, _BRAND, 0.05)
    step_x, step_y = _px(116), _px(94)
    for row, y in enumerate(range(-tile.height // 2, H, step_y)):
        offset = step_x // 2 if row % 2 else 0
        for x in range(offset - tile.width, W, step_x):
            _composite_clipped(layer, tile, (x, y))
    return layer


# --- the WhatsApp banking panel -------------------------------------------

# WhatsApp's own greens: the brand green for the logo tile (the app promo's
# WA_GREEN), and its teal green for text, which reads on a light background
# where the brand green does not.
_WA_GREEN = (37, 211, 102)
_WA_TEAL = (18, 140, 126)
_GLYPH_PATH = os.path.join(_ASSETS, "whatsapp-glyph.png")

# The app's promo copy (components/design/whatsapp-banking-promo.tsx), so the
# offer reads the same wherever a customer meets it.
_PROMO_LABEL = "ZITCH WHATSAPP BANKING"
_PROMO_HEADLINE = "Bank wherever you chat"
_PROMO_BODY = "Send money, buy airtime and check your balance on WhatsApp."


def whatsapp_start() -> tuple:
    """(link, number as printed) that opens a chat with Zitch's banking number
    with "Hi" already typed, which the bot answers with its welcome.

    Both empty when no number is configured: a code that opens nothing is worse
    than no code.
    """
    from django.conf import settings

    raw = (getattr(settings, "WHATSAPP", {}) or {}).get("BUSINESS_NUMBER") or ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) < 10:
        return "", ""
    if digits.startswith("234") and len(digits) == 13:
        shown = f"+234 {digits[3:6]} {digits[6:9]} {digits[9:]}"
    else:
        shown = f"+{digits}"
    return f"https://wa.me/{digits}?text=Hi", shown


def _qr_modules(data: str):
    """The QR code for `data` as rows of booleans, without its quiet zone, or
    None if the library is missing. Medium error correction: the code is plain
    and must survive JPEG compression and a photo of a screen."""
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError:
        return None
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=0)
    code.add_data(data)
    code.make(fit=True)
    return code.get_matrix()


def _load_glyph(size: int):
    """The WhatsApp glyph (components/design/WhatsAppGlyph.tsx, rasterised) as
    a `size`-pixel white silhouette, or None if the asset is missing."""
    try:
        alpha = Image.open(_GLYPH_PATH).convert("RGBA").getchannel("A")
    except (OSError, ValueError):
        return None
    glyph = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    glyph.putalpha(alpha.resize((size, size), Image.LANCZOS))
    return glyph


def _ring_mask(size, radius: int, width: int) -> Image.Image:
    """A rounded-rectangle outline `width` px thick, softened a pixel."""
    w, h = size
    outer = Image.new("L", size, 0)
    ImageDraw.Draw(outer).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    inner = Image.new("L", size, 0)
    ImageDraw.Draw(inner).rounded_rectangle([width, width, w - 1 - width, h - 1 - width],
                                            radius=max(0, radius - width), fill=255)
    return ImageChops.subtract(outer, inner).filter(ImageFilter.GaussianBlur(0.7))


def _draw_tracked(d, x: float, baseline: float, text: str, font, fill, tracking: float) -> float:
    """Draw `text` letter-spaced by `tracking` px, as the app sets its labels."""
    for ch in text:
        d.text((x, baseline), ch, font=font, fill=fill, anchor="ls")
        x += font.getlength(ch) + tracking
    return x


def _promo_plan(x0: int, x1: int, link: str, site: str, number: str) -> dict:
    """Fonts, wrapped lines and height of the WhatsApp banking panel."""
    plan = {
        "label": _font(_BOLD, 14), "head": _font(_HEAVY, 23), "body": _font(_MEDIUM, 17),
        "site": _font(_BOLD, 18), "number": _font(_MEDIUM, 17), "caption": _font(_BOLD, 13.5),
        "pad": _px(24), "icon": _px(56), "modules": _qr_modules(link) if link else None,
    }
    modules = plan["modules"]
    plan["cell"] = max(2, _px(112) // len(modules)) if modules else 0
    plan["qr"] = plan["cell"] * len(modules) if modules else 0
    plan["quiet"] = 2 * plan["cell"]
    plan["tile"] = plan["qr"] + 2 * plan["quiet"]
    plan["text_x"] = x0 + plan["pad"] + plan["icon"] + _px(18)
    right = x1 - plan["pad"] - (plan["tile"] + _px(24) if modules else 0)
    width = right - plan["text_x"]
    plan["body_lines"] = _wrap(_PROMO_BODY, plan["body"], width)
    joined = f"{site}  \u00b7  {number}" if number else site
    plan["contact"] = [joined] if _text_width(joined, plan["site"]) <= width else \
        [x for x in (site, number) if x]
    plan["text_h"] = (_cap_height(plan["label"]) + _px(15) + _cap_height(plan["head"])
                      + _px(16) + _cap_height(plan["body"])
                      + (len(plan["body_lines"]) - 1) * _px(25)
                      + _px(20) + _cap_height(plan["site"])
                      + (len(plan["contact"]) - 1) * _px(26) + _px(5))
    plan["qr_h"] = (plan["tile"] + _px(10) + _cap_height(plan["caption"])) if modules else 0
    plan["inner_h"] = max(plan["text_h"], plan["qr_h"], plan["icon"])
    plan["height"] = plan["inner_h"] + 2 * plan["pad"]
    return plan


def _draw_promo(img: Image.Image, x0: int, top: int, x1: int, plan: dict, site: str,
                number: str) -> None:
    """Zitch WhatsApp banking: the app's promo, with a code to scan.

    Every receipt is forwarded to someone, which makes it the one place the
    offer reaches people who do not bank with Zitch yet. The code opens a chat
    with the banking number with "Hi" typed; the number is printed beside it
    for the reader who is looking at the receipt on the phone they would scan
    with.
    """
    height = plan["height"]
    ring = _ring_mask((x1 - x0, height), _px(22), max(1, _px(1.5)))
    tone = Image.new("RGBA", ring.size, tuple(_WA_GREEN) + (0,))
    tone.putalpha(ring.point(lambda a: int(a * 0.45)))
    img.alpha_composite(tone, (x0, top))

    inner_top = top + plan["pad"]
    text_top = inner_top + (plan["inner_h"] - plan["text_h"]) // 2
    icon = plan["icon"]
    ix, iy = x0 + plan["pad"], text_top

    def tile(d, s, factor):
        (ax, ay), (bx, by) = s(ix, iy, ix + icon, iy + icon)
        d.rounded_rectangle([ax, ay, bx, by], radius=(bx - ax) * 0.29,
                            fill=tuple(_WA_GREEN) + (255,))

    _supersampled(img, (ix, iy, ix + icon, iy + icon), tile)
    glyph = _load_glyph(int(icon * 0.58))
    if glyph is not None:
        img.alpha_composite(glyph, (int(ix + (icon - glyph.width) / 2),
                                    int(iy + (icon - glyph.height) / 2)))

    d = ImageDraw.Draw(img)
    x = plan["text_x"]
    base = text_top + _cap_height(plan["label"])
    _draw_tracked(d, x, base, _PROMO_LABEL, plan["label"], _WA_TEAL, _px(1.3))
    base += _px(15) + _cap_height(plan["head"])
    d.text((x, base), _PROMO_HEADLINE, font=plan["head"], fill=_INK_1, anchor="ls")
    base += _px(16) + _cap_height(plan["body"])
    for index, line in enumerate(plan["body_lines"]):
        d.text((x, base + index * _px(25)), line, font=plan["body"], fill=_INK_3, anchor="ls")
    base += (len(plan["body_lines"]) - 1) * _px(25) + _px(20) + _cap_height(plan["site"])
    for line in plan["contact"]:
        if line.startswith(site):
            end = _draw_text(d, x, base, site, plan["site"], _TEAL_700)
            rest = line[len(site):]
            if rest:
                d.text((end, base), rest, font=plan["number"], fill=_INK_3, anchor="ls")
        else:
            d.text((x, base), line, font=plan["number"], fill=_INK_3, anchor="ls")
        base += _px(26)

    modules = plan["modules"]
    if not modules:
        return
    size = plan["tile"]
    qx = x1 - plan["pad"] - size
    qy = inner_top + (plan["inner_h"] - plan["qr_h"]) // 2

    def white(d2, s, factor):
        (ax, ay), (bx, by) = s(qx, qy, qx + size, qy + size)
        d2.rounded_rectangle([ax, ay, bx, by], radius=_px(12) * factor, fill=(255, 255, 255, 255))

    _supersampled(img, (qx, qy, qx + size, qy + size), white)
    d = ImageDraw.Draw(img)
    cell, ox, oy = plan["cell"], qx + plan["quiet"], qy + plan["quiet"]
    for r, row in enumerate(modules):
        for c, dark in enumerate(row):
            if dark:
                d.rectangle([ox + c * cell, oy + r * cell, ox + (c + 1) * cell - 1,
                             oy + (r + 1) * cell - 1], fill=_INK_1)
    d.text((qx + size / 2, qy + size + _px(10) + _cap_height(plan["caption"])),
           "Scan to start", font=plan["caption"], fill=_INK_3, anchor="ms")


def render_receipt(title: str, rows: list, ref: str, *, status: str = "Successful") -> bytes:
    """Return JPEG bytes for a receipt titled `title` with `rows` = [(label, value)].

    The sheet: a teal Zitch header with the receipt's title, then a white ticket
    with the amount, the status pill and the time, a tear line, and the rows as
    `receipt_layout` arranges them. Under it, Zitch WhatsApp banking with a code
    to scan (`_draw_promo`), then where to get help.
    """
    from django.conf import settings
    from django.utils import timezone

    layout = receipt_layout(title, rows, ref, status=status)

    wordmark = _font(_HEAVY, 34)
    header = _font(_BOLD, 20)
    pill = _font(_BOLD, 21)
    when_font = _font(_MEDIUM, 21)
    label_font = _font(_MEDIUM, 20)
    value_font = _font(_BOLD, 22)
    sub_font = _font(_MEDIUM, 19)
    box_label = _font(_BOLD, 16)
    box_value = _font(_HEAVY, 30)
    foot = _font(_MEDIUM, 17)
    foot_small = _font(_MEDIUM, 15)

    W = _px(820)
    margin = _px(34)                     # canvas edge to ticket
    inset = _px(44)                      # ticket edge to text
    card_w = W - 2 * margin
    left, right = margin + inset, W - margin - inset
    content_w = right - left
    label_w = _px(190)
    value_w = content_w - label_w - _px(20)

    # The headline: the amount, shrunk only if a very large one would not fit.
    headline = layout["amount"] or layout["title"]
    size = 64 if layout["amount"] else 44
    hero = _font(_HEAVY, size)
    while size > 30 and _text_width(headline, hero) > content_w:
        size -= 2
        hero = _font(_HEAVY, size)
    when = layout["when"] or timezone.localtime().strftime("%d %b %Y, %H:%M")

    # --- measure, top to bottom, before anything is drawn --------------------
    band_h = _px(232)
    card_top = _px(126)
    hero_base = card_top + _px(52) + _cap_height(hero)
    pill_top = hero_base + _px(30)
    pill_h = _pill_size(layout["status"], pill)[1]
    when_base = pill_top + pill_h + _px(42)
    tear_y = when_base + _px(40)          # absolute y of the tear line

    row_pad = _px(18)
    value_gap, sub_gap = _px(31), _px(28)
    blocks = []                           # ("box"|"row", label, lines, sub_lines, height)
    for label, value in layout["highlights"]:
        lines = _wrap(value, box_value, content_w - 2 * _px(22))
        height = _px(22) + _cap_height(box_label) + _px(18) + _cap_height(box_value) \
            + (len(lines) - 1) * _px(42) + _px(24)
        blocks.append(("box", label, lines, [], height))
    for label, name, detail in layout["parties"]:
        lines = _wrap(name, value_font, value_w)
        sub = _wrap(detail, sub_font, value_w) if detail else []
        blocks.append(("row", label, lines, sub, 0))
    for label, value in layout["rows"]:
        blocks.append(("row", label, _wrap(value, value_font, value_w), [], 0))
    for index, (kind, label, lines, sub, height) in enumerate(blocks):
        if kind == "row":
            label_lines = _wrap(label, label_font, label_w)
            body = _cap_height(value_font) + max(
                (len(lines) - 1) * value_gap + len(sub) * sub_gap,
                (len(label_lines) - 1) * value_gap)
            blocks[index] = (kind, label_lines, lines, sub, 2 * row_pad + body + _px(4))

    body_top = tear_y + _px(16)
    box_gap = _px(14)
    body_h = sum(h + (box_gap if k == "box" else 0) for k, _l, _v, _s, h in blocks)
    card_bottom = body_top + body_h + _px(26)

    links = getattr(settings, "ZITCH_LINKS", {}) or {}
    site = (links.get("WEBSITE") or "https://zitch.ng").split("://", 1)[-1].rstrip("/")
    link, number = whatsapp_start()
    promo_top = card_bottom + _px(30)
    promo = _promo_plan(margin, W - margin, link, site, number)
    foot_top = promo_top + promo["height"] + _px(34)
    H = foot_top + _cap_height(foot) + _px(24) + _cap_height(foot_small) + _px(44)

    # --- the sheet ------------------------------------------------------------
    img = Image.new("RGBA", (W, H), tuple(_PAPER) + (255,))
    img.paste(_diagonal(W, band_h, _BRAND, _TEAL_800), (0, 0))
    mark = _load_mark(_px(48))
    big = _load_mark(_px(300))
    if big is not None:
        # A large ghost of the ribbon on the band's right, cut off by its edges.
        _composite_clipped(img, _tinted(big, (255, 255, 255), 0.08),
                           (W - big.width + _px(26), -_px(46)), clip=(0, 0, W, band_h))
    d = ImageDraw.Draw(img)

    # Header: the mark and wordmark on the left, the receipt's title on the right.
    row_mid = _px(64)
    x = left
    if mark is not None:
        white = _white(mark)
        img.alpha_composite(white, (int(x), int(row_mid - white.height / 2)))
        x += white.width + _px(14)
    word_base = row_mid + _cap_height(wordmark) / 2 + _px(1)
    d.text((x, word_base), "zitch", font=wordmark, fill=(255, 255, 255), anchor="ls")
    title = layout["title"]
    d.text((right - _text_width(title, header), row_mid + _cap_height(header) / 2),
           title, font=header, fill=_TEAL_50, anchor="ls")

    # The ticket, its soft shadow first.
    notch_r = _px(17)
    shape = _ticket_mask((card_w, card_bottom - card_top), _px(28), tear_y - card_top, notch_r)
    shadow = Image.new("L", (W // 4, H // 4), 0)
    shadow.paste(shape.resize((max(1, card_w // 4), max(1, (card_bottom - card_top) // 4))),
                 (margin // 4, (card_top + _px(10)) // 4))
    shadow = shadow.filter(ImageFilter.GaussianBlur(_px(14) / 4)).resize((W, H), Image.BICUBIC)
    tone = Image.new("RGBA", (W, H), tuple(_TEAL_800) + (0,))
    tone.putalpha(shadow.point(lambda a: int(a * 0.16)))
    img.alpha_composite(tone)
    ticket = Image.new("RGBA", (card_w, card_bottom - card_top), tuple(_CARD) + (255,))
    ticket.putalpha(shape)
    img.alpha_composite(ticket, (margin, card_top))
    d = ImageDraw.Draw(img)

    # Headline, status, time.
    cx = W / 2
    _draw_text(d, cx - _text_width(headline, hero) / 2, hero_base, headline, hero, _INK_1)
    _draw_pill(img, cx, pill_top, layout["status"], status, pill)
    d = ImageDraw.Draw(img)
    _draw_text(d, cx - _text_width(when, when_font) / 2, when_base, when, when_font, _INK_3)

    # The tear line: a dashed rule between the two notches.
    dash, gapw = _px(9), _px(7)
    x = margin + notch_r + _px(12)
    while x < W - margin - notch_r - _px(12):
        d.line([x, tear_y, min(x + dash, W - margin - notch_r - _px(12)), tear_y],
               fill=_TEAR, width=max(1, _px(2)))
        x += dash + gapw

    # Body.
    y = body_top
    previous = None
    for kind, label, lines, sub, height in blocks:
        if kind == "box":
            top = y + box_gap
            d.rounded_rectangle([left, top, right, top + height], radius=_px(18), fill=_TEAL_50)
            base = top + _px(22) + _cap_height(box_label)
            d.text((left + _px(22), base), label.upper(), font=box_label, fill=_TEAL_700,
                   anchor="ls")
            base += _px(18) + _cap_height(box_value)
            for line in lines:
                _draw_text(d, left + _px(22), base, line, box_value, _INK_1)
                base += _px(42)
            y = top + height
        else:
            if previous == "row":
                d.line([left, y, right, y], fill=_HAIRLINE, width=max(1, _px(1)))
            base = y + row_pad + _cap_height(value_font)
            for line_no, text in enumerate(label):
                d.text((left, base + line_no * value_gap), text, font=label_font,
                       fill=_INK_3, anchor="ls")
            for text in lines:
                _draw_text(d, right - _text_width(text, value_font), base, text,
                           value_font, _INK_1)
                base += value_gap
            base += sub_gap - value_gap
            for text in sub:
                _draw_text(d, right - _text_width(text, sub_font), base, text, sub_font,
                           _INK_3)
                base += sub_gap
            y += height
        previous = kind

    # The texture rides over the rows, clipped to the ticket.
    texture = _mark_texture((card_w, card_bottom - card_top), _load_mark(_px(34)))
    texture.putalpha(Image.composite(texture.getchannel("A"),
                                     Image.new("L", texture.size, 0), shape))
    img.alpha_composite(texture, (margin, card_top))
    d = ImageDraw.Draw(img)

    # Zitch WhatsApp banking, then where to get help.
    _draw_promo(img, margin, promo_top, W - margin, promo, site, number)
    d = ImageDraw.Draw(img)
    support = links.get("SUPPORT_EMAIL", "")
    base = foot_top + _cap_height(foot)
    if support:
        d.text((cx, base), f"Need help? {support}", font=foot, fill=_INK_3, anchor="ms")
        base += _px(24) + _cap_height(foot_small)
    d.text((cx, base), "Keep this receipt for your records.", font=foot_small,
           fill=_INK_3, anchor="ms")

    buf = io.BytesIO()
    # subsampling=0 keeps chroma at full resolution: the default halves it, which
    # is invisible on a photo and visibly softens small text.
    img.convert("RGB").save(buf, format="JPEG", quality=94, subsampling=0, optimize=True)
    return buf.getvalue()


_STATUS_BADGE = {
    "success": ("✓ Successful", (226, 244, 240), _BRAND_DEEP),
    "pending": ("⏳ Pending", (255, 244, 219), (156, 108, 5)),
    "under_review": ("⏳ Under review", (255, 244, 219), (156, 108, 5)),
    "failed": ("✕ Failed", (252, 228, 226), (168, 45, 38)),
}


def render_statement_pdf(rows: list, *, balance: str, generated: str,
                         heading: str = "", holder: str = "",
                         account: str = "", period: str = "",
                         address: str = "") -> bytes:
    """Return single-page PDF bytes for a transaction-history statement.

    `rows` = [{"date", "label", "amount", "sign", "status", "reference"}], newest
    first. Drawn with the same brand header/watermark as the receipt, then a
    row per transaction, so a customer forwarding the file gets something that
    reads as Zitch's, not a generic export.

    The optional identity block (`holder`/`account`/`period`/`address`) is what
    separates a chat convenience list from a document someone hands to a landlord
    or an embassy: those readers need to see WHOSE account it is and WHAT window
    it covers, and a statement that cannot say so is not evidence of anything.
    The WhatsApp caller passes none of them and gets the compact list it always
    got. `address` is only ever supplied when the customer asked for it.
    """
    reg = _font("DejaVuSans.ttf", 22)
    bold = _font("DejaVuSans-Bold.ttf", 24)
    wordmark = _font("DejaVuSans-Bold.ttf", 44)
    small = _font("DejaVuSans.ttf", 19)
    tiny = _font("DejaVuSans.ttf", 17)
    mark_font = _font("DejaVuSans-Bold.ttf", 34)

    naira_ok = _has_glyph(reg, _NAIRA)

    def money_safe(v: str) -> str:
        return v if naira_ok else v.replace(_NAIRA, "NGN ")

    # The callers pass the FULLWIDTH +/- (U+FF0B/U+FF0D) because that is what
    # lines up in a WhatsApp chat bubble — but DejaVu has no glyph for either,
    # so on the rendered sheet every amount was prefixed with a tofu box.
    def sign_safe(v: str) -> str:
        return v.replace("＋", "+").replace("－", "-")

    W = _px(820)
    pad = _px(56)
    band_h = _px(150)
    title_h = _px(90)
    row_h = _px(84)
    foot_h = _px(100)

    # Only the identity lines actually supplied take up space — the chat's
    # compact list passes none and its layout is unchanged.
    id_lines = [(k, v) for k, v in
                (("Account name", holder), ("Account number", account),
                 ("Statement period", period), ("Address", address)) if v]
    id_h = (_px(30) + _px(38) * len(id_lines)) if id_lines else 0

    H = band_h + title_h + id_h + row_h * max(1, len(rows)) + foot_h

    img = Image.new("RGB", (W, H), _BG)
    img.paste(_gradient(W, band_h, _BRAND, _BRAND_DEEP), (0, 0))
    d = ImageDraw.Draw(img)

    # --- header band ---------------------------------------------------------
    mark = _load_mark(_px(62))
    x = pad
    if mark:
        img.paste(_white(mark), (x, (band_h - mark.height) // 2 - _px(8)), _white(mark))
        x += mark.width + _px(20)
    d.text((x, band_h // 2 - _px(38)), "zitch", font=wordmark, fill=(255, 255, 255))
    d.text((x + _px(2), band_h // 2 + _px(6)),
           "ACCOUNT STATEMENT" if id_lines else "TRANSACTION HISTORY", font=tiny,
           fill=(226, 246, 243))
    site = "zitch.ng"
    d.text((W - pad - d.textlength(site, font=small), band_h // 2 - _px(12)),
           site, font=small, fill=(226, 246, 243))

    # --- title / balance -------------------------------------------------
    y = band_h
    d.text((pad, y + _px(20)),
           heading or f"Last {len(rows)} transaction{'s' if len(rows) != 1 else ''}",
           font=bold, fill=_INK)
    bal_line = f"Balance {money_safe(balance)}"
    d.text((W - pad - d.textlength(bal_line, font=small), y + _px(26)), bal_line,
           font=small, fill=_MUTED)
    d.line([0, y + title_h, W, y + title_h], fill=_LINE, width=max(1, _px(1)))
    y += title_h

    # --- who / when ---------------------------------------------------------
    if id_lines:
        d.rectangle([0, y, W, y + id_h], fill=_WASH)
        ly = y + _px(22)
        for k, v in id_lines:
            d.text((pad, ly), k, font=tiny, fill=_MUTED)
            d.text((pad + _px(210), ly - _px(2)), str(v), font=small, fill=_INK)
            ly += _px(38)
        d.line([0, y + id_h, W, y + id_h], fill=_LINE, width=max(1, _px(1)))
        y += id_h

    # The watermark goes over the body only, same reasoning as the receipt.
    body_box = (0, band_h + title_h + id_h, W, H - foot_h)
    body_h = body_box[3] - body_box[1]
    if body_h > 0:
        body = img.crop(body_box).convert("RGBA")
        body.alpha_composite(_watermark((W, body_h), mark_font))
        img.paste(body.convert("RGB"), body_box[:2])
    d = ImageDraw.Draw(img)

    # --- rows ------------------------------------------------------------
    for row in rows:
        amount_str = f"{sign_safe(row.get('sign', ''))}{money_safe(row.get('amount', ''))}"
        colour = _BRAND_DEEP if row.get("sign") == "＋" else _INK
        d.text((pad, y + _px(14)), row.get("label", ""), font=bold, fill=_INK)
        d.text((pad, y + _px(46)), row.get("date", ""), font=tiny, fill=_MUTED)
        d.text((W - pad - d.textlength(amount_str, font=bold), y + _px(12)), amount_str,
               font=bold, fill=colour)
        badge_text, badge_bg, badge_fg = _STATUS_BADGE.get(
            row.get("status", "success"), _STATUS_BADGE["success"])
        bw = d.textlength(badge_text, font=tiny)
        by = y + _px(44)
        d.rounded_rectangle([W - pad - bw - _px(24), by, W - pad, by + _px(32)],
                            radius=_px(16), fill=badge_bg)
        d.text((W - pad - bw - _px(12), by + _px(6)), badge_text, font=tiny, fill=badge_fg)
        y += row_h
        d.line([pad, y, W - pad, y], fill=_LINE, width=max(1, _px(1)))

    # --- footer ------------------------------------------------------------
    fy = H - foot_h
    d.rectangle([0, fy, W, H], fill=_WASH)
    d.text((pad, fy + _px(26)), f"Generated  {generated}", font=small, fill=_INK)
    note = "Generated by Zitch · zitch.ng"
    d.text((W - pad - d.textlength(note, font=small), fy + _px(26)), note,
           font=small, fill=_BRAND_DEEP)

    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=100.0 * SCALE)
    return buf.getvalue()
