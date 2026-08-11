import os
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, OTP, OperatorTotp
from transfers.models import Bank
from wallet.models import Transaction, Wallet
from whatsapp.models import AuditLog, SystemSetting, WaMessageLog, WaOnboarding, WebhookEvent


User = get_user_model()
NONCE = "reset-test-nonce-that-is-at-least-thirty-two-characters"


@override_settings(WEMA={"SIMULATION": True}, ALLOW_PRODUCTION_SIMULATION=True)
class ResetCustomerDataTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="operator", email="operator@zitch.test", password="safe-test-password",
            is_staff=True, is_superuser=True,
        )
        self.customer = User.objects.create_user(
            username="08011112222", phone="08011112222", email="customer@zitch.test",
        )
        Group.objects.create(name="support").user_set.add(self.staff)
        OperatorTotp.objects.create(user=self.staff, secret="JBSWY3DPEHPK3PXP")
        staff_token = AccessToken.issue(self.staff)
        customer_token = AccessToken.issue(self.customer)
        self.assertTrue(staff_token.pk and customer_token.pk)

        Wallet.objects.create(user=self.customer)
        Transaction.objects.create(
            user=self.customer, service="Seed", amount="100.00", direction=Transaction.IN,
            transaction_status=Transaction.SUCCESS, reference="reset-test-transaction",
        )
        OTP.issue(phone=self.customer.phone, code="123456")
        WaOnboarding.objects.create(
            msisdn="2348011112222", step="first_name",
            expires_at=timezone.now() + timedelta(minutes=15),
        )
        WaMessageLog.objects.create(
            msisdn="2348011112222", direction=WaMessageLog.IN, text="test",
        )
        WebhookEvent.objects.create(source="whatsapp", reference="reset-test-webhook")
        AuditLog.objects.create(action="reset.test.before")
        SystemSetting.objects.create(key="ai_enabled_global", value="true")
        Bank.objects.create(code="reset-bank", name="Reset Test Bank")
        cache.set("reset-test-key", "customer-state", timeout=300)

    def run_reset(self, *extra):
        with patch.dict(os.environ, {"CUSTOMER_RESET_NONCE": NONCE}, clear=False):
            call_command(
                "reset_customer_data", "--confirm", "CONFIRM CUSTOMER RESET",
                "--nonce", NONCE, *extra,
            )

    def test_dry_run_changes_nothing(self):
        self.run_reset("--dry-run")
        self.assertTrue(User.objects.filter(pk=self.customer.pk).exists())
        self.assertTrue(Transaction.objects.filter(user=self.customer).exists())
        self.assertEqual(SystemSetting.get("ai_enabled_global"), "true")

    def test_reset_removes_customer_data_and_preserves_platform_state(self):
        self.run_reset()

        self.assertFalse(User.objects.filter(pk=self.customer.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.staff.pk, is_superuser=True).exists())
        self.assertTrue(OperatorTotp.objects.filter(user=self.staff).exists())
        self.assertTrue(Group.objects.filter(name="support").exists())
        self.assertTrue(Bank.objects.filter(code="reset-bank").exists())
        self.assertEqual(SystemSetting.get("ai_enabled_global"), "true")
        self.assertFalse(AccessToken.objects.exists())
        self.assertFalse(OTP.objects.exists())
        self.assertFalse(WaMessageLog.objects.exists())
        self.assertFalse(WaOnboarding.objects.exists())
        self.assertFalse(WebhookEvent.objects.exists())
        self.assertIsNone(cache.get("reset-test-key"))
        self.assertEqual(
            AuditLog.objects.filter(action="customer_data.reset").count(), 1
        )

    def test_nonce_can_execute_only_once(self):
        self.run_reset()
        newcomer = User.objects.create_user(
            username="08033334444", phone="08033334444", email="new@zitch.test"
        )
        self.run_reset()
        self.assertTrue(User.objects.filter(pk=newcomer.pk).exists())
