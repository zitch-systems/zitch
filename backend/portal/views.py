"""Operator portal API (staff-only, role-gated) — the backend for the admin SPA.

Read endpoints serialize the *existing* models; mutations reuse the same
services the app/WhatsApp channels use (settle_or_refund, run_maturities,
send_text, SystemSetting) so the portal can never take a code path money
doesn't already take. Every mutation is appended to the AuditLog with
before/after (hard-rule #10).
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from accounts.models import AccessToken, User
from common.http import api, fail, ok
from common.ratelimit import ratelimit
from loans.models import Loan
from cards.models import VirtualCard
from savings.models import FixedSave
from savings.services import run_maturities as run_maturities_service
from utility.providers import fx_quote, vtu_requery
from wallet.models import CurrencyWallet, Transaction, Wallet
from wallet.services import is_bank_payout, pending_vtu_purchases, settle_or_refund
from whatsapp.models import (
    AuditLog,
    Broadcast,
    ConversationState,
    SystemSetting,
    WaMessageLog,
    WhatsAppLink,
)
from whatsapp.ops import record_audit
from whatsapp.router import reply as wa_reply

from .roles import CAPS, ROLES, caps_of, require_cap, role_of

PAGE = 100  # hard cap on list sizes; the SPA paginates client-side


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@api
@ratelimit("ops_login", limit=10, window=300)
def login(request):
    """POST /api/ops/login/ {identifier, password} -> {token, role, caps, name}

    Staff-only: a valid password on a non-staff account is still a 403, and the
    failure is audited so brute-force attempts on operator accounts are visible.
    """
    ident = (request.data.get("identifier") or "").strip()
    password = request.data.get("password") or ""
    if not ident or not password:
        return fail("identifier and password required")
    user = User.objects.filter(
        Q(username__iexact=ident) | Q(email__iexact=ident) | Q(phone=ident)
    ).first()
    if user is None or not user.check_password(password):
        record_audit("ops.login_failed", target=ident, actor_type="system")
        return fail("Invalid credentials", status=401)
    if not (user.is_staff and user.is_active):
        record_audit("ops.login_denied", actor=user, target=ident)
        return fail("Staff access required", status=403)
    token = AccessToken.issue(user)
    record_audit("ops.login", actor=user, target=user.username)
    return ok(
        token=token.key,
        role=role_of(user),
        caps=caps_of(user),
        name=(f"{user.first_name} {user.last_name}".strip() or user.username),
        email=user.email,
    )


# --------------------------------------------------------------------------- #
# serializers (tiny, view-shaped)
# --------------------------------------------------------------------------- #
def _kyc_level(u) -> str:
    if u.face_verified:
        return "face"
    if u.nin_verified:
        return "nin"
    if u.bvn_verified:
        return "bvn"
    return "pending"


def _user_row(u, links_by_user, wallets_by_user, cw_by_user) -> dict:
    link = links_by_user.get(u.id)
    wallets = {"NGN": float(wallets_by_user.get(u.id, Decimal("0")))}
    for ccy, bal in cw_by_user.get(u.id, []):
        wallets[ccy] = float(bal)
    return {
        "id": u.id,
        "name": (f"{u.first_name} {u.last_name}".strip() or u.username),
        "phone": u.phone or "",
        "email": u.email,
        "kyc": _kyc_level(u),
        "tier": u.tier,
        "status": "active" if u.is_active else "frozen",
        "joined": u.date_joined.strftime("%b %Y"),
        "wa": (link.status if link else "none"),
        "aiEnabled": bool(link and link.ai_enabled),
        "marketingOptIn": bool(link and link.marketing_opt_in),
        "wallets": wallets,
    }


def _txn_row(t) -> dict:
    meta = t.meta or {}
    amt = float(t.amount)
    if t.direction == Transaction.OUT:
        amt = -amt
    return {
        "id": t.reference,
        "user": (f"{t.user.first_name} {t.user.last_name}".strip() or t.user.username),
        "type": _txn_type(t.service),
        "channel": meta.get("channel", "app"),
        "desc": meta.get("description") or t.service,
        "amt": amt,
        "cur": t.currency,
        "fee": float(meta.get("fee") or 0),
        "status": "flagged" if meta.get("flagged") else t.transaction_status.lower(),
        "time": t.created.isoformat(),
        "canRequery": bool(
            t.transaction_status == Transaction.PENDING and meta.get("reconcile")
        ),
    }


def _txn_type(service: str) -> str:
    s = (service or "").lower()
    for key in ("transfer", "fx", "convert", "fund", "airtime", "data", "electricity", "cable", "exam", "betting", "card", "loan", "save"):
        if key in s:
            return "fx" if key == "convert" else key
    return "other"


# --------------------------------------------------------------------------- #
# overview
# --------------------------------------------------------------------------- #
@api
@require_cap()
def summary(request):
    now = timezone.now()
    month_ago, week_ago = now - timedelta(days=30), now - timedelta(days=7)
    success = Transaction.objects.filter(transaction_status=Transaction.SUCCESS)

    days = [(now - timedelta(days=i)).date() for i in range(13, -1, -1)]
    by_day = dict(
        success.filter(created__gte=now - timedelta(days=14))
        .annotate(d=TruncDate("created"))
        .values_list("d")
        .annotate(v=Sum("amount"))
    )
    fx_30d = success.filter(
        Q(service__icontains="fx") | Q(service__icontains="convert"),
        created__gte=month_ago,
    ).aggregate(v=Sum("amount"))["v"] or 0

    return ok(
        users=User.objects.filter(is_active=True).count(),
        users_month=User.objects.filter(date_joined__gte=month_ago).count(),
        volume_all=float(success.aggregate(v=Sum("amount"))["v"] or 0),
        volume_week=float(success.filter(created__gte=week_ago).aggregate(v=Sum("amount"))["v"] or 0),
        wa_linked=WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE).count(),
        fx_30d=float(fx_30d),
        volume_14d=[float(by_day.get(d, 0)) for d in days],
        success_rate=_success_rate(),
        providers=_providers(),
        latest=[_txn_row(t) for t in Transaction.objects.select_related("user").order_by("-created")[:8]],
    )


def _success_rate() -> float:
    agg = Transaction.objects.aggregate(
        total=Count("id"),
        good=Count("id", filter=Q(transaction_status=Transaction.SUCCESS)),
    )
    return round(100.0 * agg["good"] / agg["total"], 1) if agg["total"] else 100.0


def _providers() -> list:
    from django.conf import settings as st
    from utility.providers import _prembly_live, payout_live, vtu_live

    rows = [
        ("Kora", "Funding & payouts", payout_live()),
        ("VTU.ng", "Airtime · data · bills", vtu_live()),
        ("Fincra", "FX rates & settlement", bool(getattr(st, "FINCRA", {}).get("SECRET_KEY"))),
        ("Meta WhatsApp", "Chat channel", bool(st.WHATSAPP.get("TOKEN"))),
        ("Sendchamp", "SMS / OTP", bool(st.SENDCHAMP["API_KEY"])),
        ("Resend", "Email / OTP fallback", bool(st.RESEND["API_KEY"])),
        ("Prembly", "KYC (BVN · NIN · face)", _prembly_live()),
    ]
    return [
        {"name": n, "role": r, "status": "operational" if live else "mock", "uptime": "live" if live else "mock mode"}
        for n, r, live in rows
    ]


# --------------------------------------------------------------------------- #
# users & KYC
# --------------------------------------------------------------------------- #
def _user_rows(qs):
    users = list(qs[:PAGE])
    ids = [u.id for u in users]
    links = {l.user_id: l for l in WhatsAppLink.objects.filter(user_id__in=ids, status=WhatsAppLink.ACTIVE)}
    wallets = dict(Wallet.objects.filter(user_id__in=ids).values_list("user_id", "balance"))
    cws = {}
    for cw in CurrencyWallet.objects.filter(user_id__in=ids):
        cws.setdefault(cw.user_id, []).append((cw.currency, cw.balance))
    return [_user_row(u, links, wallets, cws) for u in users]


@api
@require_cap()
def users(request):
    q = (request.data.get("q") or "").strip()
    qs = User.objects.filter(is_staff=False).order_by("-date_joined")
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q)
            | Q(username__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
        )
    return ok(rows=_user_rows(qs), total=User.objects.filter(is_staff=False).count())


@api
@require_cap("users")
def user_action(request):
    action = request.data.get("action")
    user = User.objects.filter(id=request.data.get("user_id"), is_staff=False).first()
    if user is None:
        return fail("User not found", status=404)
    if action == "freeze":
        before = {"is_active": user.is_active}
        user.is_active = False
        user.save(update_fields=["is_active"])
        user.tokens.all().delete()  # a frozen account loses its sessions immediately
        record_audit("user.freeze", actor=request.user_obj, target=f"user:{user.id}",
                     before=before, after={"is_active": False})
    elif action == "unfreeze":
        before = {"is_active": user.is_active}
        user.is_active = True
        user.save(update_fields=["is_active"])
        record_audit("user.unfreeze", actor=request.user_obj, target=f"user:{user.id}",
                     before=before, after={"is_active": True})
    elif action == "unlock_pin":
        before = {"pin_locked_until": str(user.pin_locked_until or "")}
        user.pin_failed_attempts = 0
        user.pin_locked_until = None
        user.save(update_fields=["pin_failed_attempts", "pin_locked_until"])
        record_audit("user.pin_unlock", actor=request.user_obj, target=f"user:{user.id}", before=before)
    else:
        return fail("Unknown action")
    return ok(success=True)


@api
@require_cap()
def kyc_queue(request):
    """Users whose submitted identity (BVN/NIN) hasn't verified, or who are
    still below the tier their verified checks support — the manual-review pile."""
    qs = User.objects.filter(is_staff=False, is_active=True).filter(
        Q(bvn_hash__gt="", bvn_verified=False) | Q(nin_hash__gt="", nin_verified=False) | Q(tier=0)
    ).order_by("-date_joined")
    rows = [
        {
            "user": (f"{u.first_name} {u.last_name}".strip() or u.username),
            "id": u.id,
            "type": "nin" if (u.nin and not u.nin_verified) else ("bvn" if u.bvn else "pending"),
            "submitted": u.date_joined.isoformat(),
            "note": f"BVN {'✓' if u.bvn_verified else '—'} · NIN {'✓' if u.nin_verified else '—'} · Face {'✓' if u.face_verified else '—'}",
            "tier": f"{u.tier} → {min(u.tier + 1, 3)}",
        }
        for u in qs[:PAGE]
    ]
    return ok(rows=rows)


@api
@require_cap("users")
def kyc_review(request):
    """Approve (bump tier, capped at 3) or reject a manual KYC review."""
    user = User.objects.filter(id=request.data.get("user_id"), is_staff=False).first()
    if user is None:
        return fail("User not found", status=404)
    approve = bool(request.data.get("approve"))
    before = {"tier": user.tier}
    if approve:
        user.tier = min(user.tier + 1, 3)
        user.save(update_fields=["tier"])
    record_audit(
        "kyc.approve" if approve else "kyc.reject",
        actor=request.user_obj, target=f"user:{user.id}",
        before=before, after={"tier": user.tier},
    )
    return ok(success=True, tier=user.tier)


# --------------------------------------------------------------------------- #
# transactions
# --------------------------------------------------------------------------- #
@api
@require_cap()
def transactions(request):
    q = (request.data.get("q") or "").strip()
    typ = (request.data.get("type") or "all").lower()
    qs = Transaction.objects.select_related("user").order_by("-created")
    if typ != "all":
        if typ == "fx":
            qs = qs.filter(Q(service__icontains="fx") | Q(service__icontains="convert"))
        else:
            qs = qs.filter(service__icontains=typ)
    if q:
        qs = qs.filter(
            Q(reference__icontains=q) | Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q) | Q(service__icontains=q)
        )
    return ok(rows=[_txn_row(t) for t in qs[:PAGE]])


@api
@require_cap("money")
def txn_requery(request):
    """Requery a provider-timeout PENDING purchase and settle it — the same
    idempotent path the reconcile cron takes, for one transaction on demand."""
    ref = (request.data.get("reference") or "").strip()
    txn = Transaction.objects.filter(reference=ref).first()
    if txn is None:
        return fail("Transaction not found", status=404)
    if not (txn.transaction_status == Transaction.PENDING and (txn.meta or {}).get("reconcile")):
        return fail("Only provider-pending purchases can be requeried", status=409)
    if is_bank_payout(txn):
        # A bank transfer settles via the Kora payout webhook, not a VTU
        # requery — don't query the wrong provider for a reference it never saw.
        return fail("Bank transfers reconcile via the disbursement webhook, not VTU requery", status=409)
    result = vtu_requery(txn.reference)
    status = settle_or_refund(txn, result)
    record_audit("txn.requery", actor=request.user_obj, target=ref,
                 before={"status": "pending"}, after={"status": status})
    return ok(success=True, status=status)


# --------------------------------------------------------------------------- #
# FX & treasury
# --------------------------------------------------------------------------- #
FX_CORRIDORS = ("USD", "GBP", "CAD", "CNY")


def _corridor_enabled(ccy: str) -> bool:
    if ccy == "CNY":
        return False  # settlement-blocked in code (§13); the setting can't enable it
    return SystemSetting.get(f"fx_corridor_{ccy.lower()}_enabled", "true") != "false"


@api
@require_cap()
def fx(request):
    margin = Decimal(SystemSetting.get("fx_margin_bps", "0") or "0")
    day_ago = timezone.now() - timedelta(hours=24)
    rates = []
    for ccy in FX_CORRIDORS:
        q = fx_quote(ccy, "NGN", Decimal("1"))
        provider = Decimal(str(q["rate"])) if q.get("success") else Decimal("0")
        customer = provider / (Decimal("1") - margin / Decimal("10000")) if provider else Decimal("0")
        vol = Transaction.objects.filter(
            Q(service__icontains="fx") | Q(service__icontains="convert"),
            transaction_status=Transaction.SUCCESS, created__gte=day_ago,
            meta__to_currency=ccy,
        ).aggregate(v=Sum("amount"))["v"] or 0
        rates.append({
            "pair": f"NGN/{ccy}",
            "provider": float(provider),
            "customer": float(customer),
            "vol24": float(vol),
            "settle": _corridor_enabled(ccy),
        })
    float_rows = [{"cur": "NGN", "bal": float(Wallet.objects.aggregate(v=Sum("balance"))["v"] or 0), "provider": "Kora"}]
    for row in CurrencyWallet.objects.values("currency").annotate(v=Sum("balance")).order_by("currency"):
        float_rows.append({"cur": row["currency"], "bal": float(row["v"] or 0), "provider": "Fincra"})
    return ok(margin=int(margin), rates=rates, float=float_rows)


@api
@require_cap("money")
def fx_margin(request):
    try:
        bps = int(request.data.get("bps"))
    except (TypeError, ValueError):
        return fail("bps must be an integer")
    if not 0 <= bps <= 1000:
        return fail("bps must be between 0 and 1000")
    before = SystemSetting.get("fx_margin_bps", "0")
    SystemSetting.set("fx_margin_bps", str(bps))
    record_audit("fx.margin_update", actor=request.user_obj, target="fx_margin_bps",
                 before={"bps": before}, after={"bps": bps})
    return ok(success=True, margin=bps)


@api
@require_cap("money")
def fx_corridor(request):
    ccy = (request.data.get("currency") or "").upper()
    if ccy not in FX_CORRIDORS or ccy == "CNY":
        return fail("Corridor not toggleable" if ccy == "CNY" else "Unknown corridor")
    enabled = bool(request.data.get("enabled"))
    before = _corridor_enabled(ccy)
    SystemSetting.set(f"fx_corridor_{ccy.lower()}_enabled", "true" if enabled else "false")
    record_audit("fx.corridor_update", actor=request.user_obj, target=f"NGN/{ccy}",
                 before={"enabled": before}, after={"enabled": enabled})
    return ok(success=True)


# --------------------------------------------------------------------------- #
# products: loans / savings / cards
# --------------------------------------------------------------------------- #
@api
@require_cap()
def products(request):
    now = timezone.now()
    loans = [
        {
            "id": l.reference,
            "user": (f"{l.user.first_name} {l.user.last_name}".strip() or l.user.username),
            "amt": float(l.principal),
            "tenor": f"{l.tenure_days}d",
            "rate": "",
            "outstanding": float(max(l.principal + l.interest - l.amount_repaid, 0)),
            "status": ("overdue" if l.status == Loan.ACTIVE and l.due_date < now else l.status),
            "due": l.due_date.strftime("%b %d, %Y"),
            "user_id": l.user_id,
        }
        for l in Loan.objects.select_related("user").order_by("-created")[:PAGE]
    ]
    savings = [
        {
            "id": s.reference,
            "user": (f"{s.user.first_name} {s.user.last_name}".strip() or s.user.username),
            "principal": float(s.principal),
            "rate": f"{s.rate * 100:.1f}% p.a.",
            "maturity": s.matures_at.strftime("%b %d, %Y"),
            "payout": float(s.principal + s.interest),
            "status": ("paid" if s.paid_out else ("matured" if s.matures_at <= now else "active")),
        }
        for s in FixedSave.objects.select_related("user").order_by("-created")[:PAGE]
    ]
    matured_due = FixedSave.objects.filter(paid_out=False, matures_at__lte=now, status=FixedSave.ACTIVE).count()
    cards = [
        {
            "id": c.id,
            "user": (f"{c.user.first_name} {c.user.last_name}".strip() or c.user.username),
            "last4": c.last4,
            "cur": "NGN",
            "bal": float(c.balance),
            "status": c.status,
        }
        for c in VirtualCard.objects.select_related("user").order_by("-created")[:PAGE]
    ]
    return ok(loans=loans, savings=savings, cards=cards, matured_due=matured_due)


@api
@require_cap("users")
def card_action(request):
    card = VirtualCard.objects.filter(id=request.data.get("card_id")).first()
    if card is None:
        return fail("Card not found", status=404)
    before = {"status": card.status}
    card.status = VirtualCard.ACTIVE if card.status == VirtualCard.FROZEN else VirtualCard.FROZEN
    card.save(update_fields=["status"])
    record_audit("card.freeze_toggle", actor=request.user_obj, target=f"card:{card.id}",
                 before=before, after={"status": card.status})
    return ok(success=True, status=card.status)


@api
@require_cap("money")
def loan_remind(request):
    loan = Loan.objects.select_related("user").filter(reference=request.data.get("reference")).first()
    if loan is None:
        return fail("Loan not found", status=404)
    link = WhatsAppLink.objects.filter(user=loan.user, status=WhatsAppLink.ACTIVE).first()
    if link is None:
        return fail("Borrower has no linked WhatsApp", status=409)
    outstanding = max(loan.principal + loan.interest - loan.amount_repaid, Decimal("0"))
    wa_reply(link.wa_msisdn,
             f"Hi {loan.user.first_name or 'there'}, a reminder from Zitch: your loan "
             f"({loan.reference}) has ₦{outstanding:,.2f} outstanding, due {loan.due_date:%b %d}. "
             "Open the app to repay.")
    record_audit("loan.reminder", actor=request.user_obj, target=loan.reference)
    return ok(success=True)


@api
@require_cap("money")
def maturities_run(request):
    n = run_maturities_service()
    record_audit("recon.maturities_run", actor=request.user_obj, after={"paid_out": n})
    return ok(success=True, paid_out=n)


@api
@require_cap("money")
def recon_run(request):
    """Requery + settle every provider-pending purchase — the cron's loop, on demand."""
    cutoff = timezone.now() - timedelta(minutes=5)
    # VTU.ng purchases only; bank-transfer payouts settle via the disbursement
    # webhook, not a VTU requery (see wallet.services.pending_vtu_purchases).
    pending = list(pending_vtu_purchases(cutoff))
    settled = 0
    for txn in pending:
        if settle_or_refund(txn, vtu_requery(txn.reference)) != "pending":
            settled += 1
    record_audit("recon.vtu_run", actor=request.user_obj,
                 after={"checked": len(pending), "settled": settled})
    return ok(success=True, settled=settled)


# --------------------------------------------------------------------------- #
# WhatsApp inbox / AI / broadcasts
# --------------------------------------------------------------------------- #
@api
@require_cap()
def inbox(request):
    msisdns = list(
        WaMessageLog.objects.order_by().values_list("msisdn", flat=True).distinct()[:50]
    )
    states = {c.msisdn: c for c in ConversationState.objects.filter(msisdn__in=msisdns)}
    links = {
        l.wa_msisdn: l
        for l in WhatsAppLink.objects.filter(wa_msisdn__in=msisdns, status=WhatsAppLink.ACTIVE).select_related("user")
    }
    rows = []
    for m in msisdns:
        last = WaMessageLog.objects.filter(msisdn=m).order_by("-created").first()
        st = states.get(m)
        link = links.get(m)
        rows.append({
            "msisdn": m,
            "user": (f"{link.user.first_name} {link.user.last_name}".strip() or link.user.username) if link else "(unlinked)",
            "status": (st.status if st else "bot"),
            "aiEnabled": (st.ai_enabled if st else True),
            "agent": (st.assigned_agent.username if st and st.assigned_agent else None),
            "last": last.created.isoformat() if last else None,
        })
    rows.sort(key=lambda r: r["last"] or "", reverse=True)
    return ok(rows=rows)


@api
@require_cap()
def thread(request):
    msisdn = (request.data.get("msisdn") or "").strip()
    if not msisdn:
        return fail("msisdn required")
    msgs = [
        {
            "dir": m.direction.lower(),
            "text": m.text,
            "t": m.created.isoformat(),
            "intent": m.intent_json or None,
            "flagged": m.flagged,
            "agent": m.text.startswith("[Agent"),
        }
        for m in WaMessageLog.objects.filter(msisdn=msisdn).order_by("created")[:200]
    ]
    return ok(msgs=msgs)


@api
@require_cap("wa")
def conv_ai(request):
    msisdn = (request.data.get("msisdn") or "").strip()
    if not msisdn:
        return fail("msisdn required")
    convo = ConversationState.for_msisdn(msisdn)
    before = {"ai_enabled": convo.ai_enabled}
    convo.ai_enabled = bool(request.data.get("enabled"))
    convo.save(update_fields=["ai_enabled", "updated"])
    record_audit("conversation.ai_toggle", actor=request.user_obj, target=f"wa:{msisdn}",
                 before=before, after={"ai_enabled": convo.ai_enabled})
    return ok(success=True)


@api
@require_cap()
def broadcasts(request):
    rows = [
        {
            "id": b.id,
            "template": b.template_name,
            "category": b.category,
            "status": b.status,
            "created": b.created.strftime("%b %d, %Y"),
            "by": (b.created_by.email or b.created_by.username) if b.created_by else "system",
            "queued": b.count_queued, "sent": b.count_sent, "delivered": b.count_delivered,
            "read": b.count_read, "failed": b.count_failed,
        }
        for b in Broadcast.objects.select_related("created_by").order_by("-created")[:PAGE]
    ]
    return ok(
        rows=rows,
        opted_in=WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE, marketing_opt_in=True).count(),
        linked=WhatsAppLink.objects.filter(status=WhatsAppLink.ACTIVE).count(),
    )


@api
@require_cap()
def ai_state(request):
    intents = [
        {
            "msisdn": m.msisdn,
            "text": m.text,
            "intent": m.intent_json,
            "t": m.created.isoformat(),
        }
        for m in WaMessageLog.objects.exclude(intent_json={}).order_by("-created")[:25]
    ]
    return ok(
        enabled=SystemSetting.get("ai_enabled_global", "true") != "false",
        intents=intents,
    )


@api
@require_cap("ai")
def ai_global(request):
    enabled = bool(request.data.get("enabled"))
    before = SystemSetting.get("ai_enabled_global", "true")
    SystemSetting.set("ai_enabled_global", "true" if enabled else "false")
    record_audit("ai.global_toggle", actor=request.user_obj, target="ai_enabled_global",
                 before={"enabled": before}, after={"enabled": enabled})
    return ok(success=True, enabled=enabled)


# --------------------------------------------------------------------------- #
# audit / recon / settings
# --------------------------------------------------------------------------- #
def _audit_row(a, emails):
    return {
        "actor": emails.get(a.actor_id, a.actor_id or "system"),
        "role": a.actor_type,
        "action": a.action,
        "target": a.target,
        "before": a.before,
        "after": a.after,
        "t": a.created.isoformat(),
    }


def _actor_emails(rows):
    ids = {a.actor_id for a in rows if a.actor_id}
    return {
        str(u.id): (u.email or u.username)
        for u in User.objects.filter(id__in=[i for i in ids if i.isdigit()])
    }


@api
@require_cap()
def audit(request):
    q = (request.data.get("q") or "").strip()
    qs = AuditLog.objects.order_by("-created")
    if q:
        qs = qs.filter(Q(action__icontains=q) | Q(target__icontains=q) | Q(actor_id__icontains=q))
    rows = list(qs[:PAGE])
    return ok(rows=[_audit_row(a, _actor_emails(rows)) for a in rows])


@api
@require_cap()
def recon(request):
    rows = list(
        AuditLog.objects.filter(
            Q(action__startswith="webhook.") | Q(action__startswith="recon.")
        ).order_by("-created")[:PAGE]
    )
    return ok(rows=[_audit_row(a, _actor_emails(rows)) for a in rows], providers=_providers())


SETTING_DESCRIPTIONS = {
    "ai_enabled_global": "Master switch for the WhatsApp AI intent layer. Off ⇒ channel is fully menu-driven.",
    "fx_margin_bps": "Margin added over the provider rate on every conversion quote.",
    "fx_corridor_usd_enabled": "NGN/USD settlement corridor.",
    "fx_corridor_gbp_enabled": "NGN/GBP settlement corridor.",
    "fx_corridor_cad_enabled": "NGN/CAD settlement corridor.",
}


@api
@require_cap()
def settings_view(request):
    keys = sorted(set(SETTING_DESCRIPTIONS) | set(SystemSetting.objects.values_list("key", flat=True)))
    rows = [
        {"key": k, "value": SystemSetting.get(k, ""), "desc": SETTING_DESCRIPTIONS.get(k, "")}
        for k in keys
    ]
    team = [
        {
            "name": (f"{u.first_name} {u.last_name}".strip() or u.username),
            "email": u.email or u.username,
            "role": role_of(u),
        }
        for u in User.objects.filter(is_staff=True, is_active=True).order_by("username")
    ]
    perms = [
        {"perm": label, **{r: CAPS[r][cap] for r in ROLES}}
        for label, cap in [
            ("Reply / handover WhatsApp chats", "wa"),
            ("Send broadcasts", "broadcast"),
            ("Refund / requery transactions", "money"),
            ("Edit FX margin & corridors", "money"),
            ("Freeze users / KYC reviews", "users"),
            ("AI kill switch", "ai"),
            ("Manage team & settings", "settings"),
        ]
    ]
    return ok(settings=rows, team=team, perms=perms, roles=list(ROLES))
