import { isFundingAccountReady, walletBalances, walletCapabilities, walletCapabilityMessage } from '@/lib/services/wallet';

jest.mock('@/lib/api', () => ({ apiJson: jest.fn() }));

const ready = { success: true, provider: 'partnership', can_fund: true, available: true,
  has_account: true, account_setup_state: 'ready', account_number: '0454243073' };

describe('Partnership account capabilities', () => {
  it.each([undefined, {}, { provider: 'wema_vas', bill_payments_available: true, transfers_available: true },
    { provider: 'unknown' }, { provider: 'partnership', legacy_funding_review: true },
    { provider: 'partnership', account_setup_state: 'legacy_funding_review' },
    { provider: 'partnership', spending_available: false }])('fails closed for absent, retired or held accounts: %j', (value) => {
    expect(walletCapabilities(value)).toEqual({ billPaymentsAvailable: false, transfersAvailable: false });
  });
  it('honors separate server capabilities for the active provider', () => {
    expect(walletCapabilities({ provider: 'partnership', bill_payments_available: true, transfers_available: false }))
      .toEqual({ billPaymentsAvailable: true, transfersAvailable: false });
    expect(walletCapabilities({ provider: 'partnership' }))
      .toEqual({ billPaymentsAvailable: true, transfersAvailable: true });
  });
  it('describes the actual service capabilities', () => {
    expect(walletCapabilityMessage({ billPaymentsAvailable: true, transfersAvailable: false })).toContain('Transfers are currently unavailable');
    expect(walletCapabilityMessage({ billPaymentsAvailable: true, transfersAvailable: true })).toBe('');
  });
  it('preserves total and historical balances while preventing spending of held funds', () => {
    expect(walletBalances({ provider: 'partnership', wallet: '6000', available_balance: '1000', historical_balance: '5000' }))
      .toEqual({ totalBalance: 6000, availableBalance: 1000, historicalBalance: 5000 });
    expect(walletBalances({ provider: 'partnership', legacy_funding_review: true, wallet: '6000', available_balance: '6000' }))
      .toEqual({ totalBalance: 6000, availableBalance: 0, historicalBalance: 6000 });
    expect(walletBalances({ provider: 'wema_vas', wallet: '6000', available_balance: '6000' }).availableBalance).toBe(0);
  });
  it.each(['invalid', '-10', 'Infinity'])('does not publish invalid available funds: %s', (amount) => {
    expect(walletBalances({ provider: 'partnership', wallet: '6000', available_balance: amount }).availableBalance).toBe(0);
  });
  it('caps available funds to total and preserves the existing Partnership balance fallback', () => {
    expect(walletBalances({ provider: 'partnership', wallet: '20', available_balance: '100' }).availableBalance).toBe(20);
    expect(walletBalances({ provider: 'partnership', wallet: '20' }).availableBalance).toBe(20);
  });
  it('accepts only a currently confirmed Partnership funding destination', () => {
    expect(isFundingAccountReady(ready)).toBe(true);
    expect(isFundingAccountReady({ ...ready, account_setup_state: 'bank_history_review', spending_available: false })).toBe(true);
    for (const override of [{ provider: 'wema_vas' }, { provider: undefined }, { can_fund: false }, { can_fund: undefined },
      { available: false }, { has_account: false }, { account_setup_state: 'processing' }, { legacy_funding_review: true },
      { account_number: '7111234567' }, { account_number: '123' }]) {
      expect(isFundingAccountReady({ ...ready, ...override })).toBe(false);
    }
  });
});
