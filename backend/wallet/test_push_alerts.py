from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

from accounts.models import PushDevice, User
from wallet.models import Transaction
from wallet.services import get_or_create_wallet

from .alerts import _push_alert


@override_settings(TXN_ALERTS={"EMAIL": False, "SMS": False, "WHATSAPP": False,
                               "PUSH": True})
class NativePushAlertTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="08010000001", phone="08010000001")
        get_or_create_wallet(self.user)
        self.device = PushDevice.objects.create(
            user=self.user, token="ExpoPushToken[device-one]", platform="android")
        self.txn = Transaction.objects.create(
            user=self.user, amount=Decimal("500"), service="Airtime — MTN",
            direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
            reference="ZTCHPUSH001")

    @patch("requests.post")
    def test_push_is_addressed_to_the_device_without_lock_screen_pii(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": [{"status": "ok"}]}
        post.return_value = response

        _push_alert(self.txn, "Debit alert: ₦500.00")

        payload = post.call_args.kwargs["json"][0]
        self.assertEqual(payload["to"], self.device.token)
        self.assertEqual(payload["channelId"], "transactions")
        self.assertNotIn(self.user.phone, payload["body"])
        self.assertNotIn("balance", payload["body"].lower())
        self.assertEqual(payload["data"]["reference"], self.txn.reference)

    @patch("requests.post")
    def test_an_uninstalled_device_is_pruned(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": [{
            "status": "error", "details": {"error": "DeviceNotRegistered"},
        }]}
        post.return_value = response
        _push_alert(self.txn, "Debit alert: ₦500.00")
        self.assertFalse(PushDevice.objects.filter(pk=self.device.pk).exists())
