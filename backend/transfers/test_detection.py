from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import User
from banking.test_client import CONFIG
from banking.client import ProviderError, Reply
from .models import Bank, Beneficiary
from .services import detect_account_banks, nuban_candidate


@override_settings(NINEPSB=CONFIG)
class BankDetectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.first = Bank.objects.create(code="first", name="First Bank", bank_code="011", popular=True)
        self.access = Bank.objects.create(code="access", name="Access Bank", bank_code="044", popular=True)
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", popular=True)
        Bank.objects.create(code="inactive", name="Inactive Bank", bank_code="011", active=False)

    def test_auto_detection_reads_a_matching_recipient_from_the_waas_wrapper(self):
        def enquiry(number, code):
            return Reply({"status": "SUCCESS" if code == "011" else "FAILED", "data": {
                "customer": {"account": {"number": number, "bank": code, "name": "Jane Doe"}}}}, 200)
        with patch("banking.client.Client.token", return_value="token"), \
                patch("banking.client.Client.resolve", side_effect=enquiry) as query:
            matches = detect_account_banks("0000014579")
        self.assertEqual(matches, [{"bank": "first", "bank_name": "First Bank", "name": "Jane Doe"}])
        query.assert_any_call("0000014579", "011")

    def test_cbn_dmb_and_ofi_examples_and_invalid_accounts(self):
        self.assertTrue(nuban_candidate("0000014579", "011"))
        self.assertTrue(nuban_candidate("0000014579", "000011"))
        self.assertTrue(nuban_candidate("0000214579", "950547"))
        self.assertFalse(nuban_candidate("0000014570", "011"))
        self.assertFalse(nuban_candidate("short", "011"))
        self.assertIsNone(nuban_candidate("0000014579", "090267"))

    def test_multiple_verified_candidates_are_returned_for_customer_selection_and_cached(self):
        def enquiry(number, code, **options):
            return {"success": True, "name": "Jane Doe" if code == "011" else "John Smith"}
        with patch("banking.client.Client.token", return_value="token"), \
                patch("banking.adapter.resolve_account", side_effect=enquiry) as read:
            matches = detect_account_banks("0000014579")
            again = detect_account_banks("0000014579")
        self.assertEqual({row["bank"] for row in matches}, {"first", "access"})
        self.assertEqual(matches, again)
        self.assertEqual(read.call_count, 2)
        self.assertEqual({call.args[1] for call in read.call_args_list}, {"011", "044"})

    def test_a_checksum_match_with_an_unconfirmed_name_is_never_a_recipient(self):
        with patch("banking.client.Client.token", return_value="token"), \
                patch("banking.adapter.resolve_account", return_value={"success": False, "message": "Unavailable"}):
            self.assertEqual(detect_account_banks("0000014579"), [])

    def test_only_the_requesting_customers_prior_recipients_are_used(self):
        user = User.objects.create_user(username="known")
        other = User.objects.create_user(username="other")
        Beneficiary.objects.create(user=user, name="Jane Doe", account_number="0000014579",
            bank_code="first", bank_name="First Bank")
        Beneficiary.objects.create(user=other, name="Other Person", account_number="0000014579",
            bank_code="access", bank_name="Access Bank")
        with patch("banking.client.Client.token") as token, patch("banking.adapter.resolve_account") as read:
            result = detect_account_banks("0000014579", user=user)
        self.assertEqual(result, [{"bank": "first", "bank_name": "First Bank", "name": "Jane Doe"}])
        token.assert_not_called()
        read.assert_not_called()

    def test_nip_routing_codes_do_not_replace_the_cbn_checksum_identity(self):
        bank = Bank.objects.create(code='wema', name='Wema Bank', bank_code='000017', popular=True)
        with patch('banking.client.Client.token', return_value='token'), \
                patch('banking.adapter.resolve_account', return_value={'success': True, 'name': 'Jane Doe'}) as query:
            matches = detect_account_banks('0228565772')
        self.assertIn(bank.code, {row['bank'] for row in matches})
        self.assertIn('000017', {call.args[1] for call in query.call_args_list})

    def test_a_routing_code_that_looks_like_another_cbn_code_is_not_a_checksum_match(self):
        bank = Bank.objects.create(code='unity', name='Unity Bank', bank_code='000011', popular=True)
        with patch('banking.client.Client.token', return_value='token'), \
                patch('banking.adapter.resolve_account', return_value={'success': True, 'name': 'Jane Doe'}) as query:
            matches = detect_account_banks('0000014579')
        self.assertNotIn(bank.code, {row['bank'] for row in matches})

    def test_timeouts_are_retryable_service_failures_not_missing_recipients(self):
        with patch('banking.client.Client.token', return_value='token'), \
                patch('banking.adapter.resolve_account', return_value={'success': False, 'retryable': True}), \
                self.assertRaises(ProviderError):
            detect_account_banks('0000014579')

    def test_nip_system_failure_is_not_an_empty_detected_bank_list(self):
        def enquiry(number, code):
            return Reply({'code': '96', 'customer': {'account': {
                'number': number, 'bank': code, 'name': None}}}, 200)
        with patch('banking.client.Client.token', return_value='token'), \
                patch('banking.client.Client.resolve', side_effect=enquiry), \
                self.assertRaises(ProviderError) as failure:
            detect_account_banks('0000014579')
        self.assertEqual(failure.exception.code, 'name_enquiry_unavailable')

    def test_an_in_progress_lookup_does_not_report_the_recipient_missing(self):
        with patch('banking.client.Client.token') as token, \
                patch('django.core.cache.cache.add', return_value=False), \
                self.assertRaises(ProviderError):
            detect_account_banks('0000014579')
        token.assert_not_called()
