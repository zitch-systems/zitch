"""The one email design system.

Every mail Zitch sends — verification codes, OTPs, credit/debit alerts, the
NDPR export — renders inside the same shell: brand header with the logo,
content, then a footer that signs off from the team, lists the real contact
points, and links the social profiles. Two templates had already grown side by
side with different palettes, different logos and different footers; a customer
comparing their alert against their OTP mail saw two products. One shell ends
that, and gives phishing a harder job — a fake has to reproduce the whole
identity, not whichever of our three layouts it found first.

Email HTML is written for the mail clients that exist, not the web: tables for
layout, inline styles restated on every element (clients strip <style> blocks),
and absolute image URLs — Gmail proxies remote images, and a hosted reference
keeps the message far below Gmail's clipping threshold. The logo is served from
the marketing site (Cloudflare Pages), which stays up independently of the API
host, so a Render cold start can never break the brand header.

Social links come from settings.SOCIAL_LINKS, env-driven and omitted when
unset — the same never-print-a-dead-link rule the WhatsApp menu follows. A
guessed handle is worse than none: handles we don't actually own are exactly
what a squatter turns into a phishing surface.
"""
from django.conf import settings

LOGO_URL = "https://zitch.ng/assets/brand/zitch-icon.png"

# Palette — mirrors the app/receipt brand. Restated inline at every use because
# email clients do not cascade.
_INK = "#12201f"
_DIM = "#5f7370"
_FAINT = "#8fa3a0"
_TEAL = "#0f9c93"
_TEAL_DARK = "#0a6b65"
_HEADER = "#0f2b29"
_GROUND = "#eef4f3"
_HAIRLINE = "#e4ecea"
_FONT = "font-family:Arial,Helvetica,sans-serif"


def _links() -> dict:
    return getattr(settings, "ZITCH_LINKS", {}) or {}


def _socials() -> dict:
    return getattr(settings, "SOCIAL_LINKS", {}) or {}


def _footer() -> str:
    """Team sign-off, the real contact points, the social row, the legal line.
    Every link is config-driven and omitted when blank."""
    L = _links()
    contact = []
    if L.get("WEBSITE"):
        contact.append(f'<a href="{L["WEBSITE"]}" style="color:{_TEAL_DARK};text-decoration:none">zitch.ng</a>')
    if L.get("APP"):
        contact.append(f'<a href="{L["APP"]}" style="color:{_TEAL_DARK};text-decoration:none">Get the app</a>')
    if L.get("SUPPORT_WA"):
        contact.append(f'<a href="https://wa.me/{L["SUPPORT_WA"]}" style="color:{_TEAL_DARK};text-decoration:none">WhatsApp support</a>')
    if L.get("SUPPORT_EMAIL"):
        contact.append(f'<a href="mailto:{L["SUPPORT_EMAIL"]}" style="color:{_TEAL_DARK};text-decoration:none">{L["SUPPORT_EMAIL"]}</a>')
    contact_row = (" &nbsp;&middot;&nbsp; ".join(contact)) if contact else ""

    socials = " &nbsp;&nbsp; ".join(
        f'<a href="{url}" style="color:{_DIM};text-decoration:none;font-weight:600">{name}</a>'
        for name, url in _socials().items()
    )

    parts = [
        f'<p style="margin:0 0 10px;color:{_DIM};font-size:13px;{_FONT}">'
        f'Sent with <span style="color:{_TEAL}">&#10084;</span> by the <b>Zitch team</b></p>'
    ]
    if contact_row:
        parts.append(f'<p style="margin:0 0 10px;font-size:12px;{_FONT}">{contact_row}</p>')
    if socials:
        parts.append(f'<p style="margin:0 0 12px;font-size:12px;{_FONT}">{socials}</p>')
    parts.append(
        f'<p style="margin:0;color:{_FAINT};font-size:11px;line-height:1.6;{_FONT}">'
        "Zitch Technologies Limited<br>"
        "You&rsquo;re receiving this because you have a Zitch account.</p>"
    )
    return "".join(parts)


def email_shell(content: str, *, preheader: str = "") -> str:
    """Wrap already-inline-styled content in the brand header, card and footer.

    `preheader` is the snippet line inboxes show beside the subject; hidden in
    the body itself. Without one, clients preview the first words of the header
    markup instead of anything useful.
    """
    pre = (
        f'<span style="display:none;max-height:0;overflow:hidden;mso-hide:all">{preheader}</span>'
        if preheader else ""
    )
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:{_GROUND}">{pre}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_GROUND};padding:28px 12px">
<tr><td align="center">
<table role="presentation" width="460" cellpadding="0" cellspacing="0"
       style="max-width:460px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid {_HAIRLINE}">
  <tr><td style="background:{_HEADER};padding:20px 28px">
    <img src="{LOGO_URL}" alt="Zitch" width="34" height="34"
         style="vertical-align:middle;border-radius:9px">
    <span style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:.4px;
                 vertical-align:middle;margin-left:10px;{_FONT}">Zitch</span>
  </td></tr>
  {content}
  <tr><td style="background:#f6faf9;border-top:1px solid {_HAIRLINE};padding:20px 28px;text-align:center">
    {_footer()}
  </td></tr>
</table>
<p style="margin:14px 0 0;color:{_FAINT};font-size:11px;{_FONT}">&copy; Zitch</p>
</td></tr></table></body></html>"""


def branded_message(title: str, intro: str, *, code: str = "", note: str = "") -> str:
    """A message email: heading, intro, an optional prominent one-time code, an
    optional fine-print note. Serves every verification and OTP mail, so a code
    from the app and a code from WhatsApp are visibly the same product."""
    code_box = (
        f'<div style="font-size:30px;font-weight:700;letter-spacing:9px;color:{_TEAL_DARK};'
        f'background:{_GROUND};border-radius:12px;padding:18px 10px;margin:20px 0 0;{_FONT}">{code}</div>'
        if code else ""
    )
    note_p = (
        f'<p style="margin:18px 0 0;color:{_FAINT};font-size:12px;line-height:1.6;{_FONT}">{note}</p>'
        if note else ""
    )
    content = (
        f'<tr><td style="padding:30px 28px 26px;text-align:center;{_FONT}">'
        f'<h1 style="margin:0 0 8px;color:{_INK};font-size:20px;{_FONT}">{title}</h1>'
        f'<p style="margin:0;color:{_DIM};font-size:14px;line-height:1.6;{_FONT}">{intro}</p>'
        f"{code_box}{note_p}"
        "</td></tr>"
    )
    return email_shell(content, preheader=intro)
