"""Compliance services: transaction monitoring, data-subject handling, disputes.

Everything here is either read-only or edits customer-identifying fields. Nothing in
this module moves money — deliberately. A dispute is a claim, not a refund, and an AML
case is a flag, not a freeze; wiring either straight to the ledger would turn a
customer-raised form into a way to move value.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

log = logging.getLogger("zitch.security")

# From the policy's own monitoring table (compliance QA §"Transaction Monitoring"):
# "threshold breaches (₦5M+), rapid velocity, structuring, layering, PEP activity".
THRESHOLD_NGN = Decimal(str(getattr(settings, "AML_THRESHOLD_NGN", "5000000")))
# Structuring: several movements each just UNDER the threshold, adding up past it, in a
# short window. The band is what makes it structuring rather than ordinary activity —
# amounts nowhere near the threshold are just spending.
STRUCTURING_BAND = Decimal("0.5")   # count rows >= 50% of the threshold
STRUCTURING_MIN_ROWS = 3
# SEVEN days, not 24 hours, and this is the interesting part.
#
# Under the current tier ladder the maximum a Tier-3 customer can move in one day is
# ₦5,000,000 — which is the AML threshold itself. So a 24-hour structuring window is
# mathematically unreachable: rows in the ₦2.5m–₦5m band cannot sum past ₦5m inside a day
# that is itself capped at ₦5m. A rule that can never fire is worse than no rule, because
# it reads as coverage.
#
# A multi-day window is also the better description of structuring: splitting a large
# movement across days is exactly the behaviour, and doing it across days is what evades
# a per-day cap. See docs/compliance-operations.md.
STRUCTURING_WINDOW_HOURS = int(getattr(settings, "AML_STRUCTURING_WINDOW_HOURS", 168) or 168)
# Velocity: distinct from the real-time brake in common.http (which stops a drain in
# progress). This looks for a day that was busy enough to be worth a human's attention
# even though every single movement was allowed.
VELOCITY_WINDOW_HOURS = 24
VELOCITY_MIN_ROWS = int(getattr(settings, "AML_VELOCITY_MIN_ROWS_24H", 40) or 40)


# --------------------------------------------------------------------------- #
# AML cases
# --------------------------------------------------------------------------- #
def open_case(user, trigger, *, evidence=None, narrative="", dedupe_key="",
              detected=None):
    """Open an AML case, or return the existing one for `dedupe_key`.

    Deduping matters because the scanner is a cron: without it, every run would raise
    the same case again and a real finding would drown in copies of itself.
    """
    from compliance.models import AmlCase
    from whatsapp.ops import record_audit

    if dedupe_key:
        existing = AmlCase.objects.filter(dedupe_key=dedupe_key).first()
        if existing is not None:
            return existing
    case = AmlCase.objects.create(
        user=user, trigger=trigger, evidence=evidence or {}, narrative=narrative,
        dedupe_key=dedupe_key, detected=detected or timezone.now())
    record_audit("aml.case_opened", actor_type="system", target=f"u_{user.pk}",
                 after={"case": case.pk, "trigger": trigger,
                        "due": case.due.isoformat(), "evidence": evidence or {}})
    log.warning("aml_case_opened id=%s user=%s trigger=%s due=%s",
                case.pk, user.pk, trigger, case.due.date())
    return case


def close_case(case, *, status, actor=None, narrative="", nfiu_reference=""):
    """Close a case as filed or dismissed. Both require a narrative.

    A dismissal with no reason is indistinguishable from an unworked case, and the
    reason is the only thing an examiner can actually review.
    """
    from compliance.models import AmlCase
    from whatsapp.ops import record_audit

    if status not in (AmlCase.FILED, AmlCase.DISMISSED):
        raise ValueError("A case closes as filed or dismissed")
    if not narrative.strip():
        raise ValueError("A closing narrative is required")
    if status == AmlCase.FILED and not nfiu_reference.strip():
        raise ValueError("Filing requires the NFIU/goAML reference")
    case.status = status
    case.narrative = narrative
    case.nfiu_reference = nfiu_reference
    case.closed = timezone.now()
    case.closed_by = actor
    case.save(update_fields=["status", "narrative", "nfiu_reference", "closed", "closed_by"])
    record_audit(f"aml.case_{status}", actor=actor, target=f"case_{case.pk}",
                 after={"narrative": narrative[:300], "nfiu_reference": nfiu_reference,
                        "filed_on_time": case.closed <= case.due})
    return case


def scan_transactions(*, since=None):
    """Run the documented monitoring rules over recent ledger activity.

    A cron, not a hook in the money path. Transaction monitoring that runs inside a
    debit either blocks money on a heuristic or has to be made non-blocking anyway, and
    a rule that raises a case is not something to hold a wallet lock for. Read-only over
    the ledger; the only writes are cases.

    Returns a dict of counts.
    """
    from wallet.models import Transaction

    now = timezone.now()
    # Each rule has its own window, so the query spans the WIDEST of them and every rule
    # then filters down. `since` narrows only the threshold rule's freshness accounting —
    # the pattern rules always need their full window, or a cron running hourly would see
    # an hour of activity and conclude there was no pattern.
    widest = now - timedelta(hours=max(STRUCTURING_WINDOW_HOURS, VELOCITY_WINDOW_HOURS))
    since = since or (now - timedelta(hours=24))
    rows = list(Transaction.objects.filter(
        created__gte=min(widest, since), direction=Transaction.OUT, currency="NGN",
    ).exclude(transaction_status=Transaction.FAILED).select_related("user"))

    counts = {"scanned": len(rows), "threshold": 0, "structuring": 0, "velocity": 0}

    # 1. A single movement at or above the threshold.
    for txn in rows:
        if txn.created >= since and txn.amount >= THRESHOLD_NGN:
            open_case(
                txn.user, "threshold",
                evidence={"reference": txn.reference, "amount": str(txn.amount),
                          "service": txn.service, "at": txn.created.isoformat()},
                dedupe_key=f"threshold:{txn.reference}")
            counts["threshold"] += 1

    by_user = {}
    for txn in rows:
        by_user.setdefault(txn.user, []).append(txn)

    band = THRESHOLD_NGN * STRUCTURING_BAND
    structuring_from = now - timedelta(hours=STRUCTURING_WINDOW_HOURS)
    velocity_from = now - timedelta(hours=VELOCITY_WINDOW_HOURS)
    day = now.date().isoformat()
    for user, user_rows in by_user.items():
        # 2. Structuring: several movements each just under the threshold, together over
        #    it, spread across the window.
        near = [t for t in user_rows
                if t.created >= structuring_from and band <= t.amount < THRESHOLD_NGN]
        if len(near) >= STRUCTURING_MIN_ROWS and sum(t.amount for t in near) >= THRESHOLD_NGN:
            open_case(
                user, "structuring",
                evidence={"rows": len(near), "total": str(sum(t.amount for t in near)),
                          "references": [t.reference for t in near][:20],
                          "window_hours": STRUCTURING_WINDOW_HOURS},
                # One case per user per day: the same pattern seen an hour later is the
                # same finding, and a fresh case would just bury the first.
                dedupe_key=f"structuring:{user.pk}:{day}")
            counts["structuring"] += 1

        # 3. Velocity over a day: every movement was individually allowed, but the day as
        #    a whole is worth a human's attention.
        recent = [t for t in user_rows if t.created >= velocity_from]
        if len(recent) >= VELOCITY_MIN_ROWS:
            open_case(
                user, "velocity",
                evidence={"rows": len(recent), "window_hours": VELOCITY_WINDOW_HOURS,
                          "total": str(sum(t.amount for t in recent))},
                dedupe_key=f"velocity:{user.pk}:{day}")
            counts["velocity"] += 1

    return counts


# --------------------------------------------------------------------------- #
# Data-subject requests
# --------------------------------------------------------------------------- #
# Every model holding personal data, and what an export must reach. Enumerated
# explicitly rather than walked by introspection: a reflective export silently gains
# whatever a future migration adds, which is the wrong direction for a document handed
# to a data subject — a new field should have to be considered, not appear.
def export_user_data(user) -> dict:
    """Everything held about `user`, as a JSON-safe dict.

    Verification HASHES are included as a fact of processing but never a raw identifier:
    the raw BVN/NIN is deliberately not retained (see the compliance QA's own claim that
    "only a verification hash is retained"), so there is nothing to return and the export
    says so rather than implying we hold it.
    """
    from banklink.models import LinkedBankAccount
    from cards.models import VirtualCard
    from compliance.models import Dispute
    from loans.models import Loan
    from savings.models import FixedSave
    from wallet.models import CurrencyWallet, Transaction, Wallet
    from whatsapp.models import WaMessageLog, WhatsAppLink

    def rows(qs, fields):
        return [{f: _jsonable(getattr(r, f, None)) for f in fields} for r in qs]

    wallet = Wallet.objects.filter(user=user).first()
    return {
        "generated": timezone.now().isoformat(),
        "notice": ("Raw BVN/NIN are not retained — only a one-way verification hash, "
                   "which is listed here as a fact of processing and cannot be reversed."),
        "account": {
            "id": user.pk, "username": user.username, "name": user.get_full_name(),
            "email": user.email, "phone": user.phone, "tier": user.tier,
            "date_joined": _jsonable(user.date_joined), "is_active": user.is_active,
            "address": user.address,
            "verification": {
                "bvn_verified": user.bvn_verified, "bvn_last4": user.bvn_last4,
                "nin_verified": user.nin_verified, "nin_last4": user.nin_last4,
                "face_verified": user.face_verified,
                "address_verified": user.address_verified,
                "id_document_type": user.id_document_type,
                "id_document_verified": user.id_document_verified,
            },
        },
        "wallet": {"account_number": getattr(wallet, "account_number", ""),
                   "balance": _jsonable(getattr(wallet, "balance", None))},
        "currency_wallets": rows(CurrencyWallet.objects.filter(user=user),
                                 ["currency", "balance"]),
        "transactions": rows(
            Transaction.objects.filter(user=user).order_by("created"),
            ["reference", "service", "amount", "currency", "direction",
             "transaction_status", "created"]),
        "linked_banks": rows(LinkedBankAccount.objects.filter(user=user),
                             ["bank_name", "account_number", "account_name", "status",
                              "created"]),
        "cards": rows(VirtualCard.objects.filter(user=user),
                      ["last4", "brand", "expiry", "holder", "status", "created"]),
        "savings": rows(FixedSave.objects.filter(user=user),
                        ["reference", "principal", "interest", "status", "created"]),
        "loans": rows(Loan.objects.filter(user=user),
                      ["reference", "principal", "interest", "amount_repaid", "status",
                       "due_date"]),
        "whatsapp": rows(WhatsAppLink.objects.filter(user=user),
                         ["wa_msisdn", "status", "marketing_opt_in", "linked_at"]),
        "whatsapp_messages": rows(
            WaMessageLog.objects.filter(
                msisdn__in=list(WhatsAppLink.objects.filter(user=user)
                                .values_list("wa_msisdn", flat=True))).order_by("created"),
            ["direction", "text", "created"]),
        "disputes": rows(Dispute.objects.filter(user=user),
                         ["reference", "reason", "status", "created", "resolution"]),
        "known_devices": rows(user.known_devices.all(),
                              ["device_id", "os", "model", "brand", "first_seen"]),
    }


# Identifiers cleared by an erasure, with what each is replaced by. The financial record
# is NOT touched: the policy commits to retaining transaction records for 5+ years, and
# NDPR erasure does not override a statutory retention obligation. What it does require
# is that the retained record stops identifying a person — hence pseudonymisation.
ERASE_FIELDS = ("first_name", "last_name", "email", "phone", "address", "avatar",
                "bvn_last4", "nin_last4", "id_document_type")


def erase_user_data(user, *, actor=None, note=""):
    """Pseudonymise `user` and record exactly what was cleared and what was kept.

    Not a delete. Deleting the row would take the ledger with it (or orphan it), and the
    5-year retention commitment is not ours to waive. So the identifiers go, the
    financial history stays attached to a pseudonymous id, and the account is deactivated
    so it cannot be used again.

    Returns the outcome dict that is stored on the DataSubjectRequest — the artifact an
    examiner or the data subject actually asks for.
    """
    from accounts.models import AccessToken, OperatorTotp
    from banklink.models import LinkedBankAccount
    from whatsapp.models import WhatsAppLink
    from whatsapp.ops import record_audit

    pseudonym = f"erased-{user.pk}"
    cleared = {}
    for field in ERASE_FIELDS:
        value = getattr(user, field, "")
        if value:
            cleared[field] = True
        # phone is unique and nullable: NULL, not "", or a second erasure collides.
        setattr(user, field, None if field == "phone" else "")
    user.username = pseudonym
    user.is_active = False
    # The verification hashes go too. They are not reversible, but they ARE stable
    # identifiers — the same BVN produces the same hash — so leaving them would let the
    # "erased" account be re-linked to a person by anyone who can hash a BVN.
    user.bvn_hash = ""
    user.nin_hash = ""
    user.transaction_pin = ""
    user.save()

    # Credentials and channel links cannot survive an erasure: a live session or an
    # active WhatsApp link would keep the account reachable as if nothing happened.
    tokens = AccessToken.objects.filter(user=user).delete()[0]
    links = WhatsAppLink.objects.filter(user=user).delete()[0]
    banks = LinkedBankAccount.objects.filter(user=user).delete()[0]
    OperatorTotp.objects.filter(user=user).delete()

    outcome = {
        "pseudonym": pseudonym,
        "cleared_fields": sorted(cleared),
        "verification_hashes_cleared": True,
        "deleted": {"access_tokens": tokens, "whatsapp_links": links,
                    "linked_banks": banks},
        "retained": {
            "ledger_transactions": "retained — 5+ year AML record-keeping commitment",
            "audit_log": "retained — immutable by design",
            "aml_cases": "retained — regulatory record",
        },
        "note": note,
    }
    record_audit("dsr.erased", actor=actor, target=pseudonym, after=outcome)
    log.warning("dsr_erased user=%s by=%s", user.pk, getattr(actor, "pk", "system"))
    return outcome


def record_request(*, user, kind, actor=None, note="", outcome=None, completed=False,
                   subject_label=""):
    """Create the DataSubjectRequest row for an export or erasure.

    `subject_label` must be passed for an ERASURE and captured BEFORE it runs: afterwards
    the user row no longer identifies anybody, and this label is the only handle left on
    the request.
    """
    from compliance.models import DataSubjectRequest

    req = DataSubjectRequest.objects.create(
        user=user, kind=kind, requested_by=actor, note=note[:300],
        subject_label=(subject_label
                       or (user.email or user.phone or user.username or f"u_{user.pk}"))[:120],
        outcome=outcome or {},
        status=DataSubjectRequest.COMPLETED if completed else DataSubjectRequest.RECEIVED,
        completed=timezone.now() if completed else None)
    return req


# --------------------------------------------------------------------------- #
# Disputes
# --------------------------------------------------------------------------- #
def open_dispute(user, *, reference, reason, detail=""):
    """Raise a dispute against one of the user's own transactions.

    The reference must belong to the caller. Without that check the endpoint would
    confirm whether an arbitrary reference exists, which is a lookup oracle over every
    customer's transaction ids.
    """
    from compliance.models import Dispute
    from wallet.models import Transaction
    from whatsapp.ops import record_audit

    reference = (reference or "").strip()
    if not reference:
        raise ValueError("A transaction reference is required")
    if reason not in dict(Dispute.REASONS):
        raise ValueError("Unknown dispute reason")
    if not Transaction.objects.filter(user=user, reference=reference).exists():
        raise ValueError("We can't find that transaction on your account")
    existing = Dispute.objects.filter(
        user=user, reference=reference,
        status__in=(Dispute.OPEN, Dispute.INVESTIGATING)).first()
    if existing is not None:
        return existing   # idempotent: a second tap must not open a second case
    dispute = Dispute.objects.create(user=user, reference=reference, reason=reason,
                                     detail=(detail or "")[:500])
    record_audit("dispute.opened", actor_type="user", target=reference,
                 after={"dispute": dispute.pk, "reason": reason, "due": dispute.due.isoformat()})
    return dispute


def resolve_dispute(dispute, *, status, actor=None, resolution=""):
    """Close a dispute with a reason. Moves no money — see the model docstring."""
    from compliance.models import Dispute
    from whatsapp.ops import record_audit

    if status not in (Dispute.UPHELD, Dispute.DECLINED, Dispute.WITHDRAWN):
        raise ValueError("A dispute closes as upheld, declined or withdrawn")
    if not resolution.strip():
        raise ValueError("A resolution note is required")
    dispute.status = status
    dispute.resolution = resolution[:500]
    dispute.resolved = timezone.now()
    dispute.resolved_by = actor
    dispute.save(update_fields=["status", "resolution", "resolved", "resolved_by"])
    record_audit(f"dispute.{status}", actor=actor, target=dispute.reference,
                 after={"dispute": dispute.pk, "resolution": dispute.resolution,
                        "on_time": dispute.resolved <= dispute.due})
    return dispute


def _jsonable(value):
    from datetime import date, datetime

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
