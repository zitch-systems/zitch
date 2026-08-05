"""The diagnostics, in the browser.

Everything ops needs to answer "why isn't this working" already existed — the
go-live preflight, the Wema probe, the bank-code comparison, the callback config,
the VTU balance, the SMS delivery test. All of it was reachable only with an
`Authorization: Bearer` header, which in practice means curl, which means a
terminal. An operator working from the Render dashboard and a browser could reach
exactly one of them (`/healthz`, the public one) and was otherwise blind.

So this is the same information rendered as a page inside Django admin. It adds no
new capability and no new data: every section calls the same function the HTTP
endpoint calls.

AUTH is the admin's own — `admin.site.admin_view` requires an authenticated staff
session, which is a stronger and better-audited gate than a shared bearer token
pasted between operators. There is deliberately no token here to leak.

The SMS test is the one action with a cost and a side effect (it sends a real
message and spends Termii credit), so it is POST-only behind its own button with
an explicit number — never something a page load can trigger.
"""
import json

from django.conf import settings
from django.http import HttpResponse
from django.utils.html import escape


def _section(title, body, *, note=""):
    return (f"<h2 style='margin:1.6rem 0 .3rem;font:600 15px/1.3 system-ui'>{escape(title)}</h2>"
            + (f"<p style='margin:.2rem 0 .5rem;color:#555;font:13px/1.45 system-ui'>{note}</p>"
               if note else "")
            + "<pre style='background:#f6f8fa;border:1px solid #dde;border-radius:6px;"
              "padding:10px;overflow:auto;font:12px/1.45 ui-monospace,monospace;"
              f"white-space:pre-wrap'>{escape(body)}</pre>")


def _json(value):
    try:
        return json.dumps(value, indent=2, default=str, ensure_ascii=False)
    except (TypeError, ValueError) as exc:      # a probe returning something odd
        return f"(unrenderable: {exc})"


def _safe(label, fn):
    """Run one probe. A failing probe must not take the whole page down — the page
    exists precisely for when something is broken."""
    try:
        return fn()
    except Exception as exc:                    # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "probe": label}


def diagnostics_view(request):
    import io

    from django.core.management import call_command

    from utility import wema
    from utility.providers import sms_live, sms_probe, vtu_live

    parts = []
    sent = ""

    # --- the one action with a side effect -----------------------------------
    if request.method == "POST" and request.POST.get("sms_to"):
        phone = "".join(c for c in request.POST["sms_to"] if c.isdigit())[:14]
        result = _safe("sms", lambda: sms_probe(phone))
        sent = _section("SMS delivery test — result", _json(result),
                        note="A real message was sent (or attempted). "
                             "<b>Accepted is not delivered</b>: if this says accepted but no "
                             "handset rings, the sender ID is not DND-whitelisted.")

    # --- readiness ------------------------------------------------------------
    buf = io.StringIO()
    ready = True
    try:
        call_command("wema_preflight", stdout=buf, stderr=buf)
    except SystemExit:
        ready = False
    except Exception as exc:                    # noqa: BLE001
        ready = False
        buf.write(f"\npreflight could not run: {type(exc).__name__}: {exc}")
    parts.append(_section(
        f"Go-live preflight — {'READY' if ready else 'NOT READY'}", buf.getvalue(),
        note="The same <code>manage.py wema_preflight</code>. GATE lines block real money; "
             "the rest degrade a non-money feature."))

    # --- rails ----------------------------------------------------------------
    parts.append(_section("Wema / ALAT", _json(_safe("wema", wema.wema_diagnostics)),
                          note="<code>status: simulation</code> means every Wema call is mocked "
                               "regardless of the keys — clear <code>WEMA_SIMULATION</code> for "
                               "real calls."))
    parts.append(_section(
        "SMS (Termii)",
        _json({"sms_live": _safe("sms_live", sms_live),
               "sender_id": settings.TERMII.get("SENDER_ID", ""),
               "channel": settings.TERMII.get("CHANNEL", ""),
               "base_url": settings.TERMII.get("BASE_URL", "")}),
        note="<code>sms_live: false</code> means mock mode — nothing is sent and the code is "
             "not logged anywhere, so nobody receives an OTP."))
    parts.append(_section("VTU.ng", _json({"vtu_live": _safe("vtu", vtu_live)}),
                          note="Airtime/data/bills. A purchase fails on an empty provider "
                               "wallet however correct the code is."))

    # --- the SMS form ---------------------------------------------------------
    form = (
        "<h2 style='margin:1.6rem 0 .3rem;font:600 15px/1.3 system-ui'>Send a test SMS</h2>"
        "<p style='margin:.2rem 0 .5rem;color:#555;font:13px/1.45 system-ui'>"
        "Sends a real message and spends Termii credit, so it only ever happens on this "
        "button — never on a page load.</p>"
        "<form method='post' style='margin:0 0 .6rem'>"
        f"<input type='hidden' name='csrfmiddlewaretoken' value='{escape(request.META.get('CSRF_COOKIE', ''))}'>"
        "<input name='sms_to' placeholder='08030000000' "
        "style='padding:7px 9px;border:1px solid #bbb;border-radius:6px;font:13px system-ui'>"
        "<button type='submit' style='margin-left:.4rem;padding:7px 14px;border:0;"
        "border-radius:6px;background:#0FA295;color:#fff;font:600 13px system-ui;"
        "cursor:pointer'>Send</button></form>")

    body = (
        "<div style='max-width:900px;margin:0 auto;padding:1.2rem 1rem 3rem'>"
        "<h1 style='font:700 20px/1.3 system-ui;margin:0 0 .2rem'>Diagnostics</h1>"
        "<p style='color:#555;font:13px/1.5 system-ui;margin:.2rem 0 1rem'>"
        "The same checks as <code>/preflight</code> and the <code>*-diagnose</code> endpoints, "
        "without needing a terminal. Read-only apart from the SMS test below. "
        "Secrets are never shown.</p>"
        + sent + "".join(parts) + form +
        "<p style='color:#777;font:12px/1.5 system-ui;margin-top:1.4rem'>"
        "Bank codes: <a href='../transfers/bank/'>Banks</a> → “Sync bank codes from the payout "
        "rail”. Wallets: <a href='../wallet/wallet/'>Wallets</a> → reconnect or clear a funding "
        "account. Failed payouts: <a href='../wallet/transaction/?transaction_status__exact=Failed'>"
        "Transactions</a> → “Why it failed”.</p></div>")
    return HttpResponse(body)
