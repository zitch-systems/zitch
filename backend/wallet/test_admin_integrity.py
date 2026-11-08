"""Generic Django admin must never create or rewrite financial evidence."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase, override_settings
from django.urls import path, reverse

from wallet.admin import FundingIntentAdmin, TransactionAdmin, WalletAdmin
from wallet.models import FundingIntent, Transaction, Wallet
from wallet.services import credit, get_or_create_wallet


# Isolate model-admin authorization from the separately tested production MFA
# site. These are the same registered ModelAdmin classes and real admin views.
site = AdminSite(name="wallet_integrity_admin")
site.register(Wallet, WalletAdmin)
site.register(Transaction, TransactionAdmin)
site.register(FundingIntent, FundingIntentAdmin)
urlpatterns = [path("admin/", site.urls)]


@override_settings(ROOT_URLCONF=__name__)
class FinancialAdminIntegrityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.operator = User.objects.create_superuser(
            username="wallet-auditor", email="wallet-auditor@zitch.test", password="test-password")
        self.customer = User.objects.create_user(
            username="wallet-customer", phone="08011112222", email="wallet-customer@zitch.test")
        self.wallet = get_or_create_wallet(self.customer)
        self.txn = credit(self.customer, Decimal("100"), "Audit fixture")
        self.wallet.refresh_from_db()
        self.wallet.account_number = "0100000001"
        self.wallet.bank_name = "Wema Bank"
        self.wallet.save(update_fields=["account_number", "bank_name"])
        self.funding = FundingIntent.objects.create(
            user=self.customer, reference="admin-funding-fixture", amount=Decimal("100"))
        self.client.force_login(self.operator)

    def url(self, model, operation, obj=None):
        return reverse(f"wallet_integrity_admin:wallet_{model._meta.model_name}_{operation}",
                       args=[obj.pk] if obj is not None else [])

    def records(self):
        return ((Wallet, self.wallet), (Transaction, self.txn), (FundingIntent, self.funding))

    def test_financial_records_remain_viewable_but_every_field_is_readonly(self):
        request = RequestFactory().get("/admin/")
        request.user = self.operator
        for model, obj in self.records():
            with self.subTest(model=model.__name__):
                self.assertEqual(self.client.get(self.url(model, "changelist")).status_code, 200)
                detail = self.client.get(self.url(model, "change", obj))
                self.assertEqual(detail.status_code, 200)
                self.assertNotContains(detail, 'name="_save"')
                self.assertTrue({field.name for field in model._meta.fields}.issubset(
                    site._registry[model].get_readonly_fields(request, obj)))
                self.assertNotIn("delete_selected", site._registry[model].get_actions(request))

    def test_forged_add_edit_and_delete_posts_cannot_mutate_even_as_superuser(self):
        forged = {"user": self.operator.pk, "balance": "999999.00", "amount": "999999.00",
                  "account_number": "0199999999", "bank_name": "Wema Bank (demo)",
                  "bank_tier": "3", "pnd_lifted": "on", "status": "paid", "credited": "on",
                  "transaction_status": Transaction.SUCCESS, "meta": '{"reconcile": false}',
                  "reference": "forged-admin-row", "service": "forged", "direction": "in",
                  "currency": "NGN", "post": "yes", "_save": "Save"}
        for model, obj in self.records():
            with self.subTest(model=model.__name__):
                before = model.objects.filter(pk=obj.pk).values().get()
                count = model.objects.count()
                for operation in ("add", "change", "delete"):
                    response = self.client.post(self.url(model, operation, None if operation == "add" else obj),
                                                data=forged)
                    self.assertEqual(response.status_code, 403, operation)
                self.client.post(self.url(model, "changelist"), {
                    "action": "delete_selected", "index": "0", "_selected_action": [obj.pk], "post": "yes"})
                self.assertEqual(model.objects.count(), count)
                self.assertEqual(model.objects.filter(pk=obj.pk).values().get(), before)

    def staff(self, username, *permissions):
        user = get_user_model().objects.create_user(username=username, is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="wallet", codename__in=permissions))
        return user

    def action(self, name, *wallets):
        return self.client.post(self.url(Wallet, "changelist"), {
            "action": name, "index": "0", "select_across": "0",
            "_selected_action": [wallet.pk for wallet in wallets]})

    def test_view_only_operator_cannot_forge_a_recovery_action(self):
        operator = self.staff("wallet-viewer", "view_wallet")
        Wallet.objects.filter(pk=self.wallet.pk).update(bank_name="Wema Bank (demo)")
        before = Wallet.objects.filter(pk=self.wallet.pk).values().get()
        self.client.force_login(operator)
        for action in ("clear_demo_account", "reconnect_bank_account"):
            with self.subTest(action=action), patch("wallet.admin.attach_existing_bank_account") as recover:
                response = self.action(action, self.wallet)
                self.assertIn(response.status_code, (200, 302))
                recover.assert_not_called()
                self.assertEqual(Wallet.objects.filter(pk=self.wallet.pk).values().get(), before)

    def test_named_demo_recovery_keeps_money_and_real_bank_accounts_intact(self):
        operator = self.staff("wallet-recovery", "view_wallet", "change_wallet")
        demo_user = get_user_model().objects.create_user(username="demo-wallet-customer")
        demo = get_or_create_wallet(demo_user)
        credit(demo_user, Decimal("200"), "Demo fixture")
        demo.account_number = "0100000002"
        demo.bank_name = "Wema Bank (demo)"
        demo.account_reference = "demo-reference"
        demo.save(update_fields=["account_number", "bank_name", "account_reference"])
        count = Transaction.objects.count()
        self.client.force_login(operator)
        response = self.action("clear_demo_account", demo, self.wallet)
        self.assertEqual(response.status_code, 302)
        demo.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(demo.account_number, "")
        self.assertEqual(demo.account_reference, "")
        self.assertEqual(demo.balance, Decimal("200"))
        self.assertEqual(self.wallet.account_number, "0100000001")
        self.assertEqual(self.wallet.balance, Decimal("100"))
        self.assertEqual(Transaction.objects.count(), count)
        # The same permission still cannot bypass the read-only change form.
        self.assertEqual(self.client.post(self.url(Wallet, "change", demo), {"balance": "900"}).status_code, 403)

    def test_recovery_rechecks_stale_demo_selection_before_clearing(self):
        self.wallet.bank_name = "Wema Bank (demo)"
        self.wallet.save(update_fields=["bank_name"])
        selected = Wallet.objects.filter(pk=self.wallet.pk)
        list(selected)  # cached operator selection before a real callback wins
        Wallet.objects.filter(pk=self.wallet.pk).update(bank_name="Wema Bank", account_number="0100000003")
        request = RequestFactory().post("/admin/")
        request.user = self.operator
        with patch.object(site._registry[Wallet], "message_user"):
            site._registry[Wallet].clear_demo_account(request, selected)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "0100000003")
        self.assertEqual(self.wallet.balance, Decimal("100"))

    def test_named_reconnect_keeps_callback_confirmation_guard(self):
        operator = self.staff("wallet-reconnect", "view_wallet", "change_wallet")
        Wallet.objects.filter(pk=self.wallet.pk).update(account_number="", bank_name="")
        self.client.force_login(operator)
        with patch("utility.wema.get_account_details") as bank_lookup, \
                patch("utility.wema.lift_debit_restriction") as lift:
            response = self.action("reconnect_bank_account", self.wallet)
        self.assertEqual(response.status_code, 302)
        bank_lookup.assert_not_called()
        lift.assert_not_called()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "")
        self.assertFalse(self.wallet.pnd_lifted)
        self.assertEqual(self.wallet.balance, Decimal("100"))
