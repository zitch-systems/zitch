"""Render a branded transaction receipt as a JPEG (for WhatsApp).

The chat sends this image so the customer gets an actual receipt file. Text is
drawn with a bundled DejaVu font so rendering is identical on any host (no
reliance on system fonts). No colour-emoji glyphs are drawn (the check mark is a
vector), so nothing renders as tofu.

Everything is laid out in *design units* — a ~820pt-wide sheet, the size the
layout was tuned at — and rendered through `SCALE`. A receipt is screenshotted,
zoomed and forwarded, so it is rasterised well above display size: at SCALE the
long edge lands in 4K territory and the type stays crisp when someone pinches
into it. Change SCALE alone to move the output resolution; no coordinate in this
file needs to know.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
_MARK_PATH = os.path.join(os.path.dirname(__file__), "assets", "zitch-mark.png")

# Brand teal, matching the app's receipt (lib/receipt.ts) exactly. The two
# surfaces render the same document and previously used two different greens.
_BRAND = (15, 162, 149)
_BRAND_DEEP = (11, 122, 112)
_INK = (17, 24, 28)
_MUTED = (92, 104, 110)
_LINE = (230, 234, 236)
_BG = (255, 255, 255)
_WASH = (247, 251, 251)
# The app's own success green and card surface (lib/theme palette.lime, c.surface),
# so the mark and the rows card are the same colours on both surfaces rather than
# two greens that almost match — which reads worse than two that plainly differ.
_LIME = (0, 181, 29)
_SURFACE = (255, 255, 255)

# 820 design units × 2.7 ≈ 2214px wide, and a typical receipt runs taller than it
# is wide — so the long edge clears 2160 (4K UHD's short edge) with room to spare.
SCALE = 2.7

_NAIRA = "₦"


def _font(name: str, size: float):
    """Fonts are sized in design units and scaled here, so callers never deal in
    pixels."""
    try:
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), int(round(size * SCALE)))
    except OSError:
        return ImageFont.load_default()


def _has_glyph(font, ch: str) -> bool:
    try:
        return font.getmask(ch).getbbox() is not None
    except Exception:
        return False


def _px(v: float) -> int:
    return int(round(v * SCALE))


def _load_mark(height_px: int):
    """The Z ribbon, scaled to `height_px`. Returns None if the asset is missing —
    a receipt without the mark is still a valid receipt, so this never raises."""
    try:
        mark = Image.open(_MARK_PATH).convert("RGBA")
    except (OSError, ValueError):
        return None
    w = max(1, int(mark.width * (height_px / mark.height)))
    return mark.resize((w, height_px), Image.LANCZOS)


def _white(img: Image.Image) -> Image.Image:
    """A white silhouette of `img`, keeping its alpha — the mark sits on the teal
    band, where the teal-on-teal original would disappear."""
    solid = Image.new("RGBA", img.size, (255, 255, 255, 255))
    solid.putalpha(img.getchannel("A"))
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


def _watermark(size, font) -> Image.Image:
    """Tiled diagonal ZITCH wordmarks plus a large ghost ribbon.

    Deliberately faint. A watermark that competes with the amount makes the
    receipt harder to read, which defeats the artifact; this is here to mark
    provenance on a screenshot, not to defend against a determined forger — the
    reference and our records are what actually prove a payment.
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


def _rounded_card(size, radius, fill, border):
    """The app draws its rows on a rounded surface with a hairline border. Pillow
    has no border-radius, so the card is composited as its own RGBA layer."""
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=fill + (255,),
        outline=border + (255,), width=max(1, _px(1)))
    return card


def _status_art(status: str):
    """(ring colour, disc colour, glyph) for the mark at the top.

    The app draws a lime disc inside a pale lime ring for a success. A failure or
    a pending payment must NOT borrow that: the mark is the first thing read, at a
    glance, often without reading the words under it, so a red payment wearing a
    green tick is the receipt telling a lie faster than it can be corrected.

    `glyph` is None for pending \u2014 three dots typeset at this size sit high and
    wide enough to read as two eyes and a nose, so they are drawn as circles.
    """
    key = (status or "").strip().lower()
    if key.startswith("fail") or key.startswith("not"):
        return (252, 228, 226), (200, 46, 38), "\u2715"
    if key.startswith("pend"):
        return (255, 244, 219), (176, 122, 6), None
    return (223, 244, 226), _LIME, "\u2713"


def tabulated_rows(rows: list, reference: str = "") -> list:
    """Which rows go in the card, given everything the caller passed.

    Only the AMOUNT is lifted out: the app prints it in the sentence under the
    heading and not in the table, and printed twice it reads as a rendering
    fault. Date, time and reference are rows on the app's card, so they are rows
    here — moving them would be this renderer inventing a layout rather than
    matching one.

    Separate from the drawing so the choice can be asserted directly. Testing it
    through the rendered image meant measuring pixel heights, which conflates
    "this became a row" with "this made the sentence longer".
    """
    kept = [(str(k), str(v)) for k, v in rows if str(k).strip().lower() != "amount"]
    if reference and not any(k.strip().lower() == "reference" for k, _ in kept):
        kept.append(("Reference", reference))
    return kept


def _sent_verb(status: str) -> str:
    """How the line under the heading describes the movement.

    "\u20a62,000.00 sent to ADEYEMI WILLIAM" on a FAILED receipt is a false statement
    about money, printed on the one document whose whole job is to be true about
    money. Only a settled success may say "sent"; the others name the recipient
    without claiming the transfer happened.
    """
    key = (status or "").strip().lower()
    if key.startswith("fail") or key.startswith("not"):
        return "to"
    if key.startswith("pend"):
        return "on its way to"
    return "sent to"


def render_receipt(title: str, rows: list, ref: str, *, status: str = "Successful") -> bytes:
    """Return JPEG bytes for a receipt titled `title` with `rows` = [(label, value)].

    Laid out as the MOBILE APP lays it out — centred status mark, title, one line
    saying what happened, then a rounded card of label/value rows and a small
    "zitch · zitch.ng" line. The two used to be visibly different documents: a
    customer who saw the app receipt and then a WhatsApp one could reasonably
    wonder whether the same company produced both, which is precisely the doubt a
    receipt exists to remove.

    An "Amount" row is folded into the sentence under the title, exactly as the
    app does, and left out of the table — the app shows it once, so this shows it
    once. `Reference`, `Date` and `Time` stay as ordinary rows here, because that
    is where the app puts them.
    """
    from django.utils import timezone

    reg = _font("DejaVuSans.ttf", 26)
    semi = _font("DejaVuSans-Bold.ttf", 25)
    heading = _font("DejaVuSans-Bold.ttf", 44)
    small = _font("DejaVuSans.ttf", 25)
    tiny = _font("DejaVuSans.ttf", 21)
    wordmark = _font("DejaVuSans-Bold.ttf", 28)
    mark_font = _font("DejaVuSans-Bold.ttf", 34)
    glyph_font = _font("DejaVuSans-Bold.ttf", 62)

    naira_ok = _has_glyph(reg, _NAIRA)

    def money_safe(v: str) -> str:
        return v if naira_ok else v.replace(_NAIRA, "NGN ")

    rows = [(str(k), str(v)) for k, v in rows]

    def pick(label: str) -> str:
        return next((v for k, v in rows if k.strip().lower() == label), "")

    amount = pick("amount")
    recipient = pick("to") or pick("recipient") or pick("beneficiary")
    reference = pick("reference") or ref
    body_rows = tabulated_rows(rows, reference)

    # The sentence under the heading, in the app's words.
    if amount and recipient:
        message = f"{money_safe(amount)} {_sent_verb(status)} {recipient}."
    elif amount:
        message = f"{money_safe(amount)} — {title.lower()}."
    else:
        message = ""

    W = _px(820)
    pad = _px(58)
    card_pad = _px(26)
    row_h = _px(64)

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def wrap(text: str, font, width: int) -> list:
        words, lines, line = text.split(), [], ""
        for word in words:
            trial = f"{line} {word}".strip()
            if probe.textlength(trial, font=font) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        return lines

    message_lines = wrap(message, small, W - 2 * pad - _px(80)) if message else []

    # Measure the rows before drawing: a value too long to sit beside its label
    # drops onto its own line, and the sheet has to be tall enough for that first.
    value_width = W - 2 * pad - 2 * card_pad
    heights = []
    for k, v in body_rows:
        room = value_width - probe.textlength(k, font=small) - _px(30)
        heights.append(row_h + (_px(34) if probe.textlength(money_safe(v), font=semi) > room else 0))

    mark_top = _px(52)
    ring = _px(150)
    head_h = mark_top + ring + _px(30) + _px(56) + (len(message_lines) * _px(38)) + _px(34)
    card_h = sum(heights) + 2 * _px(8)
    foot_h = _px(112)
    H = head_h + card_h + foot_h

    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)

    # --- status mark, centred ------------------------------------------------
    ring_rgb, disc_rgb, glyph = _status_art(status)
    cx = W // 2
    d.ellipse([cx - ring // 2, mark_top, cx + ring // 2, mark_top + ring], fill=ring_rgb)
    disc = _px(106)
    dy = mark_top + (ring - disc) // 2
    d.ellipse([cx - disc // 2, dy, cx + disc // 2, dy + disc], fill=disc_rgb)
    if glyph is None:
        mid = dy + disc // 2
        radius, gap = _px(9), _px(26)
        for offset in (-gap, 0, gap):
            d.ellipse([cx + offset - radius, mid - radius,
                       cx + offset + radius, mid + radius], fill=(255, 255, 255))
    else:
        gw = d.textlength(glyph, font=glyph_font)
        bbox = glyph_font.getbbox(glyph)
        d.text((cx - gw / 2, dy + (disc - (bbox[3] - bbox[1])) / 2 - bbox[1]), glyph,
               font=glyph_font, fill=(255, 255, 255))

    y = mark_top + ring + _px(30)
    tw = d.textlength(title, font=heading)
    d.text((cx - tw / 2, y), title, font=heading, fill=_INK)
    y += _px(56)
    for line in message_lines:
        lw = d.textlength(line, font=small)
        d.text((cx - lw / 2, y), line, font=small, fill=_MUTED)
        y += _px(38)

    # --- the rows card -------------------------------------------------------
    y = head_h
    card = _rounded_card((W - 2 * pad, card_h), _px(26), _SURFACE, _LINE)
    img.paste(card, (pad, y), card)
    d = ImageDraw.Draw(img)

    ry = y + _px(8)
    for index, ((k, v), h) in enumerate(zip(body_rows, heights)):
        if index:
            d.line([pad + card_pad, ry, W - pad - card_pad, ry],
                   fill=_LINE, width=max(1, _px(1)))
        val = money_safe(v)
        d.text((pad + card_pad, ry + _px(18)), k, font=small, fill=_MUTED)
        if h > row_h:
            d.text((pad + card_pad, ry + _px(50)), val, font=semi, fill=_INK)
        else:
            d.text((W - pad - card_pad - d.textlength(val, font=semi), ry + _px(16)),
                   val, font=semi, fill=_INK)
        ry += h

    # --- footer: the app's own one-line signature ----------------------------
    fy = y + card_h + _px(34)
    sig_w = d.textlength("zitch", font=wordmark) + _px(10) + d.textlength("\u00b7 zitch.ng", font=tiny)
    sx = cx - sig_w / 2
    d.text((sx, fy), "zitch", font=wordmark, fill=_BRAND)
    d.text((sx + d.textlength("zitch", font=wordmark) + _px(10), fy + _px(6)),
           "\u00b7 zitch.ng", font=tiny, fill=_MUTED)
    stamp = pick("date") or timezone.localtime().strftime("%d %b %Y")
    keep = f"Keep this receipt for your records \u00b7 {stamp}"
    d.text((cx - d.textlength(keep, font=tiny) / 2, fy + _px(40)), keep,
           font=tiny, fill=_MUTED)

    # The watermark rides over the rows, as it does in the app and the PDF, so a
    # crop cannot lift a clean region out of the middle of the document.
    layer = img.convert("RGBA")
    layer.alpha_composite(_watermark((W, H), mark_font))
    img = layer.convert("RGB")

    buf = io.BytesIO()
    # subsampling=0 keeps chroma at full resolution: the default halves it, which
    # is invisible on a photo and visibly softens small text.
    img.save(buf, format="JPEG", quality=94, subsampling=0, optimize=True)
    return buf.getvalue()


_STATUS_BADGE = {
    "success": ("✓ Successful", (226, 244, 240), _BRAND_DEEP),
    "pending": ("⏳ Pending", (255, 244, 219), (156, 108, 5)),
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
