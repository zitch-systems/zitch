"""Deployment evidence for the configured banking services, without payments."""
import json

from django.core.cache import cache
from django.core.management.base import BaseCommand

from banking.accounts import account_payload, sandbox_tester, spendable, update_account
from banking.client import Client, ProviderError, config, configured, customer_enabled, sandbox
from banking.models import BankAccount
from banking.payments import digest
from banking.uat import sandbox_checks


class Command(BaseCommand):
    help = "Check service connectivity and refresh enrolled account status without sending payments."

    def handle(self, *args, **options):
        if not sandbox() or not customer_enabled():
            self.stdout.write(json.dumps({"bank_service_check": "not_applicable"}))
            return
        keys = [Client(product)._cache_key() for product in ("waas", "vas") if configured(product)]
        key = "bank-service-check:v1:" + digest(keys)
        report = cache.get(key)
        if report is not None:
            self.stdout.write(json.dumps(report, sort_keys=True))
            return
        if not cache.add(key + ":lease", True, timeout=180):
            self.stdout.write(json.dumps({"bank_service_check": "in_progress"}))
            return
        try:
            bank = next((row for row in BankAccount.objects.filter(environment="sandbox")
                .select_related("wallet__user").order_by("pk") if sandbox_tester(row.wallet.user)), None)
            fixtures = {"account_number": bank.number, "mobile": bank.wallet.user.phone} if bank else {}
            report = {"bank_service_check": "complete", **sandbox_checks(fixtures),
                      "mode": "provider_reads_and_status_refresh"}
            if bank:
                try:
                    update_account(bank, timeout=(2, 5))
                    bank.wallet.refresh_from_db()
                    status = account_payload(bank.wallet.user)
                    report["account_readiness"] = {
                        "bank_tier": bank.tier,
                        "balance_matches": spendable(bank.wallet) == bank.available_balance,
                        "spending_available": status["spending_available"],
                        "transfers_available": status["transfers_available"],
                        "bill_payments_available": status["bill_payments_available"],
                        "opening_review_required": bank.opening_review_required,
                    }
                except ProviderError as exc:
                    report["account_readiness"] = {"confirmed": False, "reason": exc.code}
            cache.set(key, report, timeout=600)
            self.stdout.write(json.dumps(report, sort_keys=True))
        finally:
            cache.delete(key + ":lease")
