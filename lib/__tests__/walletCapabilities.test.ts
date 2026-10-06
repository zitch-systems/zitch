import { walletBalances, walletCapabilities, walletCapabilityMessage } from '@/lib/services/wallet';

jest.mock('@/lib/api', () => ({ apiJson: jest.fn() }));

describe('wallet capability display', () => {
  it('uses separate bill and transfer capabilities over the generic spending flag', () => {
    const capabilities = walletCapabilities({
      provider: 'wema_vas', spending_available: false,
      bill_payments_available: true, transfers_available: false,
    });
    expect(capabilities).toEqual({ billPaymentsAvailable: true, transfersAvailable: false });
    expect(walletCapabilityMessage(capabilities)).toBe('Bill payments are available. Transfers are currently unavailable.');
  });

  it('does not infer VAS bill eligibility or transfer availability from a missing capability', () => {
    expect(walletCapabilities({ provider: 'wema_vas' })).toEqual({
      billPaymentsAvailable: false, transfersAvailable: false,
    });
    expect(walletCapabilities({ provider: 'wema_vas', spending_available: true })).toEqual({
      billPaymentsAvailable: false, transfersAvailable: false,
    });
    expect(walletCapabilities({ provider: 'wema_vas', bill_payments_available: true }).transfersAvailable).toBe(false);
  });

  it('never enables payments or spendable funds for a VAS validation account', () => {
    const response = { provider: 'wema_vas' as const, test_mode: true,
      bill_payments_available: true, transfers_available: true, spending_available: true,
      wallet: '1000.00', available_balance: '1000.00' };
    expect(walletCapabilities(response)).toEqual({ billPaymentsAvailable: false, transfersAvailable: false });
    expect(walletBalances(response).availableBalance).toBe(0);
  });

  it('preserves older Partnership responses while respecting explicit per-service restrictions', () => {
    expect(walletCapabilityMessage(walletCapabilities({ provider: 'partnership' }))).toBe('');
    expect(walletCapabilities({ spending_available: false })).toEqual({
      billPaymentsAvailable: false, transfersAvailable: false,
    });
    expect(walletCapabilityMessage(walletCapabilities({
      provider: 'partnership', spending_available: true, bill_payments_available: false,
    }))).toBe('Bill payments are currently unavailable.');
  });
});

describe('wallet balance separation', () => {
  const vas = { provider: 'wema_vas' as const, wallet: '6000.00', vas_balance: '1000.00',
    historical_balance: '5000.00', bill_payments_available: true, transfers_available: false };

  it('keeps late legacy credits out of VAS affordability checks', () => {
    expect(walletBalances({ ...vas, available_balance: '1000.00' })).toEqual({
      totalBalance: 6000, availableBalance: 1000, historicalBalance: 5000,
    });
  });

  it('preserves both kinds of held funds while a VAS bill gate is disabled', () => {
    expect(walletBalances({ ...vas, available_balance: '0.00', bill_payments_available: false })).toEqual({
      totalBalance: 6000, availableBalance: 0, historicalBalance: 5000,
    });
    expect(walletBalances({ ...vas, available_balance: '1000.00', bill_payments_available: false }).availableBalance).toBe(0);
  });

  it('fails closed when VAS available funds are missing or malformed', () => {
    for (const available_balance of [undefined, 'invalid', '-1']) {
      expect(walletBalances({ ...vas, available_balance }).availableBalance).toBe(0);
    }
    expect(walletBalances({ ...vas, wallet: '500.00', available_balance: '1000.00' }).availableBalance).toBe(500);
  });

  it('preserves the legacy wallet balance contract', () => {
    expect(walletBalances({ wallet: '6000.00' })).toEqual({
      totalBalance: 6000, availableBalance: 6000, historicalBalance: 0,
    });
  });
});
