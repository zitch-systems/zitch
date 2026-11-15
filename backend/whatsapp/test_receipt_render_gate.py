"""Receipts are drawn one at a time per process, and never twice for one alert.

On 14 November 2026 three deposits were booked in one reconcile run. The worker
drew six receipts at once (an email and a WhatsApp picture each), at about 110 MB
apiece on a 512 MB instance, and was killed mid-send: the nine alerts for those
deposits never reached the customer.
"""
import threading
import time
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from whatsapp import receipt


class RenderGateTests(SimpleTestCase):
    def setUp(self):
        receipt._gate = None
        receipt._recent.clear()
        self.addCleanup(setattr, receipt, "_gate", None)
        self.addCleanup(receipt._recent.clear)

    def _counting_draw(self):
        state = {"now": 0, "peak": 0, "calls": 0}
        lock = threading.Lock()

        def draw(title, rows, ref, *, status="Successful"):
            with lock:
                state["now"] += 1
                state["calls"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
            return f"jpeg:{ref}".encode()

        return state, draw

    def test_six_alerts_at_once_draw_one_receipt_at_a_time(self):
        state, draw = self._counting_draw()
        with patch.object(receipt, "_draw_receipt", side_effect=draw):
            threads = [threading.Thread(target=receipt.render_receipt,
                                        args=("Credit received", [("Amount", "N500")], f"REF{i}"))
                       for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(state["calls"], 6)
        self.assertEqual(state["peak"], 1)

    def test_an_alerts_email_and_whatsapp_share_one_drawing(self):
        state, draw = self._counting_draw()
        rows = [("Amount", "N21,000.00"), ("Date", "14 Nov 2026, 11:52 AM")]
        with patch.object(receipt, "_draw_receipt", side_effect=draw):
            first = receipt.render_receipt("Credit received", rows, "WEMA-CR-1")
            again = receipt.render_receipt("Credit received", list(rows), "WEMA-CR-1")
            other = receipt.render_receipt("Credit received", rows, "WEMA-CR-2")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertEqual(state["calls"], 2)

    def test_a_drawing_is_not_reused_after_two_minutes(self):
        state, draw = self._counting_draw()
        with patch.object(receipt, "_draw_receipt", side_effect=draw):
            receipt.render_receipt("Credit received", [("Amount", "N1")], "R1")
            with patch.object(receipt.time, "monotonic", return_value=time.monotonic() + 121):
                receipt.render_receipt("Credit received", [("Amount", "N1")], "R1")
        self.assertEqual(state["calls"], 2)

    def test_a_queue_that_does_not_move_gives_up_so_the_alert_goes_as_text(self):
        gate = receipt._render_gate()
        gate.acquire()
        self.addCleanup(gate.release)
        with patch.object(receipt, "RENDER_WAIT_SECONDS", 0.05), \
             patch.object(receipt, "_draw_receipt") as draw:
            with self.assertRaisesMessage(RuntimeError, "receipt_render_busy"):
                receipt.render_receipt("Credit received", [("Amount", "N1")], "R2")
        draw.assert_not_called()

    @override_settings(RECEIPT_RENDER_CONCURRENCY=2)
    def test_the_limit_is_a_setting(self):
        state, draw = self._counting_draw()
        with patch.object(receipt, "_draw_receipt", side_effect=draw):
            threads = [threading.Thread(target=receipt.render_receipt,
                                        args=("Debit receipt", [("Amount", "N1")], f"D{i}"))
                       for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(state["peak"], 2)

    def test_the_real_drawing_still_produces_a_jpeg(self):
        data = receipt.render_receipt("Credit received", [("Amount", "₦500.00")], "ZTC-GATE")
        self.assertEqual(data[:2], b"\xff\xd8")
