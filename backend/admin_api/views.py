"""Operator portal API (`/api/admin/`).

Read endpoints aggregate the real Django models into the shape the React portal
(`static/console/portal/*.jsx`) consumes; write endpoints mutate state behind
the server-side RBAC matrix and append to the immutable AuditLog. The portal is
served same-origin from `/portal/`, so these are plain bearer-token JSON calls
(no cookies / CSRF).
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.db.models import Q, Sum
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from common.http import fail, ok, resolve_token
from common.ratelimit import ratelimit

from .auth import ROLES, staff_endpoint, staff_role, audit, CAN

# Capabilities surfaced to the portal so its UI can gate consistently with the
# server (the server remains the real gate via @staff_endpoint perm=...).
PERMS_MATRIX = [
    {"perm": "View dashboards & logs", "super_admin": True, "finance": True, "support": True, "read_only": True},
    {"perm": "Reply / handover WhatsApp chats", "super_admin": True, "finance": False, "support": True, "read_only": False},
    {"perm": "Send broadcasts", "super_admin": True, "finance": False, "support": True, "read_only": False},
    {"perm": "Refund / credit / requery / flag transactions", "super_admin": True, "finance": True, "support": False, "read_only": False},
    {"perm": "Edit FX margin & corridors", "super_admin": True, "finance": True, "support": False, "read_only": False},
    {"perm": "Freeze users / review KYC", "super_admin": True, "finance": True, "support": False, "read_only": False},
    {"perm": "AI kill switch & system settings", "super_admin": True, "finance": False, "support": False, "read_only": False},
    {"perm": "Manage team & roles", "super_admin": True, "finance": False, "support": False, "read_only": False},
]

# Known runtime settings + human descriptions (merged with live SystemSetting rows).
SETTING_DEFS = [
    ("ai_enabled_global", "true", "Master switch for the WhatsApp AI intent layer. Off ⇒ channel is fully menu-driven."),
    ("fx_margin_bps", "60", "Margin (basis points) added over the provider rate on every conversion quote."),
    ("fx_quote_ttl_seconds", "60", "How long a conversion quote stays valid. Expired quotes are never settled."),
    ("wa_pin_max_attempts", "1", "Wrong-PIN attempts before a WhatsApp flow is cancelled."),
    ("cny_settlement_enabled", "false", "CNY corridor — quote/display only until a settlement partner is live."),
    ("broadcast_marketing_optin_only", "true", "Marketing templates only reach users with marketing_opt_in = true."),
]

# Type + range constraints per setting key. A runtime setting drives money math
# (fx_margin_bps) and security behaviour (wa_pin_max_attempts, the AI kill
# switch), so an operator can't write a value the consumers can't parse — a
# non-numeric fx_margin_bps would raise mid-quote, and an absurd
# wa_pin_max_attempts would neuter the WhatsApp PIN throttle.
_BOOL_SETTINGS = {"ai_enabled_global", "cny_settlement_enabled", "broadcast_marketing_optin_only"}
_INT_SETTINGS = {
    "fx_margin_bps": (0, 1000),          # ≤10% margin
    "fx_quote_ttl_seconds": (5, 3600),   # 5s – 1h quote validity
    "wa_pin_max_attempts": (1, 10),      # keep the throttle meaningful
}


def _clean_setting_value(key: str, value):
    """Coerce + validate a SystemSetting write by key type.

    Returns ``(cleaned_str, None)`` on success or ``(None, error)`` — bool keys
    normalise to ``"true"``/``"false"``; int keys must parse and sit in range;
    any other allow-listed key is stored as its trimmed string."""
    if key in _BOOL_SETTINGS:
        s = str(value).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return "true", None
        if s in ("false", "0", "no", "off"):
            return "false", None
        return None, "Value must be true or false"
    if key in _INT_SETTINGS:
        lo, hi = _INT_SETTINGS[key]
        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            return None, "Value must be a whole number"
        if not lo <= n <= hi:
            return None, f"Value must be between {lo} and {hi}"
        return str(n), None
    return str(value).strip(), None


def _ms(dt) -> int | None:
    """Epoch milliseconds (the portal revives these into JS Dates)."""
    return int(dt.timestamp() * 1000) if dt else None


def _num(d) -> float:
    try:
        return float(d)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# Serializers — map real models to the portal's ZADM shape.
# --------------------------------------------------------------------------- #
def _kyc_label(u) -> str:
    if u.face_verified:
        return "face"
    if u.nin_verified:
        return "nin"
    if u.bvn_verified:
        return "bvn"
    return "pending"


def _wallets_by_user(user_ids=None) -> dict:
    """Balance map keyed by user id. Pass ``user_ids`` to scope the query —
    the single-user / search endpoints were scanning every wallet row in the
    database to serve a handful of rows."""
    from wallet.models import CurrencyWallet, Wallet

    wallets = Wallet.objects.all() if user_ids is None else Wallet.objects.filter(user_id__in=user_ids)
    cwallets = (CurrencyWallet.objects.all() if user_ids is None
                else CurrencyWallet.objects.filter(user_id__in=user_ids))
    out: dict[int, dict] = {}
    for w in wallets:
        out.setdefault(w.user_id, {"NGN": 0, "USD": 0, "GBP": 0, "CAD": 0})
        out[w.user_id]["NGN"] = _num(w.balance)
    for cw in cwallets:
        out.setdefault(cw.user_id, {"NGN": 0, "USD": 0, "GBP": 0, "CAD": 0})
        out[cw.user_id][cw.currency] = _num(cw.balance)
    return out


def _wa_by_user(user_ids=None) -> dict:
    from whatsapp.models import WhatsAppLink

    links = (WhatsAppLink.objects.all() if user_ids is None
             else WhatsAppLink.objects.filter(user_id__in=user_ids))
    out: dict[int, dict] = {}
    for link in links:
        # Prefer an active link for the user's headline WA status.
        cur = out.get(link.user_id)
        if cur is None or link.status == WhatsAppLink.ACTIVE:
            out[link.user_id] = {
                "wa": link.status if link.status == WhatsAppLink.ACTIVE else "pending",
                "aiEnabled": link.ai_enabled,
                "marketingOptIn": link.marketing_opt_in,
            }
    return out


def _user_row(u, wallets, wa) -> dict:
    w = wa.get(u.id, {})
    return {
        "id": f"u_{u.id}",
        "uid": u.id,
        "name": (u.get_full_name() or u.username or u.phone or "—").strip(),
        "phone": u.phone or "",
        "email": u.email or "",
        "kyc": _kyc_label(u),
        "tier": u.tier,
        "status": "active" if u.is_active else "frozen",
        "joined": u.date_joined.strftime("%b %Y") if u.date_joined else "—",
        "wa": w.get("wa", "none"),
        "aiEnabled": w.get("aiEnabled", False),
        "marketingOptIn": w.get("marketingOptIn", False),
        "wallets": wallets.get(u.id, {"NGN": 0, "USD": 0, "GBP": 0, "CAD": 0}),
    }


_TYPE_KEYWORDS = [
    ("transfer", "transfer"), ("top-up", "fund"), ("funding", "fund"), ("fund", "fund"),
    ("convert", "fx"), ("airtime", "airtime"), ("data", "data"), ("cable", "cable"),
    ("tv", "cable"), ("electric", "electricity"), ("card", "card"), ("loan", "loan"),
    ("sav", "savings"), ("bet", "betting"), ("exam", "exams"),
]


def _txn_type(service: str) -> str:
    s = (service or "").lower()
    for kw, t in _TYPE_KEYWORDS:
        if kw in s:
            return t
    return "other"


_STATUS_MAP = {"Successful": "success", "Pending": "pending", "Failed": "failed"}


def _txn_row(t, name_by_id) -> dict:
    meta = t.meta or {}
    status = "flagged" if meta.get("flagged") else _STATUS_MAP.get(t.transaction_status, "pending")
    signed = _num(t.amount) if t.direction == t.IN else -_num(t.amount)
    return {
        "id": t.reference,
        "uid": t.user_id,
        "user": name_by_id.get(t.user_id, f"user {t.user_id}"),
        "type": _txn_type(t.service),
        "channel": meta.get("channel", "app"),
        "desc": t.service,
        "amt": signed,
        "cur": t.currency or "NGN",
        "fee": _num(meta.get("fee", 0)),
        "status": status,
        "time": _ms(t.created),
        # Only provider-timeout PENDING purchases can be requeried (same rule
        # as the reconcile cron / the ops portal).
        "canRequery": bool(t.transaction_status == t.PENDING and meta.get("reconcile")),
        "flagged": bool(meta.get("flagged")),
    }


def _corridor_enabled(ccy: str) -> bool:
    """Live corridor state: the same SystemSetting the FX settlement path
    checks (wallet.forex). CNY is settlement-blocked in code regardless."""
    from whatsapp.models import SystemSetting

    if ccy == "CNY":
        return False
    return SystemSetting.get(f"fx_corridor_{ccy.lower()}_enabled", "true") != "false"


def _loan_row(l, name) -> dict:
    from loans.models import Loan

    overdue = l.status == Loan.ACTIVE and l.due_date and l.due_date < timezone.now()
    return {
        "id": f"ln_{l.id}", "ref": l.reference, "user": name, "amt": _num(l.principal),
        "tenor": f"{l.tenure_days} days", "rate": "4.5%/mo",
        "status": ("overdue" if overdue else l.status),
        "due": l.due_date.strftime("%b %d, %Y") if l.due_date else "—",
        "outstanding": _num(l.outstanding),
    }


def _saving_row(s, name) -> dict:
    return {
        "id": f"sv_{s.id}", "user": name, "principal": _num(s.principal),
        "rate": f"{_num(s.rate) * 100:.0f}% p.a.", "start": s.created.strftime("%b %d, %Y"),
        "maturity": s.matures_at.strftime("%b %d, %Y") if s.matures_at else "—",
        "status": ("paid" if s.paid_out else s.status), "payout": _num(s.maturity_value),
    }


def _card_row(c, name) -> dict:
    return {
        "id": f"cd_{c.id}", "cid": c.id, "user": name, "last4": c.last4,
        "cur": "NGN", "bal": _num(c.balance), "status": c.status, "spend30": 0,
    }


def _audit_row(a) -> dict:
    return {
        "actor": a.actor_id or a.actor_type, "role": a.actor_type, "action": a.action,
        "target": a.target, "before": a.before, "after": a.after, "t": _ms(a.created),
    }


_WEBHOOK_SOURCES = {"whatsapp": "Meta WA", "vtung": "VTU.ng", "mono": "Mono"}


def _webhook_rows(limit=40) -> list:
    """Inbound-callback history from the audit trail (webhook.* entries are
    only written after signature verification, hence sig=verified)."""
    from whatsapp.models import AuditLog

    rows = []
    for a in AuditLog.objects.filter(action__startswith="webhook.")[:limit]:
        src_key = a.action.split(".", 1)[1] if "." in a.action else a.action
        note = ""
        if isinstance(a.after, dict) and a.after:
            note = " · ".join(f"{k}: {v}" for k, v in list(a.after.items())[:3])
        rows.append({
            "src": _WEBHOOK_SOURCES.get(src_key, src_key.replace("_", " ").title()),
            "event": a.action, "ref": a.target, "sig": "verified",
            "code": 200, "time": _ms(a.created), "note": note,
        })
    return rows


_RECON_RUNS = {"recon.vtu_run": "zitch-reconcile-vtu", "recon.maturities_run": "zitch-maturities",
               "recon.wema_run": "zitch-reconcile-wema"}


def _recon_rows(limit=20) -> list:
    """Reconciliation history from the audit trail (recon.* entries)."""
    from whatsapp.models import AuditLog

    rows = []
    for a in AuditLog.objects.filter(action__startswith="recon.")[:limit]:
        after = a.after if isinstance(a.after, dict) else {}
        fixed = int(after.get("settled") or after.get("paid_out") or 0)
        rows.append({
            "run": _RECON_RUNS.get(a.action, a.action),
            "time": a.created.strftime("%b %d, %H:%M"),
            "checked": int(after.get("checked", fixed)), "mismatches": fixed,
            "fixed": fixed, "status": "done",
            "note": f"by {a.actor_id}" if a.actor_type == "admin" and a.actor_id else "",
        })
    return rows


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
# csrf_exempt is required here as on every other portal endpoint (the portal is
# a bearer-token API with no session cookie — CSRF doesn't apply, and without
# the exemption Django's middleware 403s the login POST itself). The other
# endpoints inherit it from @staff_endpoint; login authenticates, so it can't.
@csrf_exempt
@ratelimit("admin_login", limit=10, window=300)
def login(request):
    """POST /api/admin/login {username|email, password} -> {token, role, name, email}

    Requires ``is_staff``. Reuses the app's AccessToken so the same TTL/expiry
    rules apply. Generic error on bad credentials; a clear (but non-enumerating)
    403 when valid creds belong to a non-staff account.
    """
    if request.method != "POST":
        return fail("Method not allowed", status=405)
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return fail("Invalid JSON body", status=400)

    from accounts.models import AccessToken, User
    from common.http import mask_pii
    from common.ratelimit import clear_login_failures, login_locked, note_login_failure
    from whatsapp.ops import record_audit

    ident = (data.get("username") or data.get("email") or "").strip()
    password = data.get("password") or ""
    if not ident or not password:
        return fail("Username and password are required")
    # Per-account lockout on top of the per-IP ratelimit: a rotating-IP brute
    # force against one operator account is capped here.
    if login_locked("admin", ident):
        record_audit("admin.login_locked", target=mask_pii(ident), actor_type="system")
        return fail("Too many failed attempts. Try again later.", status=429, code="locked")
    user = User.objects.filter(Q(username__iexact=ident) | Q(email__iexact=ident)).first()
    if user is None or not user.check_password(password):
        note_login_failure("admin", ident)
        # Log the masked identifier only — never the raw email/phone, so the
        # audit trail can't be harvested as a plaintext account list.
        record_audit("admin.login_failed", target=mask_pii(ident), actor_type="system")
        return fail("Incorrect credentials", status=401)
    if not (user.is_staff and user.is_active):
        record_audit("admin.login_denied", actor=user, target=mask_pii(ident))
        return fail("This account does not have operator access", status=403)
    # Second factor. Checked AFTER the password and the staff gate, so a wrong
    # password and a missing code are indistinguishable from outside — otherwise this
    # endpoint would confirm which identifiers are real operator accounts.
    mfa_error = _mfa_login_error(user, data.get("code") or data.get("mfa_code") or "")
    if mfa_error is not None:
        note_login_failure("admin", ident)
        record_audit("admin.login_mfa_failed", target=mask_pii(ident), actor_type="system")
        return mfa_error
    clear_login_failures("admin", ident)
    # Admin-scoped, short-lived token (ADMIN_TOKEN_TTL_HOURS): it resolves only on
    # staff endpoints and never on the mobile app surface.
    token = AccessToken.issue(user, scope=AccessToken.ADMIN)
    record_audit("admin.login", actor=user, target=user.username)
    return ok(token=token.key, role=staff_role(user),
              name=(user.get_full_name() or user.username), email=user.email)


@staff_endpoint(methods=("POST",))
def logout(request):
    """POST /api/admin/logout — revoke the presented staff token."""
    from accounts.models import AccessToken

    AccessToken.objects.filter(key=AccessToken._hash(resolve_token(request))).delete()
    return ok(message="Signed out")


@staff_endpoint(methods=("GET",))
def me(request):
    return ok(role=request.role, name=(request.staff.get_full_name() or request.staff.username),
              email=request.staff.email, roles=ROLES, can=sorted(CAN.get(request.role, set())))


# --------------------------------------------------------------------------- #
# Bootstrap — one call returns every collection the portal renders.
# --------------------------------------------------------------------------- #
@staff_endpoint(methods=("GET",))
def bootstrap(request):
    from cards.models import VirtualCard
    from loans.models import Loan
    from savings.models import FixedSave
    from wallet.forex import QUOTE_ONLY, SETTLEABLE
    from wallet.models import Transaction, Wallet
    from whatsapp.models import (AuditLog, Broadcast, ConversationState, SystemSetting,
                                 WaMessageLog, WhatsAppLink)

    User = request.staff.__class__

    users_qs = list(User.objects.all().order_by("-date_joined")[:300])
    name_by_id = {u.id: (u.get_full_name() or u.username or u.phone or f"user {u.id}").strip() for u in User.objects.all()}
    wallets = _wallets_by_user()
    wa = _wa_by_user()
    users = [_user_row(u, wallets, wa) for u in users_qs]

    txns_qs = list(Transaction.objects.select_related(None).all()[:150])
    txns = [_txn_row(t, name_by_id) for t in txns_qs]

    # --- KYC queue: users mid-verification (a started-but-incomplete tier path) ---
    kycq = []
    for u in User.objects.filter(Q(bvn_verified=True) | Q(nin_verified=True) | Q(bvn_hash__gt="") | Q(nin_hash__gt="")).order_by("-date_joined")[:50]:
        if u.face_verified and u.nin_verified and u.bvn_verified:
            continue  # fully verified, nothing to review
        nxt = min(u.tier + 1, 3)
        pending_type = "face" if (u.bvn_verified and u.nin_verified) else ("nin" if u.bvn_verified else "bvn")
        kycq.append({
            "user": (u.get_full_name() or u.username or u.phone or "—").strip(), "id": f"u_{u.id}", "uid": u.id,
            "type": pending_type, "submitted": _ms(u.date_joined),
            "note": f"BVN {'✓' if u.bvn_verified else '—'} · NIN {'✓' if u.nin_verified else '—'} · Face {'✓' if u.face_verified else '—'}",
            "tier": f"{u.tier} → {nxt}",
        })

    # --- WhatsApp conversations: state + last few messages + linked identity ---
    convos = []
    link_user_by_msisdn = {l.wa_msisdn: l for l in WhatsAppLink.objects.exclude(wa_msisdn="")}
    for cs in ConversationState.objects.all().order_by("-updated")[:40]:
        link = link_user_by_msisdn.get(cs.msisdn)
        msgs = list(WaMessageLog.objects.filter(msisdn=cs.msisdn).order_by("-created")[:20])[::-1]
        convos.append({
            "msisdn": cs.msisdn,
            "user": name_by_id.get(link.user_id) if link else "(unlinked)",
            "status": cs.status, "aiEnabled": cs.ai_enabled,
            "agent": (cs.assigned_agent.get_full_name() or cs.assigned_agent.username) if cs.assigned_agent else None,
            "last": _ms(cs.updated),
            "msgs": [{"dir": m.direction, "text": m.text, "t": _ms(m.created),
                      "intent": m.intent_json or None, "flagged": m.flagged} for m in msgs],
        })

    broadcasts = [{
        "id": f"bc_{b.id}", "template": b.template_name, "category": b.category, "status": b.status,
        "created": b.created.strftime("%b %d, %Y"), "by": (b.created_by.email if b.created_by else "system"),
        "queued": b.count_queued, "sent": b.count_sent, "delivered": b.count_delivered,
        "read": b.count_read, "failed": b.count_failed, "unknown": b.count_unknown,
    } for b in Broadcast.objects.all()[:50]]

    audit_rows = [_audit_row(a) for a in AuditLog.objects.all()[:100]]

    loans = [_loan_row(l, name_by_id.get(l.user_id, "—")) for l in Loan.objects.all()[:80]]
    savings = [_saving_row(s, name_by_id.get(s.user_id, "—")) for s in FixedSave.objects.all()[:80]]
    # Cards are funded from the NGN wallet (see cards.models.VirtualCard).
    cards = [_card_row(c, name_by_id.get(c.user_id, "—")) for c in VirtualCard.objects.all()[:80]]

    # --- Overview KPIs (real aggregates) ---
    now = timezone.now()
    day_ago = now - timedelta(hours=24)
    total_ngn = Wallet.objects.aggregate(s=Sum("balance"))["s"] or Decimal("0")
    txn_24h = Transaction.objects.filter(created__gte=day_ago).count()
    vol_24h = Transaction.objects.filter(created__gte=day_ago, direction=Transaction.OUT).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    active_users = User.objects.filter(is_active=True).count()

    # 14-day outflow volume (₦m/day) for the bar chart.
    volume_14d = []
    for i in range(13, -1, -1):
        start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        v = Transaction.objects.filter(created__gte=start, created__lt=end, direction=Transaction.OUT).aggregate(s=Sum("amount"))["s"] or Decimal("0")
        volume_14d.append(round(_num(v) / 1_000_000, 1))

    kpis = {
        "users": active_users, "txn24h": txn_24h, "vol24h": _num(vol_24h),
        "ngn_liability": _num(total_ngn), "pending_kyc": len(kycq),
        "flagged": Transaction.objects.filter(meta__flagged=True).count(),
        "active_loans": Loan.objects.filter(status=Loan.ACTIVE).count(),
        "wa_links": WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE).count(),
        "wa_optin": WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE, marketing_opt_in=True).count(),
        "matured_due": FixedSave.objects.filter(paid_out=False, status=FixedSave.ACTIVE,
                                                matures_at__lte=now).count(),
    }

    # --- FX corridors: live provider rate + margin-derived customer rate, and
    # the corridor state from the same SystemSetting the settlement path reads
    # (the static SETTLEABLE set says what *can* settle; the setting says what
    # is currently *enabled*) ---
    from utility.providers import fx_quote

    margin_bps = int(SystemSetting.get("fx_margin_bps", "60") or "60")
    flags = {"USD": "🇺🇸", "GBP": "🇬🇧", "CAD": "🇨🇦", "CNY": "🇨🇳"}
    rates = []
    for c in ["USD", "GBP", "CAD", "CNY"]:
        q = fx_quote(c, "NGN", Decimal("1"))
        provider = _num(q.get("rate", 0)) if q.get("success") else 0.0
        customer = provider * (1 + margin_bps / 10000.0)
        rates.append({
            "pair": f"NGN/{c}", "flag": flags.get(c, "🏳️"), "margin": margin_bps,
            "provider": provider, "customer": customer,
            "settle": (c in SETTLEABLE) and _corridor_enabled(c), "vol24": 0,
        })

    # Float = platform liability we actually hold per currency (real).
    from wallet.models import CurrencyWallet
    float_rows = [{"cur": "NGN", "sym": "₦", "bal": _num(total_ngn), "provider": "Wema"}]
    for c in ["USD", "GBP", "CAD"]:
        bal = CurrencyWallet.objects.filter(currency=c).aggregate(s=Sum("balance"))["s"] or Decimal("0")
        float_rows.append({"cur": c, "sym": {"USD": "$", "GBP": "£", "CAD": "C$"}[c], "bal": _num(bal), "provider": "Fincra"})

    # Providers: live vs mock from the same source the /healthz probe uses.
    from django.conf import settings as dj_settings
    from utility.providers import _prembly_live, payout_live, vtu_live
    from whatsapp.providers import wa_live

    def _st(live):
        return "operational" if live else "degraded"

    fincra_live = bool(dj_settings.FINCRA.get("SECRET_KEY"))
    providers = [
        {"name": "Wema", "role": "Funding · payouts · KYC", "status": _st(payout_live()), "uptime": "—"},
        {"name": "VTU.ng", "role": "Airtime · data · bills", "status": _st(vtu_live()), "uptime": "—"},
        {"name": "Fincra", "role": "FX rates & settlement", "status": _st(fincra_live), "uptime": "—"},
        {"name": "Meta WhatsApp", "role": "Chat channel", "status": _st(wa_live()), "uptime": "—"},
        {"name": "Prembly", "role": "KYC (face · address · ID)", "status": _st(_prembly_live()), "uptime": "—"},
    ]

    settings_rows = []
    for key, default, desc in SETTING_DEFS:
        settings_rows.append({"key": key, "value": SystemSetting.get(key, default), "desc": desc})

    team = [{
        "name": (u.get_full_name() or u.username), "email": u.email or u.username, "role": staff_role(u),
    } for u in User.objects.filter(is_staff=True).order_by("-is_superuser", "username")[:50]]

    return ok(
        users=users, txns=txns, convos=convos, broadcasts=broadcasts, audit=audit_rows,
        rates=rates, float=float_rows, providers=providers, volume_14d=volume_14d,
        loans=loans, savings=savings, cards=cards, kycq=kycq,
        webhooks=_webhook_rows(), recons=_recon_rows(),
        team=team, perms=PERMS_MATRIX, settings=settings_rows, kpis=kpis,
        meta={"role": request.role, "name": (request.staff.get_full_name() or request.staff.username)},
    )


# --------------------------------------------------------------------------- #
# Write actions — each enforces a capability and appends to the AuditLog.
# --------------------------------------------------------------------------- #
def _get_user(uid):
    """Resolve a CUSTOMER (non-staff) user for an operations write action.

    Scoped to is_staff=False (mirroring the portal app) so a back-office operator
    can't freeze/credit/KYC-flip another operator — or their OWN account, since
    operators are staff. Keeps these endpoints to the customer base they target.
    """
    from accounts.models import User

    try:
        return User.objects.get(pk=int(uid), is_staff=False)
    except (User.DoesNotExist, TypeError, ValueError):
        return None


@staff_endpoint(methods=("POST",), perm="settings")
def setting_update(request):
    """POST {key, value} — flip a runtime SystemSetting (incl. the AI kill switch)."""
    from whatsapp.models import SystemSetting

    key = (request.data.get("key") or "").strip()
    value = request.data.get("value")
    allowed = {k for k, _, _ in SETTING_DEFS}
    if key not in allowed:
        return fail("Unknown setting key", status=400)
    cleaned, err = _clean_setting_value(key, value)
    if err:
        return fail(err, status=400)
    before = SystemSetting.get(key, "")
    SystemSetting.set(key, cleaned)
    audit(request, "settings.update", target=key, before={"value": before}, after={"value": cleaned})
    return ok(success=True, key=key, value=cleaned)


@staff_endpoint(methods=("POST",), perm="users")
def user_status(request):
    """POST {uid, status: active|frozen} — freeze/unfreeze a user (is_active)."""
    u = _get_user(request.data.get("uid"))
    if u is None:
        return fail("User not found", status=404)
    status = (request.data.get("status") or "").strip()
    if status not in ("active", "frozen"):
        return fail("status must be active or frozen")
    before = "active" if u.is_active else "frozen"
    u.is_active = status == "active"
    u.save(update_fields=["is_active"])
    # A freeze must take effect NOW, not at token TTL: revoke the user's live
    # sessions so a frozen account can't keep transacting on an existing token.
    revoked = 0
    if status == "frozen":
        revoked = u.tokens.all().delete()[0]
    audit(request, "user.freeze" if status == "frozen" else "user.unfreeze",
          target=f"u_{u.id} ({u.get_full_name() or u.username})",
          before={"status": before}, after={"status": status, "sessions_revoked": revoked})
    return ok(success=True, uid=u.id, status=status, sessions_revoked=revoked)


@staff_endpoint(methods=("POST",), perm="users")
def kyc_review(request):
    """POST {uid, decision: approve|reject, type: bvn|nin|face}

    Approving marks the relevant verification flag and recomputes the tier
    (face also upgrades nothing but unlocks large transfers). Rejecting a
    bvn/nin review clears the unverified submission (the user resubmits);
    rejecting face is audit-only. Every decision is audited.
    """
    u = _get_user(request.data.get("uid"))
    if u is None:
        return fail("User not found", status=404)
    decision = (request.data.get("decision") or "approve").strip()
    kind = (request.data.get("type") or "").strip()
    if kind not in ("bvn", "nin", "face"):
        return fail("type must be bvn, nin or face")
    before = {"tier": u.tier, "bvn": u.bvn_verified, "nin": u.nin_verified, "face": u.face_verified}
    if decision == "approve":
        if kind == "bvn":
            u.bvn_verified = True
        elif kind == "nin":
            u.nin_verified = True
        else:
            u.face_verified = True
        u.recompute_tier()
        u.save(update_fields=["bvn_verified", "nin_verified", "face_verified", "tier"])
    elif kind in ("bvn", "nin"):
        # Reject clears the UNVERIFIED submitted identifier so the user leaves
        # the review queue and must resubmit — previously a pure-audit no-op, so
        # a rejected row reappeared on every queue load. A verified identity is
        # never revoked here (face has no stored artifact to clear).
        if kind == "bvn" and u.bvn_hash and not u.bvn_verified:
            u.bvn_hash = u.bvn_last4 = ""
            u.save(update_fields=["bvn_hash", "bvn_last4"])
        elif kind == "nin" and u.nin_hash and not u.nin_verified:
            u.nin_hash = u.nin_last4 = ""
            u.save(update_fields=["nin_hash", "nin_last4"])
    after = {"tier": u.tier, "bvn": u.bvn_verified, "nin": u.nin_verified, "face": u.face_verified}
    audit(request, f"kyc.{decision}", target=f"u_{u.id} ({kind})", before=before, after=after)
    return ok(success=True, uid=u.id, tier=u.tier, decision=decision)


@staff_endpoint(methods=("POST",), perm="money")
def txn_flag(request):
    """POST {ref, flagged: bool} — flag/unflag a transaction for compliance review.

    Flagging is an annotation in ``meta`` (the amount/direction/status of a
    settled ledger row stay immutable)."""
    from wallet.models import Transaction

    ref = (request.data.get("ref") or "").strip()
    flagged = bool(request.data.get("flagged", True))
    t = Transaction.objects.filter(reference=ref).first()
    if t is None:
        return fail("Transaction not found", status=404)
    meta = dict(t.meta or {})
    before = bool(meta.get("flagged"))
    if flagged:
        meta["flagged"] = True
    else:
        meta.pop("flagged", None)
    t.meta = meta
    t.save(update_fields=["meta"])
    audit(request, "txn.flag" if flagged else "txn.unflag", target=ref,
          before={"flagged": before}, after={"flagged": flagged})
    # status = what the row should now display (the underlying ledger status
    # when released — flagging never rewrites the settled status).
    display = "flagged" if flagged else _STATUS_MAP.get(t.transaction_status, "pending")
    return ok(success=True, ref=ref, flagged=flagged, status=display)


@staff_endpoint(methods=("POST",), perm="money")
def card_freeze(request):
    """POST {card_id, status: active|frozen} — freeze/unfreeze a virtual card.

    Accepts the bare pk or the portal's serialized form (``cd_<pk>``)."""
    from cards.models import VirtualCard
    from utility.providers import card_set_status

    raw = str(request.data.get("card_id") or "")
    try:
        card = VirtualCard.objects.get(pk=int(raw.removeprefix("cd_")))
    except (VirtualCard.DoesNotExist, TypeError, ValueError):
        return fail("Card not found", status=404)
    status = (request.data.get("status") or "").strip()
    if status not in (VirtualCard.ACTIVE, VirtualCard.FROZEN):
        return fail("status must be active or frozen")
    # Freeze/unfreeze at the ISSUER first (same as the user-facing
    # cards.toggle_freeze) — a DB-only flip left the real card transacting.
    result = card_set_status(card.card_token, active=(status == VirtualCard.ACTIVE))
    if not result.get("success"):
        return fail(result.get("message", "Could not update card"), status=502)
    before = card.status
    card.status = status
    card.save(update_fields=["status"])
    audit(request, "card.freeze" if status == VirtualCard.FROZEN else "card.unfreeze",
          target=f"cd_{card.id}", before={"status": before}, after={"status": status})
    return ok(success=True, card_id=card.id, status=status)


@staff_endpoint(methods=("POST",), perm="wa")
def wa_handover(request):
    """POST {msisdn, mode: human|bot} — take over / return a WhatsApp conversation."""
    from whatsapp.models import ConversationState

    msisdn = (request.data.get("msisdn") or "").strip()
    mode = (request.data.get("mode") or "").strip()
    if mode not in ("human", "bot"):
        return fail("mode must be human or bot")
    cs = ConversationState.for_msisdn(msisdn)
    before = {"status": cs.status, "ai": cs.ai_enabled}
    cs.status = ConversationState.HUMAN if mode == "human" else ConversationState.BOT
    cs.ai_enabled = mode == "bot"
    cs.assigned_agent = request.staff if mode == "human" else None
    cs.save(update_fields=["status", "ai_enabled", "assigned_agent", "updated"])
    audit(request, "wa.handover" if mode == "human" else "wa.return_to_bot", target=msisdn,
          before=before, after={"status": cs.status, "ai": cs.ai_enabled})
    return ok(success=True, msisdn=msisdn, status=cs.status)


@staff_endpoint(methods=("POST",), perm="users")
def user_pin_unlock(request):
    """POST {uid} — clear a user's transaction-PIN lockout (the PIN itself is
    untouched; the user keeps having to know it). Fail-closed: nothing here can
    move money."""
    u = _get_user(request.data.get("uid"))
    if u is None:
        return fail("User not found", status=404)
    before = {"locked_until": str(u.pin_locked_until or ""), "failed_attempts": u.pin_failed_attempts}
    u.pin_failed_attempts = 0
    u.pin_locked_until = None
    u.save(update_fields=["pin_failed_attempts", "pin_locked_until"])
    audit(request, "user.pin_unlock", target=f"u_{u.id}", before=before, after={"locked_until": ""})
    return ok(success=True, uid=u.id)


@staff_endpoint(methods=("POST",), perm="money")
def txn_requery(request):
    """POST {ref} — requery a provider-timeout PENDING purchase and settle it.

    Identical to the reconcile cron / ops portal path: provider truth decides
    settle vs refund, idempotently. Anything not provider-pending is a 409."""
    from utility.providers import vtu_requery
    from wallet.models import Transaction
    from wallet.services import is_bank_payout, settle_or_refund

    ref = (request.data.get("ref") or "").strip()
    txn = Transaction.objects.filter(reference=ref).first()
    if txn is None:
        return fail("Transaction not found", status=404)
    if not (txn.transaction_status == Transaction.PENDING and (txn.meta or {}).get("reconcile")):
        return fail("Only provider-pending purchases can be requeried", status=409)
    if is_bank_payout(txn):
        # A bank transfer settles via the reconcile_wema poller, not a VTU
        # requery — don't query the wrong provider for a reference it never saw.
        return fail("Bank transfers reconcile via the disbursement webhook, not VTU requery", status=409)
    status = settle_or_refund(txn, vtu_requery(txn.reference))
    audit(request, "txn.requery", target=ref, before={"status": "pending"}, after={"status": status})
    return ok(success=True, ref=ref, status=status)


@staff_endpoint(methods=("POST",), perm="money")
def fx_margin(request):
    """POST {bps} — set the FX margin (0–1000 bps) applied to every quote."""
    from whatsapp.models import SystemSetting

    try:
        bps = int(request.data.get("bps"))
    except (TypeError, ValueError):
        return fail("bps must be an integer")
    if not 0 <= bps <= 1000:
        return fail("bps must be between 0 and 1000")
    before = SystemSetting.get("fx_margin_bps", "60")
    SystemSetting.set("fx_margin_bps", str(bps))
    audit(request, "fx.margin_update", target="fx_margin_bps", before={"bps": before}, after={"bps": bps})
    return ok(success=True, margin=bps)


@staff_endpoint(methods=("POST",), perm="money")
def fx_corridor(request):
    """POST {currency, enabled} — pause/resume a settlement corridor. CNY is
    settlement-blocked in code and can't be enabled from here."""
    from whatsapp.models import SystemSetting

    ccy = (request.data.get("currency") or "").upper()
    if ccy not in ("USD", "GBP", "CAD", "CNY") or ccy == "CNY":
        return fail("Corridor not toggleable" if ccy == "CNY" else "Unknown corridor")
    enabled = bool(request.data.get("enabled"))
    before = _corridor_enabled(ccy)
    SystemSetting.set(f"fx_corridor_{ccy.lower()}_enabled", "true" if enabled else "false")
    audit(request, "fx.corridor_update", target=f"NGN/{ccy}",
          before={"enabled": before}, after={"enabled": enabled})
    return ok(success=True, currency=ccy, enabled=enabled)


@staff_endpoint(methods=("POST",), perm="money")
def loan_remind(request):
    """POST {ref} — send the borrower a WhatsApp repayment reminder."""
    from loans.models import Loan
    from whatsapp.models import WhatsAppLink
    from whatsapp.router import reply as wa_send

    ref = (request.data.get("ref") or "").strip()
    loan = Loan.objects.select_related("user").filter(reference=ref).first()
    if loan is None:
        return fail("Loan not found", status=404)
    link = WhatsAppLink.objects.filter(user=loan.user, status=WhatsAppLink.ACTIVE).first()
    if link is None:
        return fail("Borrower has no linked WhatsApp", status=409)
    wa_send(link.wa_msisdn,
            f"Hi {loan.user.first_name or 'there'}, a reminder from Zitch: your loan "
            f"({loan.reference}) has ₦{loan.outstanding:,.2f} outstanding, due {loan.due_date:%b %d}. "
            "Open the app to repay.")
    audit(request, "loan.reminder", target=ref)
    return ok(success=True, ref=ref)


@staff_endpoint(methods=("POST",), perm="money")
def run_maturities(request):
    """POST {} — run the Fixed-Save maturity sweep now (idempotent per plan)."""
    from savings.services import run_maturities as run_maturities_service

    n = run_maturities_service()
    audit(request, "recon.maturities_run", after={"paid_out": n})
    return ok(success=True, paid_out=n)


@staff_endpoint(methods=("POST",), perm="money")
def run_recon(request):
    """POST {} — requery + settle every provider-pending purchase (the VTU
    reconcile cron's loop, on demand)."""
    from utility.providers import vtu_requery
    from wallet.services import pending_vtu_purchases, settle_or_refund

    cutoff = timezone.now() - timedelta(minutes=5)
    # VTU.ng purchases only; bank-transfer payouts settle via the disbursement
    # webhook, not a VTU requery (see wallet.services.pending_vtu_purchases).
    pending = list(pending_vtu_purchases(cutoff))
    settled = 0
    for txn in pending:
        if settle_or_refund(txn, vtu_requery(txn.reference)) != "pending":
            settled += 1
    audit(request, "recon.vtu_run", after={"checked": len(pending), "settled": settled})
    return ok(success=True, checked=len(pending), settled=settled)


@staff_endpoint(methods=("POST",), perm="wa")
def wa_conv_ai(request):
    """POST {msisdn, enabled} — toggle the AI layer for one conversation."""
    from whatsapp.models import ConversationState

    msisdn = (request.data.get("msisdn") or "").strip()
    if not msisdn:
        return fail("msisdn required")
    cs = ConversationState.for_msisdn(msisdn)
    before = {"ai_enabled": cs.ai_enabled}
    cs.ai_enabled = bool(request.data.get("enabled"))
    cs.save(update_fields=["ai_enabled", "updated"])
    audit(request, "conversation.ai_toggle", target=msisdn, before=before,
          after={"ai_enabled": cs.ai_enabled})
    return ok(success=True, msisdn=msisdn, enabled=cs.ai_enabled)


@staff_endpoint(methods=("POST",), perm="wa")
def wa_reply(request):
    """POST {msisdn, text} — send an agent reply into a WhatsApp conversation."""
    from whatsapp.router import reply as wa_send

    msisdn = (request.data.get("msisdn") or "").strip()
    text = (request.data.get("text") or "").strip()
    if not msisdn or not text:
        return fail("msisdn and text required")
    result = wa_send(msisdn, text)
    if not result.get("success"):
        return fail(result.get("message", "WhatsApp delivery failed"), status=502)
    audit(request, "conversation.agent_reply", target=msisdn, after={"chars": len(text)})
    return ok(success=True, msisdn=msisdn, message_id=result.get("message_id", ""))


@staff_endpoint(methods=("POST",), perm="broadcast")
def wa_broadcast(request):
    """POST {template_name, category?} — request a checked template campaign."""
    from common import approvals
    from whatsapp.ops import validate_broadcast_spec
    from whatsapp.providers import wa_enabled

    if not wa_enabled():
        return fail("WhatsApp banking is currently unavailable", status=503)
    try:
        spec = validate_broadcast_spec(request.data)
        approval = approvals.submit(
            "whatsapp.broadcast", payload=spec, requested_by=request.staff,
            reason="WhatsApp template campaign",
        )
    except (ValueError, approvals.ApprovalError) as exc:
        return fail(str(exc))
    response = ok(
        success=True, pending_approval=True, approval_id=approval.pk,
        message="A second broadcast operator must approve this campaign.",
    )
    response.status_code = 202
    return response


# --------------------------------------------------------------------------- #
# Read endpoints beyond bootstrap — customer 360, server-side search,
# broadcast delivery detail. Open to any staff role (read_only included),
# like bootstrap.
# --------------------------------------------------------------------------- #
@staff_endpoint(methods=("POST",))
def user_detail(request):
    """POST {uid} — one customer's full picture: profile, recent ledger rows,
    products, WhatsApp link state, and the audit entries that touched them."""
    from cards.models import VirtualCard
    from loans.models import Loan
    from savings.models import FixedSave
    from wallet.models import Transaction
    from whatsapp.models import AuditLog, WhatsAppLink

    u = _get_user(request.data.get("uid"))
    if u is None:
        return fail("User not found", status=404)
    name = (u.get_full_name() or u.username or u.phone or "—").strip()
    wallets = _wallets_by_user(user_ids=[u.id])
    wa = _wa_by_user(user_ids=[u.id])
    link = WhatsAppLink.objects.filter(user=u, status=WhatsAppLink.ACTIVE).first()
    txns = [_txn_row(t, {u.id: name}) for t in Transaction.objects.filter(user=u)[:25]]
    # Audit rows that touched THIS user. Targets are written as "u_<id>",
    # "u_<id> (…)" (admin_api) or "user:<id>" (ops portal); the old
    # `target__contains="u_<id>"` cross-matched u_1 against u_12/u_103 and
    # missed the ops-portal form entirely.
    target_q = (Q(target=f"u_{u.id}") | Q(target__startswith=f"u_{u.id} ")
                | Q(target=f"user:{u.id}") | Q(target__startswith=f"user:{u.id} "))
    return ok(
        user=_user_row(u, wallets, wa),
        txns=txns,
        loans=[_loan_row(l, name) for l in Loan.objects.filter(user=u)[:20]],
        savings=[_saving_row(s, name) for s in FixedSave.objects.filter(user=u)[:20]],
        cards=[_card_row(c, name) for c in VirtualCard.objects.filter(user=u)[:20]],
        wa_msisdn=(link.wa_msisdn if link else ""),
        pin_locked=bool(u.pin_locked_until and u.pin_locked_until > timezone.now()),
        audit=[_audit_row(a) for a in AuditLog.objects.filter(target_q)[:20]],
    )


@staff_endpoint(methods=("POST",))
def user_search(request):
    """POST {q} — server-side user search across name/username/email/phone
    (bootstrap carries only the newest 300)."""
    q = (request.data.get("q") or "").strip()
    User = request.staff.__class__
    qs = User.objects.all().order_by("-date_joined")
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q)
            | Q(username__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
        )
    rows = list(qs[:100])
    ids = [u.id for u in rows]
    wallets = _wallets_by_user(user_ids=ids)
    wa = _wa_by_user(user_ids=ids)
    return ok(rows=[_user_row(u, wallets, wa) for u in rows],
              total=qs.count())


@staff_endpoint(methods=("POST",))
def txn_search(request):
    """POST {q?, type?, status?} — server-side ledger search (bootstrap carries
    only the newest 150). Filters mirror the portal's chips."""
    from wallet.models import Transaction

    q = (request.data.get("q") or "").strip()
    typ = (request.data.get("type") or "all").lower()
    status = (request.data.get("status") or "all").lower()
    qs = Transaction.objects.all().order_by("-created")
    if typ != "all":
        if typ == "fx":
            qs = qs.filter(Q(service__icontains="fx") | Q(service__icontains="convert"))
        else:
            qs = qs.filter(service__icontains=typ)
    if status == "flagged":
        qs = qs.filter(meta__flagged=True)
    elif status in _STATUS_MAP.values():
        rev = {v: k for k, v in _STATUS_MAP.items()}
        qs = qs.filter(transaction_status=rev[status])
    if q:
        qs = qs.filter(
            Q(reference__icontains=q) | Q(service__icontains=q)
            | Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q)
            | Q(user__username__icontains=q) | Q(user__phone__icontains=q)
        )
    rows = list(qs.select_related("user")[:200])
    name_by_id = {t.user_id: (t.user.get_full_name() or t.user.username or t.user.phone or "—").strip()
                  for t in rows}
    return ok(rows=[_txn_row(t, name_by_id) for t in rows])


@staff_endpoint(methods=("POST",))
def audit_search(request):
    """POST {q} — search the full append-only audit log (bootstrap carries 100)."""
    from whatsapp.models import AuditLog

    q = (request.data.get("q") or "").strip()
    qs = AuditLog.objects.all()
    if q:
        qs = qs.filter(Q(action__icontains=q) | Q(target__icontains=q) | Q(actor_id__icontains=q))
    return ok(rows=[_audit_row(a) for a in qs[:200]])


@staff_endpoint(methods=("POST",))
def wa_broadcast_detail(request):
    """POST {id} — per-recipient delivery outcomes for one broadcast.
    Accepts the bare pk or the portal's serialized form (``bc_<pk>``)."""
    from whatsapp.models import Broadcast

    raw = str(request.data.get("id") or "")
    try:
        b = Broadcast.objects.get(pk=int(raw.removeprefix("bc_")))
    except (Broadcast.DoesNotExist, TypeError, ValueError):
        return fail("Broadcast not found", status=404)
    recipients = [{
        "user": (r.user.get_full_name() or r.user.username) if r.user else "—",
        "msisdn": r.wa_msisdn, "status": r.status, "error": r.error, "t": _ms(r.created),
    } for r in b.recipients.select_related("user").order_by("-created")[:300]]
    return ok(broadcast={
        "id": f"bc_{b.id}", "template": b.template_name, "category": b.category,
        "status": b.status, "created": b.created.strftime("%b %d, %Y"),
        "by": (b.created_by.email if b.created_by else "system"),
        "queued": b.count_queued, "sent": b.count_sent, "delivered": b.count_delivered,
        "read": b.count_read, "failed": b.count_failed, "unknown": b.count_unknown,
    }, recipients=recipients)


# --------------------------------------------------------------------------- #
# Manual wallet credit (goodwill / manual refund) — money capability.
# --------------------------------------------------------------------------- #
@staff_endpoint(methods=("POST",), perm="money")
def wallet_credit(request):
    """POST {uid, amount, reason, idempotency_key?} — credit a user's NGN wallet.

    Back-office goodwill/refund credits ride the SAME ledger service as funding
    (wallet.services.credit): atomic, row-locked, and idempotent under the
    client key, so an operator double-click can never credit twice. A reason is
    mandatory and lands in both the ledger row's meta and the audit log.

    Above the single-credit ceiling this now does what its own error message has
    always promised — routes the credit to a SECOND OPERATOR rather than refusing
    it — provided dual approval is enabled. See common.approvals.
    """
    from common.http import parse_amount

    u = _get_user(request.data.get("uid"))
    if u is None:
        return fail("User not found", status=404)
    amount = parse_amount(request.data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    reason = (request.data.get("reason") or "").strip()
    if len(reason) < 5:
        return fail("A reason (min 5 characters) is required for manual credits")
    # Bound insider/abuse risk on the highest-value operator action: a per-credit
    # ceiling and a per-operator rolling-24h cap (settings-driven), independent of
    # the idempotency guard (which only stops *accidental* double credits).
    from decimal import Decimal as _D

    from django.conf import settings

    max_one = _D(str(getattr(settings, "ADMIN_MAX_MANUAL_CREDIT", "500000")))
    if amount > max_one:
        from common import approvals

        if not approvals.required_for("wallet.credit"):
            return fail(f"Amount exceeds the single manual-credit limit of ₦{max_one:,.0f}. "
                        "A larger credit needs a second approver (set "
                        "OPS_REQUIRE_DUAL_APPROVAL to enable that route).",
                        status=403, code="credit_limit")
        # Held, not performed. Nothing is credited until a DIFFERENT operator
        # approves, and the approval executes this same function's money core.
        req = approvals.submit(
            "wallet.credit",
            payload={"uid": u.id, "amount": str(amount), "reason": reason,
                     "idempotency_key": (request.data.get("idempotency_key") or "")},
            requested_by=request.staff, reason=reason)
        audit(request, "wallet.manual_credit_requested", target=f"u_{u.id}",
              after={"amount": str(amount), "reason": reason, "approval_id": req.pk})
        return ok(pending_approval=True, approval_id=req.pk, uid=u.id, amount=str(amount),
                  message=("Above your single-credit limit — sent to a second operator "
                           "for approval. Nothing has been credited yet."),
                  status=202)

    return _perform_manual_credit(
        user=u, amount=amount, reason=reason,
        actor=request.staff, idempotency_key=request.data.get("idempotency_key"),
        single_cap=max_one)


def _perform_manual_credit(*, user, amount, reason, actor, idempotency_key=None,
                           single_cap=None):
    """The money core of a manual credit, shared by the direct path and the
    dual-approval executor.

    Extracted rather than duplicated deliberately: this is the one operator action that
    creates money from nothing, and a second implementation of it — even a careful one —
    would be a second place for the caps, the idempotency key and the audit row to
    drift out of agreement.

    `single_cap=None` waives the per-credit ceiling, which is correct only on the
    approved path: that ceiling's documented remedy IS a second approver, so enforcing
    it after one has approved would make the approval route useless. The per-operator
    rolling-24h cap is NOT waived — it bounds the maker either way, because two
    colluding operators is a different threat from one, and an unbounded approved path
    would become the weakest link.
    """
    from datetime import timedelta as _td
    from decimal import Decimal as _D

    from django.conf import settings
    from django.db import transaction as _dbtx
    from django.utils import timezone as _tz

    from accounts.models import User as _User
    from common.http import idempotent_replay, spend_key
    from wallet.services import (DuplicateTransaction, credit, existing_for_key,
                                 get_or_create_wallet)
    from whatsapp.models import AuditLog as _AL

    u = user
    if single_cap is not None and amount > single_cap:
        return fail(f"Amount exceeds the single manual-credit limit of ₦{single_cap:,.0f}. "
                    "A larger credit needs a second approver.", status=403, code="credit_limit")
    op = actor.email or actor.username or str(actor.id)
    since = _tz.now() - _td(hours=24)
    day_cap = _D(str(getattr(settings, "ADMIN_MANUAL_CREDIT_DAILY_CAP", "2000000")))
    # Derive a server-side idempotency key when the client omits one: the ledger's
    # unique (user, idempotency_key) constraint is PARTIAL (excludes ""), so a blank
    # key would let a double-submit credit twice. spend_key falls back to a
    # deterministic per-(user, amount, reason) key within a short window.
    key = spend_key(idempotency_key, u, "manual_credit", amount, reason)
    replay = idempotent_replay(existing_for_key(u, key))
    if replay is not None:
        return replay
    with _dbtx.atomic():
        # Serialize THIS operator's manual credits so the rolling-24h cap can't be
        # raced: without the lock, N concurrent credits each read spent=0, all pass
        # the check, and mint past the cap (the control this bounds). Locking the
        # operator's own staff row is a cheap per-operator mutex; credit() then
        # locks the target wallet row, in a consistent order (no deadlock).
        _User.objects.select_for_update().get(pk=actor.id)
        spent_today = _D("0")
        for row in _AL.objects.filter(actor_id=op, action="wallet.manual_credit", created__gte=since):
            try:
                spent_today += _D(str((row.after or {}).get("amount", "0")))
            except (TypeError, ValueError):
                pass
        if spent_today + amount > day_cap:
            return fail(f"This exceeds your ₦{day_cap:,.0f} daily manual-credit cap "
                        f"(₦{spent_today:,.0f} already in the last 24h).",
                        status=403, code="credit_daily_cap")
        before = get_or_create_wallet(u).balance
        try:
            txn = credit(
                u, amount, "Manual credit — operations",
                meta={"channel": "admin", "reason": reason,
                      "actor": (actor.email or actor.username)},
                idempotency_key=key,
            )
        except DuplicateTransaction:
            return idempotent_replay(existing_for_key(u, key))
        # Written with the ACTOR, not a request: on the approved path there is no
        # request in scope, and attributing the credit to the approver rather than the
        # maker is what makes the trail readable.
        _AL.objects.create(
            actor_type="admin", actor_id=op, action="wallet.manual_credit",
            target=f"u_{u.id}", before={"balance": str(before)},
            after={"balance": str(before + amount), "amount": str(amount),
                   "reason": reason})
    return ok(success=True, uid=u.id, reference=txn.reference,
              amount=str(amount), balance=_num(before + amount))


# --------------------------------------------------------------------------- #
# Dual approval (maker/checker) — the queue for actions held for a second operator.
# --------------------------------------------------------------------------- #
from common import approvals as _approvals  # noqa: E402

PAGE_APPROVALS = 100


@_approvals.register("wallet.credit", capability="money")
def _execute_approved_credit(payload, approver, approval_request=None):
    """Run an approved manual credit through the SAME money core the direct path uses.

    The single-credit ceiling is waived here and only here: that ceiling's documented
    remedy is a second approver, so still enforcing it after one has approved would
    make the whole route pointless. Every other control — the rolling-24h cap, the
    idempotency key, the ledger's own guards — applies unchanged.
    """
    from decimal import Decimal

    user = _get_user(payload.get("uid"))
    if user is None:
        raise ValueError(f"user {payload.get('uid')} no longer exists or is now staff")
    res = _perform_manual_credit(
        user=user, amount=Decimal(str(payload["amount"])), reason=payload.get("reason", ""),
        actor=approver, idempotency_key=payload.get("idempotency_key") or None,
        single_cap=None)
    # _perform_manual_credit returns an HttpResponse either way; surface the body so a
    # refusal (e.g. the daily cap) is recorded on the request rather than looking like
    # a success.
    import json as _json

    body = _json.loads(res.content or b"{}")
    if res.status_code != 200:
        raise ValueError(body.get("message") or f"credit refused ({res.status_code})")
    return body


@staff_endpoint(methods=("POST",))
def approvals_list(request):
    """POST {status?} — the approval queue. Defaults to pending."""
    from whatsapp.models import ApprovalRequest

    status = (request.data.get("status") or ApprovalRequest.PENDING).strip()
    rows = list(ApprovalRequest.objects.filter(status=status)[:PAGE_APPROVALS])
    rows = [r for r in rows
            if (_approvals.capability_for(r.action)
                and _approvals.capability_for(r.action) in CAN.get(request.role, set()))]
    return ok(rows=[{
        "id": r.pk, "action": r.action, "payload": r.payload, "reason": r.reason,
        "status": r.status,
        "requested_by": (r.requested_by.email or r.requested_by.username),
        "decided_by": (r.decided_by.email or r.decided_by.username) if r.decided_by else "",
        "created": _ms(r.created), "decided": _ms(r.decided), "result": r.result,
        # So the UI can grey out a request the viewer is not allowed to decide, instead
        # of offering a button that always fails.
        "is_own_request": r.requested_by_id == request.staff.id,
        "can_decide": r.requested_by_id != request.staff.id,
    } for r in rows])


@staff_endpoint(methods=("POST",))
def approvals_decide(request):
    """POST {id, approve, note?} — approve or reject a held action.

    Self-approval is refused in the service, not here, so no endpoint can forget it.
    """
    from common.approvals import ApprovalError, decide
    from whatsapp.models import ApprovalRequest

    try:
        req = ApprovalRequest.objects.get(pk=int(request.data.get("id") or 0))
    except (ApprovalRequest.DoesNotExist, TypeError, ValueError):
        return fail("Approval request not found", status=404)
    capability = _approvals.capability_for(req.action)
    if not capability or capability not in CAN.get(request.role, set()):
        return fail("Insufficient privileges for this action", status=403, code="forbidden")
    approve = bool(request.data.get("approve"))
    try:
        decided = decide(req, approver=request.staff, approve=approve,
                         note=(request.data.get("note") or ""))
    except ApprovalError as exc:
        return fail(str(exc), status=409, code="approval_conflict")
    return ok(success=True, id=decided.pk, status=decided.status, result=decided.result)


# --------------------------------------------------------------------------- #
# Operator MFA (TOTP)
# --------------------------------------------------------------------------- #
@staff_endpoint(methods=("POST",))
def mfa_status(request):
    """POST {} — whether this operator has a confirmed second factor."""
    from accounts.models import OperatorTotp

    row = OperatorTotp.objects.filter(user=request.staff).first()
    return ok(enrolled=bool(row and row.confirmed),
              pending=bool(row and not row.confirmed),
              required=_mfa_required_for(request.staff))


@staff_endpoint(methods=("POST",))
def mfa_enroll(request):
    """POST {} — issue a fresh secret and the otpauth:// URI to scan.

    Re-enrolling replaces an UNCONFIRMED secret freely, but a CONFIRMED one requires a
    current code (below): otherwise any authenticated operator session could silently
    swap the second factor for one the attacker controls, which would make the whole
    factor decorative.
    """
    from accounts.models import OperatorTotp
    from accounts.totp import new_secret, provisioning_uri

    row = OperatorTotp.objects.filter(user=request.staff).first()
    if row and row.confirmed:
        from accounts.totp import verify
        code = (request.data.get("code") or "")
        step = verify(row.secret, code, after_step=row.last_step)
        if step is None:
            return fail("Enter a current code from your existing authenticator to replace it.",
                        status=403, code="mfa_code_required")
        row.last_step = step
        row.save(update_fields=["last_step"])

    secret = new_secret()
    account = request.staff.email or request.staff.username
    OperatorTotp.objects.update_or_create(
        user=request.staff,
        defaults={"secret": secret, "confirmed": False, "last_step": 0,
                  "confirmed_at": None})
    audit(request, "ops.mfa_enroll_started", target=request.staff.username)
    # The secret is returned ONCE. There is no endpoint that re-displays it: an
    # authenticated session that could re-read it would be a permanent bypass.
    return ok(secret=secret, otpauth_uri=provisioning_uri(secret, account=account),
              message="Scan this in your authenticator, then confirm with a code.")


@staff_endpoint(methods=("POST",))
def mfa_confirm(request):
    """POST {code} — prove the secret was stored, and turn the factor on."""
    from django.utils import timezone as _tz

    from accounts.models import OperatorTotp
    from accounts.totp import verify

    row = OperatorTotp.objects.filter(user=request.staff).first()
    if row is None:
        return fail("Start enrolment first", status=400, code="not_enrolled")
    step = verify(row.secret, request.data.get("code") or "", after_step=row.last_step)
    if step is None:
        return fail("That code is not valid. Check your device clock and try the next code.",
                    status=403, code="mfa_invalid")
    row.confirmed = True
    row.confirmed_at = _tz.now()
    row.last_step = step
    row.save(update_fields=["confirmed", "confirmed_at", "last_step"])
    audit(request, "ops.mfa_enabled", target=request.staff.username)
    return ok(success=True, message="Two-factor authentication is on for your account.")


@staff_endpoint(methods=("POST",))
def mfa_disable(request):
    """POST {code} — turn the factor off, proving possession first.

    A session alone is not enough. The realistic attack is a hijacked operator session
    (a shared machine, a stolen token): if that session could remove the factor, the
    factor only protects the login form and not the account.
    """
    from accounts.models import OperatorTotp
    from accounts.totp import verify

    row = OperatorTotp.objects.filter(user=request.staff, confirmed=True).first()
    if row is None:
        return ok(success=True, message="Two-factor authentication was not enabled.")
    if verify(row.secret, request.data.get("code") or "", after_step=row.last_step) is None:
        return fail("Enter a current code to turn two-factor off.", status=403,
                    code="mfa_invalid")
    row.delete()
    audit(request, "ops.mfa_disabled", target=request.staff.username)
    return ok(success=True, message="Two-factor authentication is off.")


def _mfa_required_for(user) -> bool:
    """Whether this operator MUST have a second factor.

    `OPS_REQUIRE_MFA` is off by default on purpose: switching it on before operators
    have enrolled would lock every one of them out of the portal at once, including
    whoever would have to fix it. With it on, only money- or settings-capable roles are
    required — a read-only account cannot move anything, and forcing enrolment on it
    buys nothing while giving people a reason to resent the control.
    """
    from django.conf import settings

    if not getattr(settings, "OPS_REQUIRE_MFA", False):
        return False
    from .auth import staff_role

    return staff_role(user) in ("super_admin", "finance")


def _mfa_login_error(user, code):
    """None when the operator may sign in; a failure response otherwise.

    Two distinct outcomes, and conflating them is how an MFA rollout locks people out:

    * A CONFIRMED factor is always demanded. `mfa_required` tells the client to prompt
      for a code rather than showing "wrong password" for a correct one.
    * `OPS_REQUIRE_MFA` additionally refuses a money-capable operator who has NOT
      enrolled — but with a message that says to enrol, not that the credentials were
      wrong.
    """
    from accounts.models import OperatorTotp
    from accounts.totp import verify

    row = OperatorTotp.objects.filter(user=user, confirmed=True).first()
    if row is None:
        if _mfa_required_for(user):
            return fail("Two-factor authentication is required for your role. Ask a super "
                        "admin to reset your access so you can enrol.",
                        status=403, code="mfa_enrolment_required")
        return None
    if not code:
        return fail("Enter the code from your authenticator app.", status=401,
                    code="mfa_required")
    step = verify(row.secret, code, after_step=row.last_step)
    if step is None:
        return fail("That code is not valid or has already been used.", status=401,
                    code="mfa_invalid")
    # Burn the step so the same code cannot be replayed inside its own window.
    row.last_step = step
    row.save(update_fields=["last_step"])
    return None
