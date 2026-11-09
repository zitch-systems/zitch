"""Who is stuck without a funding account, and what each one is waiting on.

Customers whose identity is verified but whose NUBAN was never issued are
invisible between alerts: `repair_missing_funding_accounts` logs one line per
sweep, and the per-user page fires at most once an hour. Neither answers the
question an operator actually has — *how many are stuck, and which of them need
a human to do something?* This command answers it in one read-only pass.

It is decision support, not a repair. It attaches nothing, calls no bank
endpoint, and writes nothing. In particular it does NOT resolve the
`existing_customer` case by looking a NUBAN up and attaching it: an
existing-account response does not prove the account belongs to THIS customer,
so attaching on it could bind someone else's bank account to a wallet. See
`wallet/wema_callbacks.py`, which refuses such responses for the same reason,
and `account_recovery._eligible`, which routes this state to reconciliation.
What that reconciliation needs from us is an accurate list, which is this.

Output carries user ids and bank-side state only — never a phone, email, holder
name or account number — so it is safe in a deploy log or a ticket. The archived
former-provider account NUMBERS live in `export_previous_bank_accounts`, which
writes them to a 0600 file outside the repository. This command only reports
whether such a row exists, because that is what decides the next action.

Usage
-----
    python manage.py partnership_recovery_worksheet
    python manage.py partnership_recovery_worksheet --json --limit 200
"""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.utils import timezone

#: Read-only; the archive is 9PSB-era evidence that may not exist at all on a
#: database that never carried it, so its presence is always checked first.
ARCHIVE_TABLE = "banking_previousbankarchive"

#: What an operator should do next, per bank-side state. Keyed by the
#: WemaFaceSession.account_failure_category the provider response produced.
NEXT_ACTION = {
    "existing_customer": (
        "Reconcile with the bank: it reports this customer already exists, so "
        "issuance cannot be retried and no lookup may be trusted to prove which "
        "account is theirs. Their former account number, if any, is in the "
        "export_previous_bank_accounts file."),
    "authentication_or_entitlement": (
        "Check the partnership credentials and product entitlement; this is a "
        "configuration fault, not a customer problem."),
    "invalid_identity": (
        "The bank rejected the identity. The customer needs a corrected BVN/NIN, "
        "not another attempt at the same one."),
    "invalid_phone": (
        "The bank rejected the phone number on the record. Correct it before any "
        "retry."),
    "invalid_correlation": (
        "The face correlation was not accepted. A fresh, authorized recovery "
        "session is required."),
    "recovery_lookup_unresolved": (
        "A recovery attempt ran but the bank lookup did not confirm 'no account'. "
        "Reconcile before authorizing another."),
}

DEFAULT_ACTION = (
    "No provider failure was recorded. Confirm whether issuance was ever "
    "submitted before authorizing a recovery.")


def _archived_wallet_ids(wallet_ids):
    """Wallet ids that still have former-provider archive evidence.

    Existence only: this never reads an account number. Returns an empty set on
    a database without the table, which is the normal case for a deployment that
    never carried the previous provider.
    """
    if not wallet_ids:
        return set()
    try:
        if ARCHIVE_TABLE not in set(connection.introspection.table_names()):
            return set()
        ids = sorted(wallet_ids)
        # Explicit placeholders rather than `IN %s`: psycopg expands a tuple
        # parameter, SQLite does not, and this runs under both.
        placeholders = ",".join(["%s"] * len(ids))
        with connection.cursor() as cur:
            cur.execute(
                f'SELECT wallet_id FROM "{ARCHIVE_TABLE}" WHERE wallet_id IN ({placeholders})',
                ids)
            return {row[0] for row in cur.fetchall()}
    except DatabaseError:
        # Evidence we cannot read is reported as unknown rather than as absent:
        # "no archive" and "could not check" lead to different operator actions.
        return None


class Command(BaseCommand):
    help = ("List customers whose identity is verified but who have no funding "
            "account, with what each is waiting on. Read-only; no account numbers.")

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json",
                            help="Machine-readable output (ids and states only).")
        parser.add_argument("--limit", type=int, default=200,
                            help="Maximum customers to report (default 200).")

    def handle(self, *args, **options):
        from wallet.models import WemaFaceSession, WemaProvisioningAttempt

        User = get_user_model()
        limit = max(1, min(int(options["limit"] or 200), 1000))
        now = timezone.now()

        users = list(User.objects.filter(is_active=True).filter(
            Q(bvn_verified=True) | Q(nin_verified=True)
        ).filter(
            Q(wallet__isnull=True) | Q(wallet__account_number="")
        ).order_by("id")[:limit])

        wallet_ids = {u.pk: w for u, w in
                      ((u, getattr(u, "wallet", None)) for u in users) if w}
        archived = _archived_wallet_ids({w.pk for w in wallet_ids.values()})

        rows = []
        for user in users:
            session = (WemaFaceSession.objects.filter(user=user)
                       .order_by("-created", "-pk").first())
            pending_otp = WemaProvisioningAttempt.objects.filter(
                user=user, status="pending").filter(
                Q(otp_verified_at__isnull=False) | Q(expires_at__gt=now)).exists()
            wallet = wallet_ids.get(user.pk)
            category = (session.account_failure_category or "") if session else ""
            state = (session.account_state or "") if session else ""

            if archived is None:
                evidence = "unknown"
            elif wallet is None:
                evidence = "no_wallet"
            else:
                evidence = "yes" if wallet.pk in archived else "no"

            if pending_otp:
                waiting_on = "an OTP setup that is still live"
                action = "Let the active OTP attempt finish or expire before anything else."
            elif session is None:
                waiting_on = "nothing; issuance was never started"
                action = ("Identity is verified but no issuance attempt exists. "
                          "Confirm the customer actually reached account setup.")
            elif state == "awaiting_callback":
                age = now - session.updated
                waiting_on = f"the bank's account callback ({int(age.total_seconds() // 60)}m)"
                action = ("Within the hour this is in flight; beyond it, treat the "
                          "callback as lost and reconcile."
                          if age > timedelta(hours=1) else
                          "In flight. No action yet.")
            elif category:
                waiting_on = f"a bank failure recorded as {category}"
                action = NEXT_ACTION.get(category, DEFAULT_ACTION)
            else:
                waiting_on = "review; identity is proven but no account exists"
                action = DEFAULT_ACTION

            rows.append({
                "user_id": user.pk,
                "identity": "bvn" if user.bvn_verified else "nin",
                "session_id": session.pk if session else None,
                "session_status": (session.status or "") if session else "",
                "account_state": state,
                "failure_category": category,
                "stuck_for_hours": (round((now - session.created).total_seconds() / 3600, 1)
                                    if session else None),
                "former_provider_evidence": evidence,
                "waiting_on": waiting_on,
                "next_action": action,
            })

        if options["as_json"]:
            self.stdout.write(json.dumps(
                {"read_only": True, "count": len(rows), "customers": rows},
                sort_keys=True))
            return

        if not rows:
            self.stdout.write(self.style.SUCCESS(
                "No verified customer is missing a funding account."))
            return

        self.stdout.write(f"{len(rows)} verified customer(s) have no funding account.")
        self.stdout.write("Read-only. No account numbers, names or contact details below.\n")
        for row in rows:
            age = f"{row['stuck_for_hours']}h" if row["stuck_for_hours"] is not None else "-"
            self.stdout.write(
                f"user {row['user_id']}  identity={row['identity']}  "
                f"session={row['session_id'] or '-'}  state={row['account_state'] or '-'}  "
                f"category={row['failure_category'] or '-'}  age={age}  "
                f"former_provider_evidence={row['former_provider_evidence']}")
            self.stdout.write(f"    waiting on: {row['waiting_on']}")
            self.stdout.write(f"    next:       {row['next_action']}\n")

        blocked = sum(1 for r in rows if r["failure_category"] == "existing_customer")
        if blocked:
            self.stdout.write(self.style.WARNING(
                f"{blocked} customer(s) are blocked on bank reconciliation and will "
                "not resolve on their own. Nothing in this system can issue them an "
                "account until the bank confirms which account is theirs."))
