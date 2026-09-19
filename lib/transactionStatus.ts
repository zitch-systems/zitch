/** Conservative display classification for immutable-ledger transaction rows. */
export type TxnState = 'success' | 'pending' | 'failed';

const SUCCESS = new Set([
  'success',
  'successful',
  'complete',
  'completed',
  'settled',
  'paid',
  'approved',
]);

const FAILED = /fail|declin|revers|cancel|reject|expire|void/;

export function txnState(status: unknown): TxnState {
  const normalized = String(status ?? '').trim().toLowerCase();
  if (FAILED.test(normalized)) return 'failed';
  if (SUCCESS.has(normalized)) return 'success';
  // Blank, malformed and future provider states are not proof of settlement.
  return 'pending';
}

export function transactionStatusPresentation(status: unknown): {
  state: TxnState;
  label: string;
  icon: 'check' | 'history' | 'x';
} {
  const state = txnState(status);
  const label = String(status ?? '').trim() || 'Status unavailable';
  return {
    state,
    label,
    icon: state === 'success' ? 'check' : state === 'failed' ? 'x' : 'history',
  };
}

export function settledTransactionTotal(
  transactions: readonly { status: unknown; dir: string; amount: number }[],
  direction: 'in' | 'out',
): number {
  return transactions
    .filter((transaction) => (
      transaction.dir === direction && txnState(transaction.status) === 'success'
    ))
    .reduce((total, transaction) => total + Math.abs(transaction.amount), 0);
}

/** Keep status refresh bounded and stop as soon as settlement is terminal. */
export function shouldContinueTransactionPolling(
  status: unknown,
  completedPolls: number,
  maxPolls = 5,
): boolean {
  return txnState(status) === 'pending' && completedPolls < maxPolls;
}
