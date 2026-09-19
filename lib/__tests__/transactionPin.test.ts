import {
  isValidTransactionPin,
  TRANSACTION_PIN_LENGTH,
} from '@/lib/transactionPin';

describe('transaction PIN policy', () => {
  it('uses the backend six-digit contract', () => {
    expect(TRANSACTION_PIN_LENGTH).toBe(6);
    expect(isValidTransactionPin('135790')).toBe(true);
  });

  it.each(['1234', '1234567', '12a456', '', ' 123456 '])(
    'rejects %p',
    (pin) => {
      expect(isValidTransactionPin(pin)).toBe(false);
    },
  );
});
