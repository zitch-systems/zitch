"""What happens when the customer types instead of tapping the secure screen.

Three defects from one production thread, and the invariant each one leaves:

* An open identity Flow answered EVERY chat message with "enter it on the secure
  screen" — including "Send 500 to Mutumin", which is plainly a new instruction,
  and including the NIN itself, which was already in the thread by the time we
  refused to read it. Refusing does not un-send it; it only adds a dead end.
* "Send 1000 to Mutumin 2217940528" opened the guided form rather than the
  confirm, because the message named no bank. The NUBAN's check digit encodes
  the bank code, so nothing was actually missing.
* The Flow's closing screen rendered one sentence for every ending, so a settled
  transfer, a queued one and a refused one looked alike at a glance.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from transfers.models import Bank
from whatsapp import router
from whatsapp.flows import ACCOUNT_OTP, FLOW_ID_STATE, _success_screen
from whatsapp.models import PendingAction
from whatsapp.test_flows import MSISDN, _make_user


def _identity_action(user, action_type="kyc", **payload):
    return PendingAction.objects.create(
        user=user, msisdn=MSISDN, action_type=action_type, state=FLOW_ID_STATE,
        payload={"id_kind": "bvn", "id_fallback_state": "bvn", **payload},
        expires_at=timezone.now() + timedelta(minutes=10))


class TypedIdentityIsAnsweredTests(TestCase):
    """The escape hatch and the typed-answer path on an open identity Flow."""

    def setUp(self):
        self.user = _make_user()

    def test_a_new_instruction_leaves_the_verification(self):
        """The hatch PIN_SCREEN and TRANSFER_FORM already had. Without it the
        customer is answered with the same line forever and told no way out."""
        pa = _identity_action(self.user)
        with patch.object(router, "reply") as reply, \
             patch.object(router, "handle_inbound") as inbound:
            router._advance(pa, self.user, MSISDN, "Send 500 to Mutumin")
        inbound.assert_called_once_with(MSISDN, "Send 500 to Mutumin")
        self.assertIn("leaving that verification", reply.call_args[0][1])
        self.assertFalse(PendingAction.objects.filter(pk=pa.pk).exists())

    def test_a_typed_identity_number_is_processed_not_refused(self):
        pa = _identity_action(self.user)
        with patch.object(router, "reply") as reply, \
             patch.object(router, "_advance_kyc") as advance:
            router._advance(pa, self.user, MSISDN, "81083752916")
        advance.assert_called_once_with(pa, self.user, MSISDN, "81083752916")
        pa.refresh_from_db()
        self.assertEqual(pa.state, "bvn")   # handed to the chat step, not left armed
        self.assertIn("delete your message", reply.call_args[0][1].lower())

    def test_add_account_routes_to_its_own_handler(self):
        """Signup parks both ID types in one 'bvn' entry state and has a
        different advance function; sending it to the KYC one would ask the
        wrong questions about a number that is already right."""
        pa = _identity_action(self.user, action_type="add_account")
        with patch.object(router, "reply"), \
             patch.object(router, "_advance_add_account") as advance:
            router._advance(pa, self.user, MSISDN, "81083752916")
        advance.assert_called_once()

    def test_a_typed_account_code_is_processed(self):
        pa = _identity_action(self.user, action_type="add_account",
                              id_kind=ACCOUNT_OTP, tracking_id="t1")
        with patch.object(router, "reply"), \
             patch.object(router, "_advance_add_account") as advance:
            router._advance(pa, self.user, MSISDN, "123456")
        advance.assert_called_once()
        pa.refresh_from_db()
        self.assertEqual(pa.state, "otp")

    def test_a_typed_email_address_is_processed_and_not_read_as_a_new_command(self):
        """The one identity answer that contains letters. Recognised by shape
        before the new-instruction hatch, which would otherwise abandon the
        verification the customer was in the middle of completing."""
        pa = _identity_action(self.user, id_kind="email", id_step="address")
        with patch.object(router, "reply"), \
             patch.object(router, "_advance_kyc") as advance, \
             patch.object(router, "handle_inbound") as inbound:
            router._advance(pa, self.user, MSISDN, "ada@example.com")
        advance.assert_called_once()
        inbound.assert_not_called()
        pa.refresh_from_db()
        self.assertEqual(pa.state, "email_address")

    def test_an_email_address_gets_no_delete_tip(self):
        """It is not a secret, and telling someone to delete their own address
        is noise that makes the real warning cheaper."""
        pa = _identity_action(self.user, id_kind="email", id_step="address")
        with patch.object(router, "reply") as reply, \
             patch.object(router, "_advance_kyc"):
            router._advance(pa, self.user, MSISDN, "ada@example.com")
        self.assertFalse(any("delete" in str(c).lower() for c in reply.call_args_list))

    def test_resend_at_a_step_with_no_code_does_not_cancel_the_verification(self):
        """"resend" is plainly about the step in progress. The new-instruction
        hatch would read it as changing the subject and throw the step away."""
        pa = _identity_action(self.user)
        with patch.object(router, "reply") as reply, \
             patch.object(router, "handle_inbound") as inbound:
            router._advance(pa, self.user, MSISDN, "resend")
        inbound.assert_not_called()
        self.assertIn("no code to re-send", reply.call_args[0][1])
        self.assertTrue(PendingAction.objects.filter(pk=pa.pk).exists())

    def test_a_wrong_length_number_is_nudged_rather_than_treated_as_a_command(self):
        pa = _identity_action(self.user)
        with patch.object(router, "reply") as reply, \
             patch.object(router, "handle_inbound") as inbound:
            router._advance(pa, self.user, MSISDN, "8108375")
        inbound.assert_not_called()
        self.assertIn("11-digit BVN", reply.call_args[0][1])


class BankDetectedFromTheAccountNumberTests(TestCase):
    """"Send 1000 to Mutumin 2217940528" — a complete instruction that used to
    open the guided form because it named no bank."""

    def setUp(self):
        self.user = _make_user()
        # 058 + 221794052 -> the CBN check digit the tests below rely on.
        self.gtb = Bank.objects.create(code="gtb", name="GTBank", bank_code="058",
                                       color="#e30613", active=True, popular=True)

    def _nuban_for(self, bank_code, serial="221794052"):
        seq = bank_code + serial
        total = sum(int(c) * (3, 7, 3)[i % 3] for i, c in enumerate(seq))
        return serial + str((10 - total % 10) % 10)

    def test_an_unambiguous_nuban_reaches_the_confirm_without_a_bank_name(self):
        acct = self._nuban_for("058")
        self.assertEqual(router.nuban_bank_candidates(acct), [self.gtb])
        with patch.object(router, "payout_resolve_account",
                          return_value={"success": True, "name": "Mutumin Sani", "mock": True}), \
             patch.object(router, "reply") as reply:
            handled = router._begin_bank_transfer(
                self.user, MSISDN, Decimal("1000"), acct, "Send 1000 to Mutumin")
        self.assertTrue(handled)
        pa = PendingAction.objects.get(msisdn=MSISDN)
        self.assertEqual(pa.payload["bank_code"], "058")
        self.assertIn("MUTUMIN SANI", reply.call_args[0][1].upper())

    def test_the_checksum_narrows_an_ambiguous_name_match(self):
        """_match_banks substring-matches over a whole sentence, so several
        banks matching is routine — and only one of them owns the account."""
        other = Bank.objects.create(code="ster", name="Sterling", bank_code="232",
                                    color="#c00", active=True)
        acct = self._nuban_for("058")
        with patch.object(router, "_match_banks", return_value=[self.gtb, other]), \
             patch.object(router, "payout_resolve_account",
                          return_value={"success": True, "name": "Mutumin Sani", "mock": True}), \
             patch.object(router, "reply"):
            router._begin_bank_transfer(self.user, MSISDN, Decimal("1000"), acct, "anything")
        self.assertEqual(PendingAction.objects.get(msisdn=MSISDN).payload["bank_code"], "058")

    def test_a_named_bank_still_wins_over_the_checksum(self):
        """The checksum is a suggestion; a bank the customer actually named is
        an instruction. Never silently overridden."""
        other = Bank.objects.create(code="ster", name="Sterling", bank_code="232",
                                    color="#c00", active=True)
        acct = self._nuban_for("058")   # checksum says GTBank
        with patch.object(router, "_match_banks", return_value=[other]), \
             patch.object(router, "payout_resolve_account",
                          return_value={"success": True, "name": "Mutumin Sani", "mock": True}), \
             patch.object(router, "reply"):
            router._begin_bank_transfer(self.user, MSISDN, Decimal("1000"), acct, "sterling")
        self.assertEqual(PendingAction.objects.get(msisdn=MSISDN).payload["bank_code"], "232")

    def test_an_unresolvable_account_still_falls_back_to_the_guided_form(self):
        """False is the caller's signal to open the form — the fallback must
        survive, or a genuinely ambiguous message dead-ends."""
        Bank.objects.all().delete()
        self.assertFalse(router._begin_bank_transfer(
            self.user, MSISDN, Decimal("1000"), "2217940528", "Send 1000 to Mutumin"))

    def test_the_ai_transfer_intent_no_longer_needs_a_bank_name(self):
        acct = self._nuban_for("058")
        with patch.object(router, "payout_resolve_account",
                          return_value={"success": True, "name": "Mutumin Sani", "mock": True}), \
             patch.object(router, "reply"), \
             patch.object(router, "_start_transfer") as form:
            router.dispatch_intent(self.user, MSISDN, {
                "name": "transfer",
                "input": {"amount": 1000, "account_number": acct, "bank_name": None}})
        form.assert_not_called()


class TerminalScreenReportsTheOutcomeTests(TestCase):
    """The closing screen says WHICH ending this was, in its heading."""

    def test_each_outcome_gets_its_own_heading(self):
        self.assertEqual(_success_screen("x", "success")["data"]["status"], "✅ Successful")
        self.assertEqual(_success_screen("x", "pending")["data"]["status"], "⏳ Pending")
        self.assertEqual(_success_screen("x", "failed")["data"]["status"], "❌ Not completed")

    def test_an_untagged_outcome_claims_nothing(self):
        """Executors that have not been tagged, and the non-money terminals (an
        expired session, a signup that ended in the chat), must not be rendered
        as a successful payment."""
        self.assertEqual(_success_screen("Session ended.")["data"]["status"], "Done")
        self.assertEqual(_success_screen("x", "nonsense")["data"]["status"], "Done")

    def test_a_queued_payment_closes_as_pending_not_successful(self):
        """Meta allows the data-exchange 10 seconds and a payout takes longer,
        so the Flow closes before the rail has answered. Claiming success there
        would assert an outcome nobody has established."""
        user = _make_user()
        pa = PendingAction.objects.create(
            user=user, msisdn=MSISDN, action_type="transfer", state="flow_pin",
            payload={"amount": "1000"}, expires_at=timezone.now() + timedelta(minutes=5))
        with override_settings(WHATSAPP_PROCESS_INLINE=False), \
             patch("whatsapp.jobs.enqueue_flow_execution"), \
             patch("whatsapp.jobs.drain_in_background"):
            outcome = router.authorise_flow_execution(pa, user)
        self.assertEqual(outcome.status, router.OUTCOME_PENDING)
        self.assertNotIn("✅", outcome)

    def test_a_plain_string_outcome_reads_as_done(self):
        """Outcome subclasses str precisely so untagged executors keep working."""
        self.assertEqual(getattr("just a line", "status", ""), "")
        self.assertEqual(router.Outcome("done").status, "done")
