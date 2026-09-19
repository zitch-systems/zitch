import {
  settledTransactionTotal,
  shouldContinueTransactionPolling,
  transactionStatusPresentation,
  txnState,
} from '@/lib/transactionStatus';

describe('transaction status display classification', () => {
  it.each(['SUCCESS', 'Successful', 'completed', 'settled', 'paid', 'approved'])(
    'allows the explicit success state %s',
    (status) => expect(txnState(status)).toBe('success'),
  );

  it.each(['FAILED', 'declined', 'reversed', 'cancelled', 'rejected', 'expired', 'voided'])(
    'maps the terminal failure state %s to failed',
    (status) => expect(txnState(status)).toBe('failed'),
  );

  it.each(['', 'unknown', 'queued', 'processing', 'SUCCESS_OR_PENDING', undefined, null])(
    'keeps unproven state %s non-successful',
    (status) => expect(txnState(status)).toBe('pending'),
  );

  it('counts only explicit success in aggregate movement totals', () => {
    const rows = [
      { status: 'SUCCESS', dir: 'in', amount: 100 },
      { status: 'pending', dir: 'in', amount: 200 },
      { status: '', dir: 'in', amount: 300 },
      { status: 'FAILED', dir: 'in', amount: 400 },
      { status: 'completed', dir: 'out', amount: -50 },
      { status: 'future-provider-state', dir: 'out', amount: -500 },
    ];

    expect(settledTransactionTotal(rows, 'in')).toBe(100);
    expect(settledTransactionTotal(rows, 'out')).toBe(50);
  });

  it('never gives failed or unknown receipts a success check', () => {
    expect(transactionStatusPresentation('Failed')).toEqual({
      state: 'failed', label: 'Failed', icon: 'x',
    });
    expect(transactionStatusPresentation('')).toEqual({
      state: 'pending', label: 'Status unavailable', icon: 'history',
    });
    expect(transactionStatusPresentation('future-provider-state').icon).toBe('history');
    expect(transactionStatusPresentation('Under review')).toEqual({
      state: 'pending', label: 'Under review', icon: 'history',
    });
  });

  it.each(['SUCCESS', 'FAILED'])(
    'stops pending polling when the backend transitions to %s',
    (settledStatus) => {
      expect(shouldContinueTransactionPolling('PENDING', 1)).toBe(true);
      expect(shouldContinueTransactionPolling(settledStatus, 2)).toBe(false);
    },
  );

  it('bounds an indefinitely pending transaction', () => {
    expect(shouldContinueTransactionPolling('PENDING', 4, 5)).toBe(true);
    expect(shouldContinueTransactionPolling('PENDING', 5, 5)).toBe(false);
  });
});
