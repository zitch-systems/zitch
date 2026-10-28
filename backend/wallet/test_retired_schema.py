"""Rolling removal is gated on an empty schema and never erases money."""
import importlib

from django.apps import apps
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase
from django.utils import timezone

from wallet.funding_test_fixtures import funding_hold
from wallet.models import BillFundingBinding, Transaction, UnsettledFunding
from wallet.tests import make_user

detach = importlib.import_module("wallet.migrations.0030_detach_retired_funding")


class RetiredSchemaTests(TestCase):
    def old_apps(self):
        return MigrationLoader(connection).project_state([
            ("wallet", "0029_retired_funding_archive"),
            ("wema_vas", "0004_separate_account_modes")]).apps

    def test_runtime_models_and_bill_foreign_key_are_absent(self):
        self.assertEqual(list(apps.get_app_config("wema_vas").get_models()), [])
        self.assertNotIn("vas_account", {f.name for f in BillFundingBinding._meta.fields})

    def test_nonempty_schema_stops_removal_without_deleting_records(self):
        user, _ = make_user("08077770001", "schema-gate@example.test")
        old = self.old_apps()
        Account = old.get_model("wema_vas", "VirtualAccount")
        account = Account.objects.create(user_id=user.pk, number="9990000001", prefix="999",
            display_name="Zitch/Test", encrypted_identity="test", verification_reference="proof",
            consent_reference="consent", verified_at=timezone.now(), mode="live")
        with self.assertRaisesRegex(RuntimeError, "not empty"):
            detach.require_empty(old, connection.schema_editor())
        self.assertTrue(Account.objects.filter(pk=account.pk).exists())

    def test_empty_schema_accepts_outstanding_holds_without_mutating_them(self):
        user, _ = make_user("08077770002", "schema-money@example.test")
        hold = funding_hold(user, "100")
        ledger = list(Transaction.objects.values())
        detach.require_empty(self.old_apps(), connection.schema_editor())
        self.assertEqual(list(Transaction.objects.values()), ledger)
        self.assertTrue(UnsettledFunding.objects.filter(pk=hold.pk, amount="100").exists())
