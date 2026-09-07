from django.test import TestCase


class EmailBrandTests(TestCase):
    """Every mail renders inside ONE shell: logo header, team sign-off, contact
    points, socials. Two templates had grown side by side with different
    palettes and different logos — a customer comparing their alert against
    their OTP mail saw two products."""

    def test_the_shell_carries_logo_team_and_contacts(self):
        from common.emails import LOGO_URL, branded_message

        html = branded_message("Confirm your email", "Use this code.", code="123456")
        self.assertIn(LOGO_URL, html)
        self.assertIn("Zitch team", html)
        self.assertIn("zitch.ng", html)
        self.assertIn("support@zitch.ng", html)
        self.assertIn("123456", html)

    def test_socials_render_only_when_configured(self):
        from django.test import override_settings

        from common.emails import branded_message

        self.assertNotIn("Instagram", branded_message("t", "i"))
        with override_settings(SOCIAL_LINKS={"Instagram": "https://instagram.com/real-handle"}):
            html = branded_message("t", "i")
            self.assertIn("Instagram", html)
            self.assertIn("https://instagram.com/real-handle", html)

    def test_the_alert_uses_the_same_shell(self):
        from decimal import Decimal

        from common.emails import LOGO_URL
        from wallet.alerts import _email_alert_html
        from wallet.services import credit, get_or_create_wallet
        from wallet.models import Transaction
        from accounts.models import User

        u = User.objects.create(username="08033334444", phone="08033334444",
                                first_name="Ada", email="a@b.test")
        get_or_create_wallet(u)
        credit(u, Decimal("4300"), "Transfer from YUSUFF")
        txn = Transaction.objects.filter(user=u).first()
        html = _email_alert_html(txn)
        self.assertIn(LOGO_URL, html)
        self.assertIn("Zitch team", html)
        self.assertIn("Credit alert", html)
        self.assertIn("4,300", html)

    def test_every_customer_email_sends_html(self):
        """No call site may regress to bare text — that is what customers were
        actually receiving until tonight's Resend inspection."""
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for f in ("wallet/alerts.py", "whatsapp/router.py", "accounts/views.py",
                  "accounts/admin.py"):
            text = (root / f).read_text()
            for m in re.finditer(r"send_email\((?:[^()]|\([^()]*\))*\)", text):
                if "html=" not in m.group(0):
                    offenders.append(f"{f}: {m.group(0)[:60]}...")
        self.assertEqual(offenders, [])
