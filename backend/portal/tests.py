"""Operator portal tests: staff login, RBAC caps, audited mutations, web pages.

The portal is the money-control surface, so the tests pin the gates: non-staff
can never log in, read_only can read but not mutate, every mutation lands in
the audit log, and the FX corridor pause actually stops quotes.
"""
import json
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from unittest.mock import patch

from django.test import Client, SimpleTestCase, TestCase, override_settings

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
        # An app-scoped (mobile) token is refused at the scope gate before any
        # role check — 401, not 403 — so it can never reach the operator surface.
        u = User.objects.create(username="cust2", phone="08011113333")
        token = AccessToken.issue(u).key  # default app scope
        res = self.post("summary", token=token)
        self.assertEqual(res.status_code, 401)


class RbacTests(PortalTestCase):
    def setUp(self):
        super().setUp()
        self.read_only = AccessToken.issue(make_staff("ada"), scope=AccessToken.ADMIN).key
        self.support = AccessToken.issue(make_staff("funmi", role="support"), scope=AccessToken.ADMIN).key
        self.finance = AccessToken.issue(make_staff("dapo", role="finance"), scope=AccessToken.ADMIN).key

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
        self.admin = AccessToken.issue(make_staff("amara", superuser=True), scope=AccessToken.ADMIN).key
        self.user = User.objects.create(username="08010000009", phone="08010000009",
                                        first_name="Kemi", tier=1)
        get_or_create_wallet(self.user)

    def test_kyc_queue_lists_pending_item_without_crashing(self):
        # Regression: the queue row-builder read non-existent u.bvn/u.nin and 500'd
        # on exactly the rows the queue selects (a submitted-but-unverified ID).
        self.user.bvn_hash = "deadbeefhash"
        self.user.bvn_verified = False
        self.user.save(update_fields=["bvn_hash", "bvn_verified"])
        res = self.post("kyc-queue", token=self.admin)
        self.assertEqual(res.status_code, 200, res.content[:200])
        rows = res.json()["rows"]
        self.assertTrue(any(r["id"] == self.user.id and r["type"] == "bvn" for r in rows))

    def test_freeze_revokes_sessions_and_audits(self):
        token = AccessToken.issue(self.user).key
        res = self.post("user-action", {"user_id": self.user.id, "action": "freeze"}, token=self.admin)
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertIsNone(AccessToken.resolve(token))
        self.assertTrue(AuditLog.objects.filter(action="user.freeze").exists())

    def test_kyc_approve_marks_flags_and_derives_tier(self):
        # A user who submitted BVN + NIN (unverified): approval marks them verified
        # and DERIVES the tier from the flags (BVN+NIN => Tier 1), never a blind
        # +1. This is durable — the next recompute_tier keeps it.
        self.user.bvn_hash, self.user.nin_hash, self.user.tier = "bvnhash", "ninhash", 0
        self.user.save()
        res = self.post("kyc-review", {"user_id": self.user.id, "approve": True}, token=self.admin)
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified and self.user.nin_verified)
        self.assertEqual(self.user.tier, 1)
        self.assertEqual(res.json()["tier"], 1)
        self.user.recompute_tier()          # the grant survives a recompute
        self.assertEqual(self.user.tier, 1)
        self.assertTrue(AuditLog.objects.filter(action="kyc.approve").exists())

    def test_kyc_approve_without_submitted_identity_grants_no_tier(self):
        # No identity on file: approval must NOT grant a tier the user hasn't
        # earned (the old code blindly bumped tier — an AML/KYC control gap).
        self.user.bvn_hash, self.user.nin_hash, self.user.tier = "", "", 0
        self.user.save()
        res = self.post("kyc-review", {"user_id": self.user.id, "approve": True}, token=self.admin)
        self.assertEqual(res.json()["tier"], 0)

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

    def test_logout_revokes_admin_token(self):
        # Sign out must kill the token SERVER-side — clearing localStorage alone
        # left it valid until TTL.
        self.assertEqual(self.post("summary", token=self.admin).status_code, 200)
        self.assertEqual(self.post("logout", token=self.admin).status_code, 200)
        self.assertEqual(self.post("summary", token=self.admin).status_code, 401)
        self.assertTrue(AuditLog.objects.filter(action="ops.logout").exists())

    def test_kyc_reject_clears_submission_and_drains_queue(self):
        self.user.bvn_hash, self.user.bvn_last4, self.user.tier = "deadbeefhash", "1234", 0
        self.user.save()
        rows = self.post("kyc-queue", token=self.admin).json()["rows"]
        self.assertTrue(any(r["id"] == self.user.id for r in rows))
        res = self.post("kyc-review", {"user_id": self.user.id, "approve": False}, token=self.admin)
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        # Submission cleared (user resubmits), verified state untouched.
        self.assertEqual(self.user.bvn_hash, "")
        self.assertEqual(self.user.bvn_last4, "")
        self.assertFalse(self.user.bvn_verified)
        rows = self.post("kyc-queue", token=self.admin).json()["rows"]
        self.assertFalse(any(r["id"] == self.user.id for r in rows))
        self.assertTrue(AuditLog.objects.filter(action="kyc.reject").exists())

    def test_kyc_queue_skips_fresh_signup_lists_stale_tier(self):
        # A tier-0 user with NOTHING submitted is not reviewable — approve
        # provably no-ops on them — so they must not clog the queue…
        fresh = User.objects.create(username="fresh", phone="08010000010", tier=0)
        rows = self.post("kyc-queue", token=self.admin).json()["rows"]
        self.assertFalse(any(r["id"] == fresh.id for r in rows))
        # …but a tier-0 user whose checks already support Tier 1 IS actionable.
        fresh.bvn_verified = fresh.nin_verified = True
        fresh.save(update_fields=["bvn_verified", "nin_verified"])
        rows = self.post("kyc-queue", token=self.admin).json()["rows"]
        self.assertTrue(any(r["id"] == fresh.id for r in rows))

    def test_users_total_reflects_search_filter(self):
        User.objects.create(username="zuri", phone="08010000011", first_name="Zuri")
        data = self.post("users", {"q": "Zuri"}, token=self.admin).json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(len(data["rows"]), 1)

    def test_card_action_freezes_at_issuer_and_fails_closed(self):
        from unittest.mock import patch

        from cards.models import VirtualCard

        card = VirtualCard.objects.create(user=self.user, card_token="ct_1", last4="4321")
        # Issuer rejects -> 502 and the DB row must NOT flip (a card shown
        # "frozen" in the portal while live at the issuer is a fraud gap).
        with patch("utility.providers.card_set_status",
                   return_value={"success": False, "message": "issuer down"}) as m:
            res = self.post("card-action", {"card_id": card.id}, token=self.admin)
        self.assertEqual(res.status_code, 502)
        card.refresh_from_db()
        self.assertEqual(card.status, VirtualCard.ACTIVE)
        # Issuer accepts -> frozen, issuer called with active=False.
        with patch("utility.providers.card_set_status", return_value={"success": True}) as m:
            res = self.post("card-action", {"card_id": card.id}, token=self.admin)
        self.assertEqual(res.status_code, 200)
        m.assert_called_once_with("ct_1", active=False)
        card.refresh_from_db()
        self.assertEqual(card.status, VirtualCard.FROZEN)

    def test_thread_returns_latest_messages(self):
        from whatsapp.models import WaMessageLog

        for i in range(205):
            WaMessageLog.objects.create(msisdn="2348010000012", direction=WaMessageLog.IN,
                                        wa_message_id=f"m{i}", text=f"msg {i}")
        msgs = self.post("thread", {"msisdn": "2348010000012"}, token=self.admin).json()["msgs"]
        self.assertEqual(len(msgs), 200)
        # The newest message is present (the old ascending slice pinned the
        # thread to its oldest 200 rows forever) and order is oldest-first.
        self.assertEqual(msgs[-1]["text"], "msg 204")
        self.assertEqual(msgs[0]["text"], "msg 5")


class WebPagesTests(TestCase):
    def test_landing_prototype_portal_render(self):
        c = Client()
        for path, marker in (("/", b"Zitch"), ("/prototype/", b"root"), ("/portal/", b"root")):
            res = c.get(path)
            self.assertEqual(res.status_code, 200, path)
            self.assertIn(marker, res.content)

    def test_public_pages_never_link_to_the_operator_portal(self):
        """The money-control surface is staff-only and must not be advertised to
        the public. A footer link to it used to sit on both landing pages; this
        pins it out of every page a visitor can reach without credentials."""
        c = Client()
        for path in ("/", "/console/", "/prototype/"):
            res = c.get(path)
            if res.status_code != 200:
                continue
            body = res.content.lower()
            self.assertNotIn(b"admin portal", body, path)
            self.assertNotIn(b'href="/portal/', body, path)
            self.assertNotIn(b'href="/console/portal/', body, path)
            self.assertNotIn(b'href="/admin/', body, path)

    # The demo class names also appear in the stylesheet, which ships in both
    # modes — so these assert on the rendered elements, never the bare string.
    LIVE_BODY, DEMO_BODY = b'<body class="">', b'<body class="is-demo">'
    LIVE_BAR, DEMO_BAR = b'class="mode-bar"', b'class="mode-bar mode-bar--demo"'

    def test_live_portal_loads_the_live_bundle_and_no_fixtures(self):
        """The default must never be the mock. /portal/ with no query string is
        what an operator reaches from a bookmark, so it has to be the live one."""
        body = Client().get("/portal/").content
        self.assertIn(b"/static/portal/admin/api.js", body)
        self.assertIn(b"/static/portal/admin/portal.jsx", body)
        self.assertNotIn(b"/static/console/portal/", body)
        self.assertIn(self.LIVE_BODY, body)
        self.assertIn(self.LIVE_BAR, body)

    def test_demo_mode_loads_the_fixture_bundle_and_not_the_live_one(self):
        """Demo mode serves the console bundle and none of the live one.

        This assertion is about which files the PAGE pulls in, and that is all
        it was ever evidence of. It used to be documented as proof that demo
        mode "is incapable of calling the API" — it was not, and it passed
        happily while demo mode read and wrote live production data through a
        client inlined in the console bundle's own data.js. DemoBundleTests
        below is what actually holds that line; this one holds the wiring.
        """
        body = Client().get("/portal/?mode=demo").content
        self.assertIn(b"/static/console/portal/portal.jsx", body)
        self.assertNotIn(b"/static/portal/admin/api.js", body)
        self.assertNotIn(b"/static/portal/admin/portal.jsx", body)

    def test_demo_mode_is_flagged_in_the_markup(self):
        """Two portals that looked identical was the whole problem. Pin the
        marks that make demo unmistakable so a restyle can't quietly drop them."""
        body = Client().get("/portal/?mode=demo").content
        self.assertIn(self.DEMO_BODY, body)           # viewport frame
        self.assertIn(self.DEMO_BAR, body)            # striped bar
        self.assertIn(b"nothing here is real", body)  # plain-language warning

    def test_unknown_mode_falls_back_to_live(self):
        """Fail safe: any value that isn't exactly "demo" — a typo, a stale
        link, ?mode=live — serves the real portal rather than fixtures."""
        for qs in ("?mode=", "?mode=live", "?mode=DEMO", "?mode=demo2", "?other=demo"):
            body = Client().get("/portal/" + qs).content
            self.assertIn(b"/static/portal/admin/api.js", body, qs)
            self.assertIn(self.LIVE_BODY, body, qs)

    def test_no_template_syntax_leaks_into_either_page(self):
        """Django's {# #} comment is SINGLE-LINE only — a multi-line one is not a
        comment at all and renders verbatim across the top of the portal. Caught
        in review by screenshotting the page; the bundle-and-class assertions all
        passed straight through it, so pin the rendered text too."""
        # `}}` is deliberately not checked: minified CSS closes a nested @media
        # block with it, so it is not evidence of anything.
        for qs in ("", "?mode=demo"):
            body = Client().get("/portal/" + qs).content
            for leak in (b"{#", b"#}", b"{%", b"%}", b"{{"):
                self.assertNotIn(leak, body, f"{leak!r} leaked at /portal/{qs}")

    def test_old_console_portal_url_redirects_to_demo_mode(self):
        res = Client().get("/console/portal/")
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res["Location"], "/portal/?mode=demo")

    def test_console_landing_and_app_are_untouched(self):
        """Only the duplicate portal was consolidated; the other two console
        surfaces are still served verbatim."""
        c = Client()
        for path in ("/console/", "/console/app/"):
            self.assertEqual(c.get(path).status_code, 200, path)

    def test_health_moved_to_healthz(self):
        res = Client().get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["status"])


class DemoBundleTests(SimpleTestCase):
    """The demo bundle must be incapable of reaching the network.

    The page-level tests above check which script files each mode loads, and a
    reviewer read that as isolation. It was not. The live client was never a
    separate file to leave out — it was inlined in the demo bundle's own
    data.js, pointed at the staff API rather than the portal API that the
    comments and tests kept naming. So demo mode signed operators in for real,
    rendered real customer rows, and fired real writes (wallet credits, KYC
    decisions, the global AI kill switch) from behind a bar promising that
    nothing there was real.

    Filenames could not see that. These read the bytes.
    """

    DEMO = Path(__file__).resolve().parent.parent / "console" / "static" / "console" / "portal"
    LIVE = Path(__file__).resolve().parent / "static" / "portal" / "admin"

    # Ways to put a byte on the wire. Deliberately not a list of URLs or mounts:
    # the original guarantee was written about one URL, which is exactly how a
    # second one walked past it.
    NETWORK = ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource", "new Image(")

    def sources(self, folder):
        return sorted(p for p in folder.iterdir() if p.suffix in (".js", ".jsx"))

    def test_the_bundle_is_where_this_test_thinks_it_is(self):
        """Without this, a rename makes every assertion below vacuously true."""
        self.assertEqual(
            [p.name for p in self.sources(self.DEMO)],
            ["data.js", "portal.jsx", "ui.jsx", "views-a.jsx", "views-b.jsx", "views-c.jsx"],
        )

    def test_demo_bundle_has_no_network_primitive(self):
        for path in self.sources(self.DEMO):
            src = path.read_text(encoding="utf-8")
            for token in self.NETWORK:
                self.assertNotIn(token, src, f"{path.name} can reach the network via {token!r}")

    def test_demo_bundle_neither_stores_nor_sends_a_credential(self):
        """Signing into the old demo persisted a real staff bearer token. The
        only reference left is the line that deletes the stale one."""
        for path in self.sources(self.DEMO):
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("localStorage.setItem", src, path.name)
            self.assertNotIn("sessionStorage.setItem", src, path.name)
            self.assertNotIn("Authorization", src, path.name)
        data = (self.DEMO / "data.js").read_text(encoding="utf-8")
        self.assertIn("removeItem('zadm_token')", data)

    def test_demo_bundle_offers_no_password_field(self):
        """A live-looking sign-in on a page stamped DEMO teaches operators to
        type real credentials into a mock. There is nowhere to type them now."""
        for path in self.sources(self.DEMO):
            self.assertNotIn("type=\"password\"", path.read_text(encoding="utf-8"), path.name)

    def test_the_live_bundle_still_has_its_client(self):
        """Keeps the assertions above from passing for the wrong reason. What
        they forbid has to exist next door, or they prove nothing about
        isolation — an empty or renamed folder would satisfy them too."""
        self.assertIn("fetch(", (self.LIVE / "api.js").read_text(encoding="utf-8"))


class DiagnosticsPageTests(TestCase):
    """Every *-diagnose endpoint needs an Authorization header — curl, therefore a
    terminal. An operator working from a browser and the hosting dashboard could reach
    only /healthz and was otherwise blind."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(username="ops-diag", password="pw-diag-1",
                                              is_staff=True, is_superuser=True)

    def test_requires_a_staff_session_not_a_bearer_token(self):
        res = self.client.get("/admin/diagnostics/")
        self.assertEqual(res.status_code, 302)                 # to the admin login
        self.assertIn("/admin/login/", res["Location"])

    def test_renders_every_rail_without_a_terminal(self):
        self.client.force_login(self.staff)
        res = self.client.get("/admin/diagnostics/")
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        for expected in ("Go-live preflight", "Wema / ALAT", "SMS (Termii)", "VTU.ng"):
            self.assertIn(expected, body)

    def test_a_failing_probe_does_not_take_the_page_down(self):
        # The page exists precisely for when something is broken.
        self.client.force_login(self.staff)
        with patch("utility.wema.wema_diagnostics", side_effect=RuntimeError("rail down")):
            res = self.client.get("/admin/diagnostics/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("rail down", res.content.decode())

    def test_a_page_load_never_sends_an_sms(self):
        # It costs money and it is a real message to a real handset.
        self.client.force_login(self.staff)
        with patch("utility.providers.sms_probe") as probe:
            self.client.get("/admin/diagnostics/")
        probe.assert_not_called()

    def test_the_button_sends_one_and_says_accepted_is_not_delivered(self):
        self.client.force_login(self.staff)
        with patch("utility.providers.sms_probe",
                   return_value={"sent": {"ok": True}}) as probe:
            res = self.client.post("/admin/diagnostics/", {"sms_to": "0803 000 0000"})
        self.assertEqual(probe.call_args[0][0], "08030000000")   # digits only
        self.assertIn("Accepted is not delivered", res.content.decode())

    def test_no_secret_reaches_the_page(self):
        self.client.force_login(self.staff)
        with override_settings(TERMII={"API_KEY": "tk_supersecret", "SENDER_ID": "Zitch",
                                       "CHANNEL": "dnd", "BASE_URL": "https://v3.api.termii.com"}):
            res = self.client.get("/admin/diagnostics/")
        self.assertNotIn("tk_supersecret", res.content.decode())
