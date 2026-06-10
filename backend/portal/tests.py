"""Operator portal tests: staff login, RBAC caps, audited mutations, web pages.

The portal is the money-control surface, so the tests pin the gates: non-staff
can never log in, read_only can read but not mutate, every mutation lands in
the audit log, and the FX corridor pause actually stops quotes.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase

from accounts.models import AccessToken
from wallet.forex import FxError, create_fx_quote
from wallet.services import credit, get_or_create_wallet
from whatsapp.models import AuditLog, SystemSetting

User = get_user_model()


def make_staff(username, role=None, superuser=False):
    u = User.objects.create(username=username, email=f"{username}@zitch.test",
                            is_staff=True, is_superuser=superuser)
    u.set_password("op-pass-123")
    u.save()
    if role:
        group, _ = Group.objects.get_or_create(name=role)
        u.groups.add(group)
    return u


class PortalTestCase(TestCase):
    def setUp(self):
        self.client = Client()

    def post(self, path, body=None, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(f"/api/ops/{path}/", data=json.dumps(body or {}),
                                content_type="application/json", **headers)


class LoginTests(PortalTestCase):
    def test_staff_login_returns_role_and_caps(self):
        make_staff("amara", superuser=True)
        res = self.post("login", {"identifier": "amara", "password": "op-pass-123"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["role"], "super_admin")
        self.assertTrue(data["caps"]["money"])
        self.assertTrue(data["token"])
        self.assertTrue(AuditLog.objects.filter(action="ops.login").exists())

    def test_non_staff_cannot_login_even_with_valid_password(self):
        u = User.objects.create(username="customer", phone="08011112222")
        u.set_password("op-pass-123")
        u.save()
        res = self.post("login", {"identifier": "customer", "password": "op-pass-123"})
        self.assertEqual(res.status_code, 403)
        self.assertTrue(AuditLog.objects.filter(action="ops.login_denied").exists())

    def test_wrong_password_is_401_and_audited(self):
        make_staff("amara")
        res = self.post("login", {"identifier": "amara", "password": "wrong"})
        self.assertEqual(res.status_code, 401)
        self.assertTrue(AuditLog.objects.filter(action="ops.login_failed").exists())

    def test_customer_token_cannot_reach_ops(self):
        u = User.objects.create(username="cust2", phone="08011113333")
        token = AccessToken.issue(u).key
        res = self.post("summary", token=token)
        self.assertEqual(res.status_code, 403)


class RbacTests(PortalTestCase):
    def setUp(self):
        super().setUp()
        self.read_only = AccessToken.issue(make_staff("ada")).key
        self.support = AccessToken.issue(make_staff("funmi", role="support")).key
        self.finance = AccessToken.issue(make_staff("dapo", role="finance")).key

    def test_read_only_can_read_but_not_mutate(self):
        self.assertEqual(self.post("summary", token=self.read_only).status_code, 200)
        self.assertEqual(self.post("audit", token=self.read_only).status_code, 200)
        res = self.post("fx-margin", {"bps": 60}, token=self.read_only)
        self.assertEqual(res.status_code, 403)

    def test_support_has_wa_but_not_money(self):
        self.assertEqual(self.post("conv-ai", {"msisdn": "234800", "enabled": False},
                                   token=self.support).status_code, 200)
        self.assertEqual(self.post("fx-margin", {"bps": 60}, token=self.support).status_code, 403)

    def test_finance_has_money_but_not_ai(self):
        self.assertEqual(self.post("fx-margin", {"bps": 60}, token=self.finance).status_code, 200)
        self.assertEqual(self.post("ai-global", {"enabled": False}, token=self.finance).status_code, 403)


class MutationTests(PortalTestCase):
    def setUp(self):
        super().setUp()
        self.admin = AccessToken.issue(make_staff("amara", superuser=True)).key
        self.user = User.objects.create(username="08010000009", phone="08010000009",
                                        first_name="Kemi", tier=1)
        get_or_create_wallet(self.user)

    def test_freeze_revokes_sessions_and_audits(self):
        token = AccessToken.issue(self.user).key
        res = self.post("user-action", {"user_id": self.user.id, "action": "freeze"}, token=self.admin)
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertIsNone(AccessToken.resolve(token))
        self.assertTrue(AuditLog.objects.filter(action="user.freeze").exists())

    def test_kyc_approve_bumps_tier_and_caps_at_3(self):
        res = self.post("kyc-review", {"user_id": self.user.id, "approve": True}, token=self.admin)
        self.assertEqual(res.json()["tier"], 2)
        self.user.tier = 3
        self.user.save()
        res = self.post("kyc-review", {"user_id": self.user.id, "approve": True}, token=self.admin)
        self.assertEqual(res.json()["tier"], 3)
        self.assertEqual(AuditLog.objects.filter(action="kyc.approve").count(), 2)

    def test_fx_margin_validates_and_audits(self):
        self.assertEqual(self.post("fx-margin", {"bps": 2000}, token=self.admin).status_code, 400)
        self.assertEqual(self.post("fx-margin", {"bps": 75}, token=self.admin).status_code, 200)
        self.assertEqual(SystemSetting.get("fx_margin_bps"), "75")
        row = AuditLog.objects.get(action="fx.margin_update")
        self.assertEqual(row.after, {"bps": 75})

    def test_corridor_pause_blocks_quotes(self):
        credit(self.user, Decimal("100000"), "Seed")
        self.post("fx-corridor", {"currency": "USD", "enabled": False}, token=self.admin)
        with self.assertRaises(FxError):
            create_fx_quote(self.user, "NGN", "USD", Decimal("1000"))
        self.post("fx-corridor", {"currency": "USD", "enabled": True}, token=self.admin)
        quote = create_fx_quote(self.user, "NGN", "USD", Decimal("1000"))
        self.assertGreater(quote.receive_amount, 0)
        self.assertEqual(self.post("fx-corridor", {"currency": "CNY", "enabled": True},
                                   token=self.admin).status_code, 400)

    def test_ai_global_toggle(self):
        self.post("ai-global", {"enabled": False}, token=self.admin)
        self.assertEqual(SystemSetting.get("ai_enabled_global"), "false")
        self.assertTrue(AuditLog.objects.filter(action="ai.global_toggle").exists())

    def test_summary_users_transactions_shapes(self):
        for path in ("summary", "users", "transactions", "fx", "products",
                     "inbox", "broadcasts", "ai", "recon", "audit", "settings", "kyc-queue"):
            res = self.post(path, token=self.admin)
            self.assertEqual(res.status_code, 200, f"{path}: {res.content[:120]}")


class WebPagesTests(TestCase):
    def test_landing_prototype_portal_render(self):
        c = Client()
        for path, marker in (("/", b"Zitch"), ("/prototype/", b"root"), ("/portal/", b"root")):
            res = c.get(path)
            self.assertEqual(res.status_code, 200, path)
            self.assertIn(marker, res.content)

    def test_health_moved_to_healthz(self):
        res = Client().get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["status"])
