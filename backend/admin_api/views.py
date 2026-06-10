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

from common.http import fail, ok, resolve_token
from common.ratelimit import ratelimit

from .auth import ROLES, staff_endpoint, staff_role, audit, CAN

# Capabilities surfaced to the portal so its UI can gate consistently with the
# server (the server remains the real gate via @staff_endpoint perm=...).
PERMS_MATRIX = [
    {"perm": "View dashboards & logs", "super_admin": True, "finance": True, "support": True, "read_only": True},
    {"perm": "Reply / handover WhatsApp chats", "super_admin": True, "finance": False, "support": True, "read_only": False},
    {"perm": "Send broadcasts", "super_admin": True, "finance": False, "support": True, "read_only": False},
    {"perm": "Refund / requery / flag transactions", "super_admin": True, "finance": True, "support": False, "read_only": False},
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


def _wallets_by_user() -> dict:
    from wallet.models import CurrencyWallet, Wallet

    out: dict[int, dict] = {}
    for w in Wallet.objects.all():
        out.setdefault(w.user_id, {"NGN": 0, "USD": 0, "GBP": 0, "CAD": 0})
        out[w.user_id]["NGN"] = _num(w.balance)
    for cw in CurrencyWallet.objects.all():
        out.setdefault(cw.user_id, {"NGN": 0, "USD": 0, "GBP": 0, "CAD": 0})
        out[cw.user_id][cw.currency] = _num(cw.balance)
    return out


def _wa_by_user() -> dict:
    from whatsapp.models import WhatsAppLink

    out: dict[int, dict] = {}
    for link in WhatsAppLink.objects.all():
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
    }


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
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

    ident = (data.get("username") or data.get("email") or "").strip()
    password = data.get("password") or ""
    if not ident or not password:
        return fail("Username and password are required")
    user = User.objects.filter(Q(username__iexact=ident) | Q(email__iexact=ident)).first()
    if user is None or not user.check_password(password):
        return fail("Incorrect credentials", status=401)
    if not user.is_staff:
        return fail("This account does not have operator access", status=403)
    token = AccessToken.issue(user)
    return ok(token=token.key, role=staff_role(user),
              name=(user.get_full_name() or user.username), email=user.email)


@staff_endpoint(methods=("POST",))
def logout(request):
    """POST /api/admin/logout — revoke the presented staff token."""
    from accounts.models import AccessToken

    AccessToken.objects.filter(key=resolve_token(request)).delete()
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
    for u in User.objects.filter(Q(bvn_verified=True) | Q(nin_verified=True) | Q(bvn__gt="") | Q(nin__gt="")).order_by("-date_joined")[:50]:
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
        "read": b.count_read, "failed": b.count_failed,
    } for b in Broadcast.objects.all()[:50]]

    audit_rows = [{
        "actor": a.actor_id or a.actor_type, "role": a.actor_type, "action": a.action, "target": a.target,
        "before": a.before, "after": a.after, "t": _ms(a.created),
    } for a in AuditLog.objects.all()[:100]]

    loans = [{
        "id": f"ln_{l.id}", "user": name_by_id.get(l.user_id, "—"), "amt": _num(l.principal),
        "tenor": f"{l.tenure_days} days", "rate": "4.5%/mo", "status": l.status,
        "due": l.due_date.strftime("%b %d, %Y") if l.due_date else "—", "outstanding": _num(l.outstanding),
    } for l in Loan.objects.all()[:80]]

    savings = [{
        "id": f"sv_{s.id}", "user": name_by_id.get(s.user_id, "—"), "principal": _num(s.principal),
        "rate": f"{_num(s.rate) * 100:.0f}% p.a.", "start": s.created.strftime("%b %d, %Y"),
        "maturity": s.matures_at.strftime("%b %d, %Y") if s.matures_at else "—",
        "status": ("paid" if s.paid_out else s.status), "payout": _num(s.maturity_value),
    } for s in FixedSave.objects.all()[:80]]

    cards = [{
        "id": f"cd_{c.id}", "user": name_by_id.get(c.user_id, "—"), "last4": c.last4, "cur": "USD",
        "bal": _num(c.balance), "status": c.status, "spend30": 0,
    } for c in VirtualCard.objects.all()[:80]]

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
    }

    # --- FX corridors (real config: settle flags + live margin) ---
    margin_bps = int(SystemSetting.get("fx_margin_bps", "60") or "60")
    flags = {"USD": "🇺🇸", "GBP": "🇬🇧", "CAD": "🇨🇦", "CNY": "🇨🇳"}
    rates = [{
        "pair": f"NGN/{c}", "flag": flags.get(c, "🏳️"), "margin": margin_bps,
        "settle": c in SETTLEABLE, "vol24": 0,
    } for c in ["USD", "GBP", "CAD", "CNY"]]

    # Float = platform liability we actually hold per currency (real).
    from wallet.models import CurrencyWallet
    float_rows = [{"cur": "NGN", "sym": "₦", "bal": _num(total_ngn), "provider": "Monnify"}]
    for c in ["USD", "GBP", "CAD"]:
        bal = CurrencyWallet.objects.filter(currency=c).aggregate(s=Sum("balance"))["s"] or Decimal("0")
        float_rows.append({"cur": c, "sym": {"USD": "$", "GBP": "£", "CAD": "C$"}[c], "bal": _num(bal), "provider": "Fincra"})

    # Providers: live vs mock from the same source the /healthz probe uses.
    from django.conf import settings as dj_settings
    from utility.providers import _baxi_live, _prembly_live, payments_live
    from whatsapp.providers import wa_live

    def _st(live):
        return "operational" if live else "degraded"

    fincra_live = bool(dj_settings.FINCRA.get("SECRET_KEY"))
    providers = [
        {"name": "Monnify", "role": "Funding & payouts", "status": _st(payments_live()), "uptime": "—"},
        {"name": "Baxi", "role": "Airtime · data · bills", "status": _st(_baxi_live()), "uptime": "—"},
        {"name": "Fincra", "role": "FX rates & settlement", "status": _st(fincra_live), "uptime": "—"},
        {"name": "Meta WhatsApp", "role": "Chat channel", "status": _st(wa_live()), "uptime": "—"},
        {"name": "Prembly", "role": "KYC (BVN · NIN · face)", "status": _st(_prembly_live()), "uptime": "—"},
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
        loans=loans, savings=savings, cards=cards, kycq=kycq, webhooks=[], recons=[],
        team=team, perms=PERMS_MATRIX, settings=settings_rows, kpis=kpis,
        meta={"role": request.role, "name": (request.staff.get_full_name() or request.staff.username)},
    )


# --------------------------------------------------------------------------- #
# Write actions — each enforces a capability and appends to the AuditLog.
# --------------------------------------------------------------------------- #
def _get_user(uid):
    from accounts.models import User

    try:
        return User.objects.get(pk=int(uid))
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
    before = SystemSetting.get(key, "")
    SystemSetting.set(key, value)
    audit(request, "settings.update", target=key, before={"value": before}, after={"value": str(value)})
    return ok(success=True, key=key, value=str(value))


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
    audit(request, "user.freeze" if status == "frozen" else "user.unfreeze",
          target=f"u_{u.id} ({u.get_full_name() or u.username})", before={"status": before}, after={"status": status})
    return ok(success=True, uid=u.id, status=status)


@staff_endpoint(methods=("POST",), perm="users")
def kyc_review(request):
    """POST {uid, decision: approve|reject, type: bvn|nin|face}

    Approving marks the relevant verification flag and recomputes the tier
    (face also upgrades nothing but unlocks large transfers). Reject is a no-op
    write that is still audited.
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
    return ok(success=True, ref=ref, flagged=flagged)


@staff_endpoint(methods=("POST",), perm="money")
def card_freeze(request):
    """POST {card_id, status: active|frozen} — freeze/unfreeze a virtual card."""
    from cards.models import VirtualCard

    try:
        card = VirtualCard.objects.get(pk=int(request.data.get("card_id")))
    except (VirtualCard.DoesNotExist, TypeError, ValueError):
        return fail("Card not found", status=404)
    status = (request.data.get("status") or "").strip()
    if status not in (VirtualCard.ACTIVE, VirtualCard.FROZEN):
        return fail("status must be active or frozen")
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
