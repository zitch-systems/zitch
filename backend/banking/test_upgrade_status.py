from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import AccessToken, User, hash_identifier
from . import test_upgrade_rules
from .client import ProviderError, Reply
from .models import AccountUpgrade
from .test_client import CONFIG
from .upgrades import submit_upgrade


@override_settings(NINEPSB=CONFIG, SECURE_SSL_REDIRECT=False, RATELIMIT_ENABLE=False,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CustomerUpgradeStatusTests(TestCase):
    def setUp(self):
        cache.clear()
        test_upgrade_rules.UpgradeAccountNameTests.setUp(self)
        self.token = AccessToken.issue(self.user).key
        self.upgrade = AccountUpgrade.objects.create(account=self.bank, tier=2, state="submitted",
            reference="ZU-existing-upgrade", bvn_hash=self.user.bvn_hash,
            nin_hash=hash_identifier("10987654321"), request_digest="bound-request")

    def status(self, *, refresh=False, token=None):
        return self.client.post('/api/banking/status/', {'refresh_upgrade': refresh},
            content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + (token or self.token))

    def decision(self, state='APPROVED', **extra):
        return Reply({'status': 'SUCCESS', 'data': {
            'accountNumber': self.bank.number, 'status': state, **extra}}, 200)

    def wallet_reply(self, tier=2):
        return Reply({'status': 'SUCCESS', 'data': {'number': self.bank.number, 'name': self.bank.name,
            'tier': str(tier), 'pndstatus': 'InActive', 'freezeStatus': 'InActive', 'status': 'Active',
            'availableBalance': '0', 'ledgerBalance': '0'}}, 200)

    def test_pending_upgrade_is_visible_without_documents_or_another_submission(self):
        with patch('banking.client.Client.upgrade_status') as read, patch('banking.client.Client.upgrade') as send:
            response = self.status()
            resumed = submit_upgrade(self.user, 2, {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['upgrade_status'], {
            'reference': self.upgrade.reference, 'tier': 2, 'state': 'submitted', 'pending': True})
        self.assertTrue(resumed['pending'])
        self.assertEqual(resumed['reference'], self.upgrade.reference)
        read.assert_not_called()
        send.assert_not_called()

    def test_customer_refresh_requires_the_approval_and_fresh_bank_tier(self):
        with patch('banking.client.Client.upgrade_status', return_value=self.decision()) as read, \
                patch('banking.client.Client.wallet', return_value=self.wallet_reply()), \
                patch('banking.client.Client.upgrade') as send:
            response = self.status(refresh=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['upgrade_status']['state'], 'approved')
        self.assertEqual(response.json()['tier'], 2)
        read.assert_called_once_with(self.bank.number)
        send.assert_not_called()

    def test_an_approval_without_the_bank_tier_does_not_promote_the_customer(self):
        with patch('banking.client.Client.upgrade_status', return_value=self.decision()), \
                patch('banking.client.Client.wallet', return_value=self.wallet_reply(1)):
            response = self.status(refresh=True)
        self.assertTrue(response.json()['upgrade_status']['pending'])
        self.assertEqual(response.json()['bank_tier'], 1)

    def test_a_pending_decision_is_throttled_and_does_not_read_another_wallet(self):
        with patch('banking.client.Client.upgrade_status', return_value=self.decision('PENDING')) as read, \
                patch('banking.client.Client.wallet') as wallet:
            self.status(refresh=True)
            response = self.status(refresh=True)
        self.assertTrue(response.json()['upgrade_status']['pending'])
        read.assert_called_once()
        wallet.assert_not_called()

    def test_successful_status_enquiry_with_pending_message_keeps_request_open(self):
        reply = Reply({"status": "SUCCESS", "message": "Status request successful",
                       "data": {"status": "Successful", "message": "Pending"}}, 200)
        with patch('banking.client.Client.upgrade_status', return_value=reply), \
                patch('banking.client.Client.wallet') as wallet:
            result = self.status(refresh=True).json()
        self.assertTrue(result['upgrade_status']['pending'])
        self.upgrade.refresh_from_db()
        self.assertEqual(self.upgrade.last_error, 'status_pending')
        wallet.assert_not_called()

    def test_explicit_message_approval_still_needs_the_requested_wallet_tier(self):
        with patch('banking.client.Client.upgrade_status', return_value=self.decision('Successful', message='Approved')), \
                patch('banking.client.Client.wallet', side_effect=[self.wallet_reply(1), self.wallet_reply(2)]):
            first = self.status(refresh=True).json()
            cache.clear()
            second = self.status(refresh=True).json()
        self.assertTrue(first['upgrade_status']['pending'])
        self.assertEqual(second['upgrade_status']['state'], 'approved')
        self.assertEqual(second['tier'], 2)

    def test_decline_allows_the_customer_to_correct_their_details(self):
        with patch('banking.client.Client.upgrade_status', return_value=self.decision('DECLINED', isSuccessful=False)):
            response = self.status(refresh=True)
        self.assertEqual(response.json()['upgrade_status']['state'], 'declined')
        self.assertFalse(response.json()['upgrade_status']['pending'])

    def test_outages_preserve_the_request_without_an_automatic_resubmission(self):
        with patch('banking.client.Client.upgrade_status', side_effect=ProviderError('unavailable')), \
                patch('banking.client.Client.upgrade') as send:
            response = self.status(refresh=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['upgrade_refresh_unavailable'])
        self.assertTrue(response.json()['upgrade_status']['pending'])
        send.assert_not_called()

    def test_contradictory_approval_codes_or_accounts_never_promote_a_customer(self):
        replies = [self.decision(responseCode='51'), self.decision(isSuccessful=False),
                   self.decision(accountNumber='1100000002'), self.decision('APPROVED', message='Pending'),
                   self.decision('Successful', message='Approved', responseCode='51'),
                   self.decision('Successful', message='Approved', isSuccessful=False),
                   Reply({'status': 'SUCCESS', 'responseCode': '51', 'data': {
                       'accountNumber': self.bank.number, 'status': 'APPROVED'}}, 200)]
        for reply in replies:
            with self.subTest(reply=reply), patch('banking.client.Client.upgrade_status', return_value=reply), \
                    patch('banking.client.Client.wallet') as wallet:
                cache.clear()
                response = self.status(refresh=True)
                self.assertTrue(response.json()['upgrade_status']['pending'])
                self.assertTrue(response.json()['upgrade_refresh_unavailable'])
                wallet.assert_not_called()
        self.upgrade.refresh_from_db()
        self.assertEqual(self.upgrade.state, 'submitted')

    def test_success_word_or_free_text_does_not_approve_an_upgrade(self):
        for message in ('Successful', 'Your request was approved yesterday', ''):
            with self.subTest(message=message), \
                    patch('banking.client.Client.upgrade_status', return_value=self.decision('Successful', message=message)), \
                    patch('banking.client.Client.wallet') as wallet:
                cache.clear()
                self.assertTrue(self.status(refresh=True).json()['upgrade_status']['pending'])
                wallet.assert_not_called()

    def test_delayed_decline_cannot_replace_an_already_recorded_approval(self):
        def late_reply(_):
            AccountUpgrade.objects.filter(pk=self.upgrade.pk).update(state='approved')
            return self.decision('Successful', message='Declined')
        from .upgrades import refresh_upgrade
        with patch('banking.client.Client.upgrade_status', side_effect=late_reply):
            refresh_upgrade(self.bank)
        self.upgrade.refresh_from_db()
        self.assertEqual(self.upgrade.state, 'approved')

    def test_immediate_decline_is_reported_on_submission(self):
        from .upgrades import store_document, stored_documents
        profile = test_upgrade_rules.profile()
        store_document(self.user, 'utilityBill', profile.pop('utilityBill'))
        self.upgrade.delete()
        with patch('banking.upgrades.update_account'), \
                patch('banking.client.Client.upgrade', return_value=Reply({'status': 'SUCCESS'}, 200)), \
                patch('banking.client.Client.upgrade_status', return_value=self.decision('Successful', message='Declined')):
            result = submit_upgrade(self.user, 2, profile)
        self.assertFalse(result['pending'])
        self.assertFalse(result['success'])
        self.assertEqual(AccountUpgrade.objects.get().state, 'declined')
        self.assertIn('utilityBill', stored_documents(self.user))

    def test_accepted_submission_releases_the_uploaded_documents(self):
        from .upgrades import store_document, stored_documents
        profile = test_upgrade_rules.profile()
        store_document(self.user, 'utilityBill', profile.pop('utilityBill'))
        self.upgrade.delete()
        with patch('banking.upgrades.update_account'), \
                patch('banking.client.Client.upgrade', return_value=Reply({'status': 'SUCCESS'}, 200)), \
                patch('banking.client.Client.upgrade_status', return_value=self.decision('Successful', message='Pending')):
            result = submit_upgrade(self.user, 2, profile)
        self.assertTrue(result['pending'])
        self.assertEqual(stored_documents(self.user), {})

    def test_other_customers_cannot_read_or_refresh_the_request(self):
        other = User.objects.create_user(username='different-upgrade-owner')
        with patch('banking.client.Client.upgrade_status') as read:
            response = self.status(refresh=True, token=AccessToken.issue(other).key)
        self.assertIsNone(response.json()['upgrade_status'])
        read.assert_not_called()

    def test_a_cache_outage_preserves_the_saved_status(self):
        with patch('django.core.cache.cache.add', side_effect=RuntimeError('cache unavailable')), \
                patch('banking.client.Client.upgrade_status') as read:
            response = self.status(refresh=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['upgrade_refresh_unavailable'])
        read.assert_not_called()


@override_settings(NINEPSB=CONFIG, SECURE_SSL_REDIRECT=False, RATELIMIT_ENABLE=False,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class UndecidedUpgradeEvidenceTests(TestCase):
    """An upgrade the bank has not decided must say so, not just move checked_at."""

    def setUp(self):
        cache.clear()
        test_upgrade_rules.UpgradeAccountNameTests.setUp(self)
        self.upgrade = AccountUpgrade.objects.create(account=self.bank, tier=2, state="submitted",
            reference="ZU-undecided-upgrade", bvn_hash=self.user.bvn_hash,
            nin_hash=hash_identifier("10987654321"), request_digest="bound-request")

    def refresh(self, state):
        from .upgrades import refresh_upgrade
        reply = Reply({"status": "SUCCESS", "responseCode": "00",
                       "data": {"accountNumber": self.bank.number, "status": state}}, 200)
        with patch("banking.client.Client.upgrade_status", return_value=reply):
            approved = refresh_upgrade(self.bank)
        self.upgrade.refresh_from_db()
        return approved

    def test_a_bank_still_deciding_is_recorded_rather_than_left_blank(self):
        self.assertFalse(self.refresh("PENDING"))
        self.assertEqual(self.upgrade.state, "submitted")
        self.assertEqual(self.upgrade.last_error, "status_pending")
        self.assertIsNotNone(self.upgrade.checked_at)

    def test_a_status_this_build_does_not_know_is_distinguishable_from_waiting(self):
        self.assertFalse(self.refresh("AWAITING-COMPLIANCE-REVIEW"))
        self.assertEqual(self.upgrade.state, "submitted")
        self.assertEqual(self.upgrade.last_error, "status_unrecognized")
