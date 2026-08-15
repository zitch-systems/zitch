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


def render_receipt(title: str, rows: list, ref: str, *, status: str = "Successful") -> bytes:
    """Return JPEG bytes for a receipt titled `title` with `rows` = [(label, value)],
    a status badge, and `ref`/timestamp in the footer.

    An "Amount" row is promoted to the hero figure at the top and dropped from the
    table — it is the one number the reader is looking for, and repeating it twice
    reads as a mistake.
    """
    from django.utils import timezone

    reg = _font("DejaVuSans.ttf", 26)
    bold = _font("DejaVuSans-Bold.ttf", 26)
    wordmark = _font("DejaVuSans-Bold.ttf", 44)
    hero = _font("DejaVuSans-Bold.ttf", 62)
    small = _font("DejaVuSans.ttf", 21)
    tiny = _font("DejaVuSans.ttf", 19)
    mark_font = _font("DejaVuSans-Bold.ttf", 34)

    naira_ok = _has_glyph(reg, _NAIRA)

    def money_safe(v: str) -> str:
        return v if naira_ok else v.replace(_NAIRA, "NGN ")

    rows = [(str(k), str(v)) for k, v in rows]

    def pick(label: str) -> str:
        return next((v for k, v in rows if k.strip().lower() == label), "")

    # Amount, reference and date each have a home of their own — the hero and the
    # footer. Left in the table as well they read as a rendering mistake, and the
    # duplicate date was actively wrong: the footer used to stamp render time, so
    # a receipt re-sent later contradicted its own Date row.
    amount = pick("amount")
    stamp = pick("date")
    reference = pick("reference") or ref
    _OWN_PLACE = {"amount", "reference", "date"}
    body_rows = [(k, v) for k, v in rows if k.strip().lower() not in _OWN_PLACE]

    W = _px(820)
    pad = _px(56)
    band_h = _px(150)
    hero_h = _px(210) if amount else _px(96)
    row_h = _px(62)
    foot_h = _px(120)

    # Measure first: a value too long to sit beside its label wraps onto its own
    # line, and the sheet has to be tall enough for that before anything is drawn.
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    heights = []
    for k, v in body_rows:
        wide = (probe.textlength(money_safe(v), font=bold)
                > W - 2 * pad - probe.textlength(k, font=small) - _px(30))
        heights.append(row_h + (_px(30) if wide else 0))

    H = band_h + hero_h + sum(heights) + _px(40) + foot_h

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
    d.text((x + _px(2), band_h // 2 + _px(6)), "TRANSACTION RECEIPT", font=tiny,
           fill=(226, 246, 243))
    site = "zitch.ng"
    d.text((W - pad - d.textlength(site, font=small), band_h // 2 - _px(12)),
           site, font=small, fill=(226, 246, 243))

    # --- hero: amount + status ----------------------------------------------
    y = band_h
    if amount:
        d.rectangle([0, y, W, y + hero_h], fill=_WASH)
        d.text((pad, y + _px(34)), money_safe(title).upper(), font=tiny, fill=_MUTED)
        d.text((pad, y + _px(66)), money_safe(amount), font=hero, fill=_INK)
        badge = f"✓ {status}"
        bw = d.textlength(badge, font=small)
        by = y + _px(152)
        d.rounded_rectangle([pad, by, pad + bw + _px(34), by + _px(44)],
                            radius=_px(22), fill=(226, 244, 240))
        d.text((pad + _px(17), by + _px(9)), badge, font=small, fill=_BRAND_DEEP)
        d.line([0, y + hero_h, W, y + hero_h], fill=_LINE, width=max(1, _px(1)))
    else:
        d.text((pad, y + _px(28)), money_safe(title), font=bold, fill=_INK)
    y += hero_h

    # The watermark goes over the body only: across the header band it would
    # muddy the wordmark, and the band is already unmistakably ours.
    body_box = (0, band_h, W, H - foot_h)
    body = img.crop(body_box).convert("RGBA")
    body.alpha_composite(_watermark((W, H - foot_h - band_h), mark_font))
    img.paste(body.convert("RGB"), body_box[:2])
    d = ImageDraw.Draw(img)

    # --- rows ----------------------------------------------------------------
    y += _px(20)
    for (k, v), h in zip(body_rows, heights):
        val = money_safe(v)
        d.text((pad, y + _px(14)), k, font=small, fill=_MUTED)
        if h > row_h:
            d.text((pad, y + _px(44)), val, font=bold, fill=_INK)
        else:
            d.text((W - pad - d.textlength(val, font=bold), y + _px(10)), val,
                   font=bold, fill=_INK)
        y += h
        d.line([pad, y, W - pad, y], fill=_LINE, width=max(1, _px(1)))

    # --- footer --------------------------------------------------------------
    fy = H - foot_h
    d.rectangle([0, fy, W, H], fill=_WASH)
    d.text((pad, fy + _px(26)), f"Reference  {reference}", font=small, fill=_INK)
    d.text((pad, fy + _px(58)),
           stamp or timezone.localtime().strftime("%d %b %Y, %I:%M %p"),
           font=small, fill=_MUTED)
    note = "Generated by Zitch · zitch.ng"
    d.text((W - pad - d.textlength(note, font=small), fy + _px(26)), note,
           font=small, fill=_BRAND_DEEP)
    keep = "Keep this receipt for your records."
    d.text((W - pad - d.textlength(keep, font=tiny), fy + _px(60)), keep,
           font=tiny, fill=_MUTED)

    buf = io.BytesIO()
    # subsampling=0 keeps chroma at full resolution: the default halves it, which
    # is invisible on a photo and visibly softens small text on teal.
    img.save(buf, format="JPEG", quality=94, subsampling=0, optimize=True)
    return buf.getvalue()


_STATUS_BADGE = {
    "success": ("✓ Successful", (226, 244, 240), _BRAND_DEEP),
    "pending": ("⏳ Pending", (255, 244, 219), (156, 108, 5)),
    "failed": ("✕ Failed", (252, 228, 226), (168, 45, 38)),
}


def render_statement_pdf(rows: list, *, balance: str, generated: str) -> bytes:
    """Return single-page PDF bytes for a transaction-history statement.

    `rows` = [{"date", "label", "amount", "sign", "status", "reference"}], newest
    first. Drawn with the same brand header/watermark as the receipt, then a
    row per transaction, so a customer forwarding the file gets something that
    reads as Zitch's, not a generic export.
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

    W = _px(820)
    pad = _px(56)
    band_h = _px(150)
    title_h = _px(90)
    row_h = _px(84)
    foot_h = _px(100)

    H = band_h + title_h + row_h * max(1, len(rows)) + foot_h

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
    d.text((x + _px(2), band_h // 2 + _px(6)), "TRANSACTION HISTORY", font=tiny,
           fill=(226, 246, 243))
    site = "zitch.ng"
    d.text((W - pad - d.textlength(site, font=small), band_h // 2 - _px(12)),
           site, font=small, fill=(226, 246, 243))

    # --- title / balance -------------------------------------------------
    y = band_h
    d.text((pad, y + _px(20)), f"Last {len(rows)} transaction{'s' if len(rows) != 1 else ''}",
           font=bold, fill=_INK)
    bal_line = f"Balance {money_safe(balance)}"
    d.text((W - pad - d.textlength(bal_line, font=small), y + _px(26)), bal_line,
           font=small, fill=_MUTED)
    d.line([0, y + title_h, W, y + title_h], fill=_LINE, width=max(1, _px(1)))
    y += title_h

    # The watermark goes over the body only, same reasoning as the receipt.
    body_box = (0, band_h + title_h, W, H - foot_h)
    body_h = body_box[3] - body_box[1]
    if body_h > 0:
        body = img.crop(body_box).convert("RGBA")
        body.alpha_composite(_watermark((W, body_h), mark_font))
        img.paste(body.convert("RGB"), body_box[:2])
    d = ImageDraw.Draw(img)

    # --- rows ------------------------------------------------------------
    for row in rows:
        amount_str = f"{row.get('sign', '')}{money_safe(row.get('amount', ''))}"
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
