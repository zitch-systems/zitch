"""Tests for the /api/admin/ staff API: auth, RBAC, bootstrap shape, and every
write action (each must mutate real state AND append to the AuditLog)."""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AccessToken, User
from cards.models import VirtualCard
from loans.models import Loan
from wallet.models import Transaction, Wallet
from whatsapp.models import (AuditLog, Broadcast, ConversationState, SystemSetting,
                             WhatsAppLink)


def make_staff(username, role=None, superuser=False):
    u = User.objects.create(username=username, email=f"{username}@zitch.test",
                            phone=f"081{abs(hash(username)) % 10 ** 8:08d}",
                            is_staff=True, is_superuser=superuser)
    if role:
        group, _ = Group.objects.get_or_create(name=role)
        u.groups.add(group)
    return u


def make_customer(username="ada", phone="08011112222", balance="0"):
    u = User.objects.create(username=username, email=f"{username}@x.test", phone=phone)
    Wallet.objects.create(user=u, balance=Decimal(balance))
    return u


class AdminApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = make_staff("amara", superuser=True)
        self.admin_token = AccessToken.issue(self.admin).key
        self.finance = make_staff("dapo", role="finance")
        self.finance_token = AccessToken.issue(self.finance).key
        self.support = make_staff("funmi", role="support")
        self.support_token = AccessToken.issue(self.support).key
        self.readonly = make_staff("ada_ro")  # staff, no group -> read_only
        self.readonly_token = AccessToken.issue(self.readonly).key
        self.customer = make_customer()

    def post(self, path, token, body=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        res = self.client.post(f"/api/admin/{path}", data=json.dumps(body or {}),
                               content_type="application/json", **headers)
        return res, (res.json() if res["Content-Type"].startswith("application/json") else {})

    def get(self, path, token):
        res = self.client.get(f"/api/admin/{path}", HTTP_AUTHORIZATION=f"Bearer {token}")
        return res, res.json()

    # ---- auth ----------------------------------------------------------- #
    def test_login_is_csrf_exempt(self):
        """The portal is a cookie-less bearer-token API: login must work without
        a CSRF cookie. (The default test client masks this — enforce checks.)"""
        self.admin.set_password("pw12345"); self.admin.save()
        strict = Client(enforce_csrf_checks=True)
        res = strict.post("/api/admin/login", data=json.dumps(
            {"username": "amara", "password": "pw12345"}), content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["token"])

    def test_login_staff_only(self):
        self.admin.set_password("pw12345"); self.admin.save()
        res = self.client.post("/api/admin/login", data=json.dumps(
            {"username": "amara", "password": "pw12345"}), content_type="application/json")
        body = res.json()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["role"], "super_admin")
        self.assertTrue(body["token"])

        self.customer.set_password("pw12345"); self.customer.save()
        res = self.client.post("/api/admin/login", data=json.dumps(
            {"username": "ada", "password": "pw12345"}), content_type="application/json")
        self.assertEqual(res.status_code, 403)

    def test_me_and_bootstrap_require_token(self):
        res = self.client.get("/api/admin/me")
        self.assertEqual(res.status_code, 401)
        res, body = self.get("me", self.support_token)
        self.assertEqual(body["role"], "support")
        self.assertIn("wa", body["can"])
        self.assertNotIn("money", body["can"])

    def test_bootstrap_shape(self):
        VirtualCard.objects.create(user=self.customer, last4="4821", expiry="12/27")
        res, body = self.get("bootstrap", self.readonly_token)  # read_only can read
        self.assertEqual(res.status_code, 200)
        for key in ("users", "txns", "convos", "broadcasts", "audit", "rates", "float",
                    "providers", "volume_14d", "loans", "savings", "cards", "kycq",
                    "team", "perms", "settings", "kpis"):
            self.assertIn(key, body)
        self.assertIn("wa_optin", body["kpis"])
        self.assertIn("matured_due", body["kpis"])
        card = body["cards"][0]
        self.assertEqual(card["cid"], VirtualCard.objects.first().id)
        self.assertEqual(card["cur"], "NGN")
        rate = body["rates"][0]
        for key in ("provider", "customer", "settle", "margin"):
            self.assertIn(key, rate)
        cny = [r for r in body["rates"] if r["pair"] == "NGN/CNY"][0]
        self.assertFalse(cny["settle"])

    # ---- RBAC ------------------------------------------------------------ #
    def test_rbac_matrix_enforced(self):
        # support can't touch money/users/settings
        for path, body in (("txn/flag", {"ref": "x"}), ("users/status", {"uid": 1}),
                           ("settings/update", {"key": "ai_enabled_global", "value": "false"}),
                           ("fx/margin", {"bps": 50}), ("ops/recon", {})):
            res, _ = self.post(path, self.support_token, body)
            self.assertEqual(res.status_code, 403, path)
        # finance can't touch wa/broadcast/settings
        for path, body in (("wa/handover", {"msisdn": "234", "mode": "human"}),
                           ("wa/reply", {"msisdn": "234", "text": "hi"}),
                           ("wa/broadcast", {"template_name": "t"}),
                           ("settings/update", {"key": "ai_enabled_global", "value": "false"})):
            res, _ = self.post(path, self.finance_token, body)
            self.assertEqual(res.status_code, 403, path)
        # read_only can't write at all
        res, _ = self.post("kyc/review", self.readonly_token, {"uid": self.customer.id, "type": "bvn"})
        self.assertEqual(res.status_code, 403)

    # ---- write actions ---------------------------------------------------- #
    def test_user_status_freeze_unfreeze(self):
        res, body = self.post("users/status", self.finance_token,
                              {"uid": self.customer.id, "status": "frozen"})
        self.assertEqual(res.status_code, 200)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)
        self.assertTrue(AuditLog.objects.filter(action="user.freeze").exists())
        res, _ = self.post("users/status", self.finance_token,
                           {"uid": self.customer.id, "status": "active"})
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_active)

    def test_user_pin_unlock(self):
        self.customer.pin_failed_attempts = 3
        self.customer.pin_locked_until = timezone.now() + timedelta(minutes=20)
        self.customer.save()
        res, body = self.post("users/pin_unlock", self.finance_token, {"uid": self.customer.id})
        self.assertEqual(res.status_code, 200)
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.pin_locked_until)
        self.assertEqual(self.customer.pin_failed_attempts, 0)
        self.assertTrue(AuditLog.objects.filter(action="user.pin_unlock").exists())

    def test_kyc_review_approve(self):
        res, body = self.post("kyc/review", self.finance_token,
                              {"uid": self.customer.id, "decision": "approve", "type": "bvn"})
        self.assertEqual(res.status_code, 200)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.bvn_verified)
        self.assertEqual(body["tier"], self.customer.tier)
        self.assertTrue(AuditLog.objects.filter(action="kyc.approve").exists())

    def test_txn_flag_and_unflag(self):
        t = Transaction.objects.create(user=self.customer, amount=Decimal("100"),
                                       direction=Transaction.OUT, service="Airtime",
                                       transaction_status=Transaction.SUCCESS, reference="ZTC-T1")
        res, _ = self.post("txn/flag", self.finance_token, {"ref": "ZTC-T1", "flagged": True})
        self.assertEqual(res.status_code, 200)
        t.refresh_from_db()
        self.assertTrue(t.meta.get("flagged"))
        res, _ = self.post("txn/flag", self.finance_token, {"ref": "ZTC-T1", "flagged": False})
        t.refresh_from_db()
        self.assertFalse(t.meta.get("flagged"))

    def test_txn_requery_only_provider_pending(self):
        t = Transaction.objects.create(user=self.customer, amount=Decimal("100"),
                                       direction=Transaction.OUT, service="Airtime",
                                       transaction_status=Transaction.SUCCESS, reference="ZTC-T2")
        res, _ = self.post("txn/requery", self.finance_token, {"ref": "ZTC-T2"})
        self.assertEqual(res.status_code, 409)  # settled rows can't be requeried
        res, _ = self.post("txn/requery", self.finance_token, {"ref": "nope"})
        self.assertEqual(res.status_code, 404)

    def test_fx_margin_and_corridor(self):
        res, body = self.post("fx/margin", self.finance_token, {"bps": 85})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(SystemSetting.get("fx_margin_bps", ""), "85")
        res, _ = self.post("fx/margin", self.finance_token, {"bps": 5000})
        self.assertEqual(res.status_code, 400)

        res, body = self.post("fx/corridor", self.finance_token,
                              {"currency": "USD", "enabled": False})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(SystemSetting.get("fx_corridor_usd_enabled", ""), "false")
        # CNY can never be enabled from the portal
        res, _ = self.post("fx/corridor", self.finance_token, {"currency": "CNY", "enabled": True})
        self.assertEqual(res.status_code, 400)
        # and the bootstrap reflects the paused corridor
        _, boot = self.get("bootstrap", self.finance_token)
        usd = [r for r in boot["rates"] if r["pair"] == "NGN/USD"][0]
        self.assertFalse(usd["settle"])

    def test_card_freeze_accepts_prefixed_id(self):
        card = VirtualCard.objects.create(user=self.customer, last4="9034", expiry="11/27")
        res, _ = self.post("cards/freeze", self.finance_token,
                           {"card_id": f"cd_{card.id}", "status": "frozen"})
        self.assertEqual(res.status_code, 200)
        card.refresh_from_db()
        self.assertEqual(card.status, VirtualCard.FROZEN)

    def test_loan_remind_requires_wa_link(self):
        loan = Loan.objects.create(user=self.customer, principal=Decimal("50000"),
                                   interest=Decimal("2250"), tenure_days=30,
                                   reference="LN-1", due_date=timezone.now() + timedelta(days=30))
        res, _ = self.post("loans/remind", self.finance_token, {"ref": "LN-1"})
        self.assertEqual(res.status_code, 409)  # no linked WhatsApp
        WhatsAppLink.objects.create(user=self.customer, wa_msisdn="2348011112222",
                                    status=WhatsAppLink.ACTIVE)
        res, body = self.post("loans/remind", self.finance_token, {"ref": "LN-1"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="loan.reminder").exists())

    def test_run_maturities_and_recon(self):
        res, body = self.post("ops/maturities", self.finance_token)
        self.assertEqual(res.status_code, 200)
        self.assertIn("paid_out", body)
        res, body = self.post("ops/recon", self.finance_token)
        self.assertEqual(res.status_code, 200)
        self.assertIn("settled", body)
        self.assertTrue(AuditLog.objects.filter(action="recon.vtu_run").exists())

    def test_wa_conversation_actions(self):
        msisdn = "2348011112222"
        res, _ = self.post("wa/handover", self.support_token, {"msisdn": msisdn, "mode": "human"})
        self.assertEqual(res.status_code, 200)
        cs = ConversationState.objects.get(msisdn=msisdn)
        self.assertEqual(cs.status, ConversationState.HUMAN)
        self.assertFalse(cs.ai_enabled)

        res, _ = self.post("wa/conv_ai", self.support_token, {"msisdn": msisdn, "enabled": True})
        cs.refresh_from_db()
        self.assertTrue(cs.ai_enabled)

        res, _ = self.post("wa/reply", self.support_token, {"msisdn": msisdn, "text": "Hello!"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="conversation.agent_reply").exists())

        res, _ = self.post("wa/handover", self.support_token, {"msisdn": msisdn, "mode": "bot"})
        cs.refresh_from_db()
        self.assertEqual(cs.status, ConversationState.BOT)

    def test_wa_broadcast_marketing_respects_optin(self):
        WhatsAppLink.objects.create(user=self.customer, wa_msisdn="2348011112222",
                                    status=WhatsAppLink.ACTIVE, marketing_opt_in=False)
        opted = make_customer("bisi", phone="08033334444")
        WhatsAppLink.objects.create(user=opted, wa_msisdn="2348033334444",
                                    status=WhatsAppLink.ACTIVE, marketing_opt_in=True)
        res, body = self.post("wa/broadcast", self.support_token,
                              {"template_name": "promo", "category": "marketing"})
        self.assertEqual(res.status_code, 200)
        row = body["broadcast"]
        self.assertEqual(row["queued"], 1)  # only the opted-in user
        self.assertEqual(row["template"], "promo")
        self.assertEqual(Broadcast.objects.count(), 1)

    def test_setting_update_super_admin_only(self):
        res, body = self.post("settings/update", self.admin_token,
                              {"key": "ai_enabled_global", "value": "false"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(SystemSetting.get("ai_enabled_global", ""), "false")
        res, _ = self.post("settings/update", self.admin_token,
                           {"key": "not_a_setting", "value": "x"})
        self.assertEqual(res.status_code, 400)


class AdminApiFeatureTests(TestCase):
    """The deeper-read + manual-credit endpoints: customer 360, server-side
    search, broadcast delivery detail, webhook/recon history, wallet credit."""

    def setUp(self):
        self.client = Client()
        self.finance = make_staff("dapo2", role="finance")
        self.finance_token = AccessToken.issue(self.finance).key
        self.support = make_staff("funmi2", role="support")
        self.support_token = AccessToken.issue(self.support).key
        self.readonly = make_staff("ro2")
        self.readonly_token = AccessToken.issue(self.readonly).key
        self.customer = make_customer("zara", phone="08055556666", balance="1000")

    def post(self, path, token, body=None):
        res = self.client.post(f"/api/admin/{path}", data=json.dumps(body or {}),
                               content_type="application/json",
                               HTTP_AUTHORIZATION=f"Bearer {token}")
        return res, res.json()

    def get(self, path, token):
        res = self.client.get(f"/api/admin/{path}", HTTP_AUTHORIZATION=f"Bearer {token}")
        return res, res.json()

    def test_user_detail_360(self):
        Transaction.objects.create(user=self.customer, amount=Decimal("250"),
                                   direction=Transaction.OUT, service="Airtime — MTN",
                                   transaction_status=Transaction.SUCCESS, reference="ZTC-D1")
        Loan.objects.create(user=self.customer, principal=Decimal("20000"), interest=Decimal("900"),
                            tenure_days=30, reference="LN-D1",
                            due_date=timezone.now() + timedelta(days=30))
        VirtualCard.objects.create(user=self.customer, last4="1177", expiry="01/28")
        res, body = self.post("users/detail", self.readonly_token, {"uid": self.customer.id})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["user"]["uid"], self.customer.id)
        self.assertEqual(len(body["txns"]), 1)
        self.assertEqual(body["loans"][0]["ref"], "LN-D1")
        self.assertEqual(len(body["cards"]), 1)
        self.assertIn("pin_locked", body)
        res, _ = self.post("users/detail", self.readonly_token, {"uid": 999999})
        self.assertEqual(res.status_code, 404)

    def test_user_search(self):
        res, body = self.post("users/search", self.readonly_token, {"q": "zara"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(body["rows"]), 1)
        self.assertEqual(body["rows"][0]["uid"], self.customer.id)
        res, body = self.post("users/search", self.readonly_token, {"q": "no-such-user-xyz"})
        self.assertEqual(body["rows"], [])

    def test_txn_search_filters(self):
        Transaction.objects.create(user=self.customer, amount=Decimal("250"),
                                   direction=Transaction.OUT, service="Airtime — MTN",
                                   transaction_status=Transaction.SUCCESS, reference="ZTC-S1")
        Transaction.objects.create(user=self.customer, amount=Decimal("900"),
                                   direction=Transaction.OUT, service="Transfer — GTB",
                                   transaction_status=Transaction.FAILED, reference="ZTC-S2")
        res, body = self.post("txn/search", self.readonly_token, {"type": "airtime"})
        self.assertEqual([r["id"] for r in body["rows"]], ["ZTC-S1"])
        res, body = self.post("txn/search", self.readonly_token, {"status": "failed"})
        self.assertEqual([r["id"] for r in body["rows"]], ["ZTC-S2"])
        res, body = self.post("txn/search", self.readonly_token, {"q": "zara"})
        self.assertEqual(len(body["rows"]), 2)

    def test_audit_search(self):
        from .auth import audit as audit_helper

        class Req:  # minimal shim for the audit() helper signature
            staff = self.finance
        audit_helper(Req, "test.alpha_action", target="u_777")
        audit_helper(Req, "test.beta_action", target="u_888")
        res, body = self.post("audit/search", self.readonly_token, {"q": "alpha"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(all("alpha" in r["action"] for r in body["rows"]))
        self.assertGreaterEqual(len(body["rows"]), 1)

    def test_broadcast_detail(self):
        WhatsAppLink.objects.create(user=self.customer, wa_msisdn="2348055556666",
                                    status=WhatsAppLink.ACTIVE, marketing_opt_in=True)
        _, sent = self.post("wa/broadcast", self.support_token,
                            {"template_name": "detail_test", "category": "utility"})
        bid = sent["broadcast"]["id"]  # bc_<pk> form on purpose
        res, body = self.post("wa/broadcast_detail", self.readonly_token, {"id": bid})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["broadcast"]["template"], "detail_test")
        self.assertEqual(len(body["recipients"]), 1)
        self.assertEqual(body["recipients"][0]["msisdn"], "2348055556666")
        res, _ = self.post("wa/broadcast_detail", self.readonly_token, {"id": "bc_999999"})
        self.assertEqual(res.status_code, 404)

    def test_bootstrap_webhooks_and_recons_live(self):
        from whatsapp.models import AuditLog
        from whatsapp.ops import record_audit

        record_audit("webhook.kora", actor_type="system", target="KORA|X1",
                     after={"event": "charge.success"})
        self.post("ops/recon", self.finance_token, {})
        res, body = self.get("bootstrap", self.readonly_token)
        self.assertTrue(any(w["ref"] == "KORA|X1" and w["src"] == "Kora" for w in body["webhooks"]))
        self.assertTrue(any(r["run"] == "zitch-reconcile-vtu" for r in body["recons"]))

    def test_wallet_credit_happy_path_and_audit(self):
        res, body = self.post("wallet/credit", self.finance_token,
                              {"uid": self.customer.id, "amount": "500",
                               "reason": "Goodwill: failed airtime ZTC-S1",
                               "idempotency_key": "mc-1"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["balance"], 1500.0)
        w = Wallet.objects.get(user=self.customer)
        self.assertEqual(w.balance, Decimal("1500"))
        t = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(t.direction, Transaction.IN)
        self.assertEqual(t.meta["reason"], "Goodwill: failed airtime ZTC-S1")
        self.assertTrue(AuditLog.objects.filter(action="wallet.manual_credit").exists())

    def test_wallet_credit_idempotent_replay(self):
        body1 = self.post("wallet/credit", self.finance_token,
                          {"uid": self.customer.id, "amount": "500",
                           "reason": "Goodwill credit", "idempotency_key": "mc-dup"})[1]
        res, body2 = self.post("wallet/credit", self.finance_token,
                               {"uid": self.customer.id, "amount": "500",
                                "reason": "Goodwill credit", "idempotency_key": "mc-dup"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body2.get("duplicate"))
        self.assertEqual(body2["reference"], body1["reference"])
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, Decimal("1500"))  # once

    def test_wallet_credit_without_key_dedups_double_submit(self):
        # No client idempotency_key -> a server-side fallback key still dedups a
        # double-submit (the unique constraint skips blank keys, so without the
        # fallback an empty key would credit twice).
        body = {"uid": self.customer.id, "amount": "500", "reason": "Goodwill no-key"}
        self.post("wallet/credit", self.finance_token, body)
        self.post("wallet/credit", self.finance_token, body)
        self.assertEqual(Wallet.objects.get(user=self.customer).balance, Decimal("1500"))  # 1000 + 500 once

    def test_wallet_credit_rejects_staff_target(self):
        # Operators can't credit a staff account (incl. their own): _get_user is
        # scoped to customers, so a staff uid is "not found".
        res, _ = self.post("wallet/credit", self.finance_token,
                           {"uid": self.finance.id, "amount": "500", "reason": "self credit attempt"})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(Wallet.objects.filter(user=self.finance).count(), 0)  # nothing credited

    def test_wallet_credit_validation_and_rbac(self):
        res, _ = self.post("wallet/credit", self.finance_token,
                           {"uid": self.customer.id, "amount": "-5", "reason": "valid reason"})
        self.assertEqual(res.status_code, 400)
        res, _ = self.post("wallet/credit", self.finance_token,
                           {"uid": self.customer.id, "amount": "100", "reason": "x"})
        self.assertEqual(res.status_code, 400)  # reason too short
        res, _ = self.post("wallet/credit", self.support_token,
                           {"uid": self.customer.id, "amount": "100", "reason": "valid reason"})
        self.assertEqual(res.status_code, 403)  # support lacks money cap
        res, _ = self.post("wallet/credit", self.readonly_token,
                           {"uid": self.customer.id, "amount": "100", "reason": "valid reason"})
        self.assertEqual(res.status_code, 403)


class SeedOpsCommandTests(TestCase):
    """seed_ops provisions login-capable operators and refuses default-credential
    demo seeding in production."""

    def test_named_operator_can_log_in_with_role(self):
        from django.core.management import call_command
        call_command("seed_ops", username="zoe", role="finance", password="S3cret#pass")
        u = User.objects.get(username="zoe")
        self.assertTrue(u.is_staff and u.check_password("S3cret#pass"))
        self.assertTrue(u.groups.filter(name="finance").exists())
        # The operator can actually authenticate at the ops login endpoint.
        res = Client().post("/api/ops/login/",
                            data=json.dumps({"identifier": "zoe", "password": "S3cret#pass"}),
                            content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("role"), "finance")

    def test_demo_seed_blocked_in_production_without_force(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django.test import override_settings
        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command("seed_ops")
        self.assertFalse(User.objects.filter(username="amara").exists())
