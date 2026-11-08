"""One PIN request, one card.

The secure Flow message already IS the confirm card: its body is
`_flow_summary(pa)` and it carries the button. Every money flow then sent a
SECOND chat message with the same summary and "tap the card above" — two stacked
cards saying the same thing, the real one scrolled above, and the duplicate
inviting a tap it could not service.

The chat card is still correct on every other rung. An SMS code and the dev/test
PIN prompt have no card of their own, so that message IS their card; suppressing
it there would leave the customer with a charge to authorise and no way to.
"""
from banking.test_fixtures import bank_channel_case
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from whatsapp import router
from whatsapp.flows import FLOW_PIN_STATE
from whatsapp.models import PendingAction
from whatsapp.test_flows import MSISDN, _make_user


def _transfer_action(user, state):
    """Was an airtime action. Airtime went with the VAS rail, so that fixture
    described a state nothing can reach -- and the tests using it are about
    suppressing the second card, which has never depended on the action type."""
    return PendingAction.objects.create(
        user=user, msisdn=MSISDN, action_type="transfer", state=state,
        payload={"amount": "5000", "account": "0123456789", "bank_code": "058",
                 "bank_name": "GTBank", "name": "JOHN DOE", "pin_attempts": 0},
        expires_at=timezone.now() + timedelta(minutes=5))


@bank_channel_case
class TheFlowCardIsTheOnlyCardTests(TestCase):

    def setUp(self):
        self.user = _make_user()

    def test_nothing_is_sent_to_the_chat_when_the_flow_is_armed(self):
        pa = _transfer_action(self.user, FLOW_PIN_STATE)
        with patch.object(router, "reply") as reply, \
             patch.object(router, "reply_image") as image:
            router._send_confirm(pa, MSISDN, "📱 *Confirm airtime*\n₦5,000.00 MTN → 08166938327",
                                 logo="https://logo")
        reply.assert_not_called()
        image.assert_not_called()

    def test_the_sms_code_rung_still_gets_its_card(self):
        """It has no card of its own — suppressing this would leave the customer
        with a charge to authorise and nothing to authorise it with."""
        pa = _transfer_action(self.user, "pin")
        pa.payload["otp_hash"] = "x"
        pa.save(update_fields=["payload"])
        with patch.object(router, "reply") as reply:
            router._send_confirm(pa, MSISDN, "Confirm airtime\n₦5,000.00 MTN → 08166938327")
        said = reply.call_args[0][1]
        self.assertIn("₦5,000.00", said)
        self.assertIn("6-digit code", said)

    def test_the_logo_card_is_used_when_one_is_given(self):
        pa = _transfer_action(self.user, "pin")
        with patch.object(router, "reply_image") as image, patch.object(router, "reply"):
            router._send_confirm(pa, MSISDN, "body", logo="https://logo")
        self.assertEqual(image.call_args[0][1], "https://logo")

    #: The airtime ladder's end-to-end send (_advance_airtime) went with the VAS
    #: rail, and with it the only test here that drove a guided ladder to its
    #: Flow. The transfer form has its own coverage in test_flows.

    def test_the_flow_card_body_carries_the_whole_summary(self):
        """What justifies suppressing the chat message: the card is not a stub —
        it shows the amount and the recipient the second one did.

        Was asserted on an airtime card (amount, network, number). The same
        property on the transfer card, which is what _flow_summary still builds.
        """
        pa = _transfer_action(self.user, "amount")
        summary = router._flow_summary(pa)
        self.assertIn("₦5,000.00", summary)
        self.assertIn("JOHN DOE", summary)
        self.assertIn("0123456789", summary)

    def test_the_re_auth_card_says_why_it_appeared(self):
        """The one confirm that arrives unprompted. Its reason used to live in
        the chat line beside it — the very message that is no longer sent — so
        the card has to carry it or the customer gets a PIN request with no
        explanation."""
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="unlock", state="pin",
            payload={"pin_attempts": 0}, expires_at=timezone.now() + timedelta(minutes=5))
        self.assertIn("confirm it's you", router._flow_summary(pa))
