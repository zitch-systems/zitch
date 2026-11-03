from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import AccessToken, User
from wallet.models import Transaction
from wallet.services import debit
from .client import ProviderError, Reply
from .models import Instruction
from .payments import bind_instruction
from .test_client import CONFIG
from . import test_money


@override_settings(NINEPSB=CONFIG)
class CustomerPaymentStatusTests(TestCase):
    def setUp(self):
        cache.clear()
        test_money.MoneyTests.setUp(self)
        self.token = AccessToken.issue(self.user).key
        self.txn = debit(self.user, Decimal('100'), 'Airtime',
            meta={'provider': '9psb', 'provider_purchase': True, 'vas_type': 'airtime'},
            idempotency_key='customer-airtime')
        self.instruction = bind_instruction(self.txn, 'vas', 'airtime', {
            'source': self.bank.number, 'amount': '100.00', 'phone': self.user.phone,
            'network': 'MTN', 'product_id': ''})
        Instruction.objects.filter(pk=self.instruction.pk).update(state='pending')

    def status(self, token=None):
        return self.client.post('/api/transaction/status/', data={
            'access_token': token or self.token, 'reference': self.txn.reference}, content_type='application/json')

    def reply(self, state):
        return Reply({'status': 'success', 'responseCode': '200', 'data': {
            'transactionStatus': state, 'amount': '100', 'recipient': self.user.phone,
            'debitAccount': self.bank.number, 'network': 'MTN'}}, 200)

    def test_customer_refresh_recovers_success_without_another_purchase(self):
        with patch('banking.client.Client.vas_status', return_value=self.reply('SUCCESS')) as query, \
                patch('banking.client.Client.topup') as send:
            response = self.status()
            self.status()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['transaction']['transaction_status'], Transaction.SUCCESS)
        query.assert_called_once_with(self.instruction.reference, bill=False)
        send.assert_not_called()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 400)

    def test_pending_queries_are_throttled_and_never_refunded(self):
        with patch('banking.client.Client.vas_status', return_value=self.reply('PENDING')) as query:
            self.status()
            response = self.status()
        query.assert_called_once()
        self.assertEqual(response.json()['transaction']['transaction_status'], Transaction.PENDING)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 400)

    def test_a_confirmed_failure_refunds_once(self):
        with patch('banking.client.Client.vas_status', return_value=self.reply('FAILED')) as query:
            response = self.status()
            self.status()
        query.assert_called_once()
        self.assertEqual(response.json()['transaction']['transaction_status'], Transaction.FAILED)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 500)

    def test_an_outage_keeps_the_reserved_payment_pending(self):
        with patch('banking.client.Client.vas_status', side_effect=ProviderError('network_unavailable')):
            response = self.status()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['transaction']['transaction_status'], Transaction.PENDING)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 400)

    def test_reading_a_prepared_instruction_never_dispatches_it(self):
        Instruction.objects.filter(pk=self.instruction.pk).update(state='prepared')
        with patch('banking.client.Client.topup') as send, patch('banking.client.Client.vas_status') as query:
            self.status()
        send.assert_not_called()
        query.assert_not_called()

    def test_another_customer_cannot_trigger_a_bank_status_read(self):
        other = User.objects.create_user(username='different-customer')
        with patch('banking.client.Client.vas_status') as query:
            response = self.status(AccessToken.issue(other).key)
        self.assertEqual(response.status_code, 404)
        query.assert_not_called()

    def test_wrong_environment_records_are_not_sent_to_current_bank(self):
        with override_settings(NINEPSB={**CONFIG, 'ENVIRONMENT': 'sandbox'}), \
                patch('banking.client.Client.vas_status') as query:
            response = self.status()
        self.assertEqual(response.status_code, 200)
        query.assert_not_called()
