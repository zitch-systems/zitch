"""Reconcile Wema/ALAT money movement that has no webhook â€” inbound funding AND
outbound payout settlement.

ALAT exposes NO webhooks, so two things must be polled:

1. FUNDING (credits): a bank transfer into a user's Wema NUBAN is invisible until
   we poll. This sweeps each Wema-provisioned wallet's transaction history over a
   recent window and credits every inbound (``creditType == "Credit"``) deposit â€”
   idempotent on Wema's ``referenceId`` (stored under a ``WEMA-CR-`` ledger key), so
   re-polling the same window never double-credits.

2. PAYOUTS (settlement): a Wema transfer returned PENDING/PROCESSING has no
   disbursement webhook to settle it. This polls
   confirm_transfer_status for each PENDING bank payout and settles (SUCCESS) or
   reverses (FAILED) it â€” the settlement safety net behind the payout flow. Only runs when
   Wema is the payout rail.

Schedule every few minutes (see render.yaml); each phase only does work when Wema
is the relevant rail, so it's harmless otherwise.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from utility import wema
from utility.providers import payout_provider
from wallet.models import Wallet, WemaFaceSession, WemaProvisioningAttempt
from wallet.services import (
    apply_wema_credit, attach_existing_bank_account, pending_bank_payouts,
    reverse_transfer, self_payout_references, settle_payout,
    wema_provisioned_wallets,
)

class Command(BaseCommand):
    help = "Poll Wema for inbound deposits (credit) and PENDING payout settlement (no webhooks)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--lookback-days", type=int, default=2,
            help="Days of history to scan per wallet (default: 2). Idempotent, so overlap is safe.",
        )
        parser.add_argument(
            "--payout-older-than-minutes", type=int, default=2,
            help="Only settle payouts at least this old (default: 2).",
        )
        parser.add_argument(
            "--account-recovery-limit", type=int, default=20,
            help="Maximum recent async account creations to recover per run (default: 20).",
        )

    def handle(self, *args, **options):
        # A crashed reconcile cron is the single thing you most want paged: it is
        # the only settlement path (no webhooks), so silent death means deposits
        # stop crediting and payouts stop settling. Capture + re-raise so both
        # Sentry and the platform's nonzero-exit alerting fire.
        from utility.alerts import alert
        try:
            self._run(**options)
        except Exception:  # noqa: BLE001 — observability wrapper; original error re-raised
            alert("reconcile_wema: run crashed", level="fatal", exc=True)
            raise

    def _run(self, **options):
        today = timezone.now().date()
        date_to = today.strftime("%Y-%m-%d")
        date_from = (today - timedelta(days=max(0, options["lookback_days"]))).strftime("%Y-%m-%d")

        # Phase 0 — recover NUBANs whose asynchronous Account Creation callback
        # was delayed or missed. Wema's OTP and face endpoints can return PENDING:
        # account generation completes later, and the documented callback is the
        # normal delivery path. A lost callback must not leave a verified customer
        # permanently numberless or make them disclose their BVN again.
        #
        # Only sessions that prove an account-creation attempt are eligible. This
        # avoids probing every verified customer forever, bounds gateway traffic,
        # and never needs the raw BVN/NIN (which we deliberately do not retain).
        recovery_limit = max(0, min(int(options["account_recovery_limit"]), 100))
        recovery_since = timezone.now() - timedelta(
            days=max(1, int(getattr(settings, "WEMA_ACCOUNT_RECOVERY_DAYS", 7) or 7)))
        candidates = []
        candidate_users = set()

        def add_candidate(row):
            if len(candidates) >= recovery_limit or row.user_id in candidate_users:
                return
            wallet = Wallet.objects.filter(user_id=row.user_id).only("account_number").first()
            if wallet is not None and wallet.account_number:
                return
            candidate_users.add(row.user_id)
            candidates.append((row.user, row.identity_type, type(row).__name__))

        for session in (WemaFaceSession.objects
                        .filter(status=WemaFaceSession.VERIFIED,
                                updated__gte=recovery_since)
                        .select_related("user").order_by("-updated")[:recovery_limit]):
            add_candidate(session)
        remaining = max(0, recovery_limit - len(candidates))
        if remaining:
            for attempt in (WemaProvisioningAttempt.objects
                            .filter(status=WemaProvisioningAttempt.PENDING,
                                    updated__gte=recovery_since)
                            .select_related("user").order_by("-updated")[:remaining]):
                add_candidate(attempt)

        recovery_checked = 0
        recovered_accounts = 0
        recovery_failures = 0
        for user, identity_type, source in candidates:
            recovery_checked += 1
            recovered, detail = attach_existing_bank_account(
                user, using_bvn=identity_type == WemaProvisioningAttempt.BVN)
            if recovered is not None and recovered.account_number:
                recovered_accounts += 1
                # This is operational completion of the account-creation request,
                # not a new KYC assertion. User BVN/NIN flags are untouched.
                WemaProvisioningAttempt.objects.filter(
                    user=user, identity_type=identity_type,
                    status=WemaProvisioningAttempt.PENDING,
                ).update(status=WemaProvisioningAttempt.VERIFIED)
                self.stdout.write(
                    f"wema_account_recovered user={user.id} source={source}")
            else:
                recovery_failures += 1
                self.stderr.write(
                    f"wema_account_recovery_pending user={user.id} source={source} "
                    f"detail={detail}")

        # Phase 1 â€” inbound funding credits.
        scanned = 0
        credited = 0
        fetch_failures = 0
        pnd_lifted = 0
        pnd_failures = 0
        # date_from/date_to are sent in the format the spec EXAMPLES show, never
        # confirmed against a live response: apply_wema_credit matches rows by
        # referenceId, not by date, so nothing here has ever needed to read a date
        # back off a row. A wrong format guess would not error either â€” it would
        # silently ask for the wrong window and quietly credit nothing (or too
        # much). Logging the field NAMES of one real row, once per run, turns that
        # from a question for Wema into something confirmed by our own log the
        # first time this runs for real, rather than a guess that could misparse a
        # genuine value.
        shape_logged = False
        for wallet in wema_provisioned_wallets():
            scanned += 1
            # Account creation and PND lifting are separate bank calls. A transient
            # failure in the second must not permanently strand outgoing funds.
            if not wallet.pnd_lifted:
                pnd = wema.lift_debit_restriction(wallet.account_number)
                if pnd.get("success"):
                    wallet.pnd_lifted = True
                    wallet.save(update_fields=["pnd_lifted", "updated"])
                    pnd_lifted += 1
                else:
                    pnd_failures += 1
                    self.stderr.write(
                        f"wema_pnd_lift_retry_failed account={wallet.account_number} "
                        f"message={pnd.get('message', '')}"
                    )
            res = wema.get_transactions(wallet.account_number, date_from, date_to)
            if not res.get("success"):
                fetch_failures += 1
                diag = res.get("diagnostic") or {}
                self.stderr.write(
                    "wema_history_fetch_failed "
                    f"account={wallet.account_number} "
                    f"http_status={diag.get('http_status')} "
                    f"gateway_status_code={diag.get('gateway_status_code')} "
                    f"gateway_code={diag.get('gateway_code')} "
                    f"gateway_successful={diag.get('gateway_successful')} "
                    f"message={res.get('message')}"
                )
                continue
            # The user's own payout references, fetched once per wallet: a credit
            # row matching one is a payout REVERSAL (routed through
            # reverse_transfer inside apply_wema_credit), never a funding credit.
            self_refs = self_payout_references(wallet.user)
            rows = res.get("transactions", []) or []
            if rows and not shape_logged:
                self.stdout.write(f"transhistoryV2 row shape (field names only, "
                                  f"once per run): {sorted(rows[0].keys())}")
                shape_logged = True
            for tx in rows:
                if apply_wema_credit(wallet, tx, self_refs=self_refs) is not None:
                    credited += 1

        # Phase 2 â€” settle PENDING payouts (only when Wema is the payout rail, so we
        # payout_provider() is wema, the sole rail).
        settled = 0
        reversed_ = 0
        payouts_seen = 0
        status_failures = 0
        if payout_provider() == "wema":
            cutoff = timezone.now() - timedelta(minutes=max(0, options["payout_older_than_minutes"]))
            for txn in pending_bank_payouts(cutoff):
                payouts_seen += 1
                transfer_meta = (txn.meta or {}).get("wema_transfer") or {}
                platform_reference = str(
                    transfer_meta.get("platform_reference") or "")
                st = wema.confirm_transfer_status(
                    txn.reference, platform_reference=platform_reference)
                status = (st.get("status") or "").upper()
                outcome = wema.classify_transfer_status(status, envelope_ok=True)
                if st.get("success") and outcome == "success":
                    if settle_payout(txn.reference) is not None:
                        settled += 1
                elif not st.get("pending") and outcome == "failed" and status:
                    if reverse_transfer(txn.reference) is not None:
                        reversed_ += 1
                elif not st.get("pending") and not status:
                    status_failures += 1
                # Unknown/in-flight/unreachable: leave PENDING for the next run.

        from whatsapp.ops import record_audit
        record_audit("recon.wema_run", actor_type="system",
                     after={"wallets": scanned, "credited": credited,
                            "accounts_recovery_checked": recovery_checked,
                            "accounts_recovered": recovered_accounts,
                            "account_recovery_pending": recovery_failures,
                            "payouts_settled": settled, "payouts_reversed": reversed_,
                            "fetch_failures": fetch_failures, "status_failures": status_failures,
                            "pnd_lifted": pnd_lifted, "pnd_failures": pnd_failures})

        # Systemic-outage signal: individual transient failures are expected and
        # left PENDING for the next run, but when there was work to do and EVERY
        # gateway call failed, that's an auth/connectivity outage (not a quiet
        # no-op) â€” page it so "nothing is crediting" doesn't go unnoticed.
        from utility.alerts import alert
        if scanned and fetch_failures == scanned:
            alert(f"reconcile_wema: all {scanned} wallet history fetches failed â€” Wema "
                  f"unreachable or auth rejected; no deposits can be detected",
                  level="error", wallets=scanned)
            raise SystemExit(1)
        # A payout stuck PENDING is invisible to every other control. It is a
        # pending DEBIT, so integrity_check counts it as owed, reconcile_balances
        # sees only the benign bank-over-ledger direction, and settlement_report
        # reads it as a surplus. Nothing anywhere says "this customer's money left
        # and never arrived", and the first signal is the customer complaining.
        #
        # Leaving an unrecognised status PENDING for the next run is right in the
        # minutes after a send and wrong after hours: by then the poller has had
        # dozens of attempts and is not going to resolve it on its own. This is
        # also the safety net under the settlement paths themselves, which is why
        # it is worth more than its frequency suggests.
        stuck_after = timedelta(hours=int(getattr(settings, "WEMA_PAYOUT_STUCK_HOURS", 2) or 2))
        stuck = list(pending_bank_payouts(timezone.now() - stuck_after)[:50])
        if stuck:
            alert(f"reconcile_wema: {len(stuck)} bank payout(s) still PENDING after "
                  f"{stuck_after} - the customer is debited and the money has not "
                  f"settled or reversed; no other control reports this",
                  level="error", payouts=len(stuck),
                  references=[t.reference for t in stuck[:10]])

        # Never infer failure from age. Wema may have completed a payout even
        # when its status endpoint is unavailable or returns an unfamiliar value.
        # Such rows remain PENDING, are repeatedly queried, and stay covered by
        # the stuck-payout alert above until a terminal bank status is received.

        if payouts_seen and status_failures == payouts_seen:
            alert(f"reconcile_wema: all {payouts_seen} pending-payout status queries failed â€” "
                  f"settlement stalled", level="error", payouts=payouts_seen)

        from wallet.alerts import retry_pending_whatsapp_alerts
        whatsapp_alerts = retry_pending_whatsapp_alerts(
            since=timezone.now() - timedelta(days=max(1, options["lookback_days"])),
            limit=50,
        )

        self.stdout.write(
            f"Wema reconcile: accounts recovered {recovered_accounts}/"
            f"{recovery_checked} checked ({recovery_failures} still pending); "
            f"{credited} credit(s) / {scanned} wallet(s); "
            f"PND lifted {pnd_lifted}, retry failures {pnd_failures}; "
            f"payouts checked {payouts_seen}, settled {settled}, reversed {reversed_}; "
            f"WhatsApp alerts retried {whatsapp_alerts}")
