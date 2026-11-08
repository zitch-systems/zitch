"""Regression tests for WhatsApp signup identity and channel sign-in."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from whatsapp.flows import (
    FLOW_SIGNUP_STATE,
    handle_flow_request,
    sign_onboarding_token,
)
from whatsapp.models import WaOnboarding, WhatsAppLink
from whatsapp.router import _handle_unlinked


MSISDN = "2348099990001"
LOCAL = "08099990001"


class SignupSessionHardeningTests(TestCase):
    def test_each_valid_flow_exchange_refreshes_the_signup_deadline(self):
        ob = WaOnboarding.objects.create(
            msisdn=MSISDN,
            step=FLOW_SIGNUP_STATE,
            payload={},
            expires_at=timezone.now() + timedelta(seconds=30),
        )
        before = ob.expires_at
        response = handle_flow_request({
            "action": "data_exchange",
            "flow_token": sign_onboarding_token(ob),
            "data": {
                "first_name": "Ngozi",
                "last_name": "Ade",
                "email": "ngozi@example.com",
            },
        })
        self.assertEqual(response["screen"], "SIGNUP_PHONE")
        ob.refresh_from_db()
        self.assertGreater(ob.expires_at, before + timedelta(minutes=10))


class WhatsAppSigninHardeningTests(TestCase):
    def test_relinking_retires_the_previous_active_phone(self):
        user = User.objects.create(
            username=LOCAL,
            phone=LOCAL,
            email="ngozi@example.com",
            first_name="Ngozi",
        )
        old = WhatsAppLink.objects.create(
            user=user,
            wa_msisdn="2348088880001",
            status=WhatsAppLink.ACTIVE,
            linked_at=timezone.now(),
        )
        from whatsapp.auth_proof import credentials
        from whatsapp.linking import issue_link
        pending = issue_link(user, credentials(user))
        # Formatting separators remain accepted when a customer pastes a code.
        formatted = pending.link_code[:32] + "-" + pending.link_code[32:]
        with patch("whatsapp.router.reply"), patch("whatsapp.router.send_menu"):
            _handle_unlinked(MSISDN, "LINK " + formatted)

        pending.refresh_from_db()
        self.assertEqual(pending.status, WhatsAppLink.ACTIVE)
        self.assertEqual(pending.wa_msisdn, MSISDN)
        self.assertEqual(pending.link_code, "")
        self.assertFalse(WhatsAppLink.objects.filter(pk=old.pk).exists())
