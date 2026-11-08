from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from exams.models import ExamProduct
from wallet.services import get_or_create_wallet
from whatsapp import router
from whatsapp.models import PendingAction
from whatsapp.test_flows import MSISDN, _make_user


@override_settings(TESTING=True)
class WhatsAppExamPinTests(TestCase):
    def setUp(self):
        self.user = _make_user(balance="10000")
        self.product = ExamProduct.objects.create(
            code="waec", name="WAEC", description="Result Checker PIN",
            price=Decimal("3500"), service_id="waec-registration", active=True)

    def test_exam_pin_is_a_numbered_menu_action(self):
        self.assertIn("🔟  🎓 Exam PIN", router.menu_text())

    @patch.object(router, "reply")
    def test_the_guided_flow_collects_product_quantity_and_phone(self, reply):
        router._start_exam(self.user, MSISDN)
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual(pa.state, "product")
        router._advance_exam(pa, self.user, MSISDN, "1")
        pa.refresh_from_db()
        self.assertEqual(pa.state, "quantity")
        router._advance_exam(pa, self.user, MSISDN, "2")
        pa.refresh_from_db()
        self.assertEqual(pa.state, "phone")

        router._advance_exam(pa, self.user, MSISDN, "me")
        pa.refresh_from_db()
        self.assertEqual(pa.payload["amount"], "7000.00")
        confirm = reply.call_args[0][1]
        self.assertIn("Confirm Exam PIN", confirm)
        self.assertIn("Available balance ₦10,000.00", confirm)

    @patch.object(router, "reply_receipt")
    @patch.object(router, "vtu_purchase",
                  return_value={"success": True, "pins": ["WAEC-ABC-123"]})
    def test_success_debits_once_and_delivers_the_pin(self, purchase, receipt):
        pa = PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="exam", state="pin",
            payload={
                "pin_attempts": 0, "exam_code": "waec", "exam_name": "WAEC",
                "description": "Result Checker PIN", "service_id": "waec-registration",
                "quantity": 1, "phone": self.user.phone, "unit_price": "3500.00",
                "amount": "3500.00", "meta": {"exam": "waec"},
            },
            expires_at=router.timezone.now() + router.FLOW_TTL,
        )
        outcome = router._exec_exam(pa, self.user, MSISDN)
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("6500"))
        self.assertIn("successful", str(outcome))
        self.assertIn("WAEC-ABC-123", str(receipt.call_args))
        purchase.assert_called_once()
