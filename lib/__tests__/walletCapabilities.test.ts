import { walletCapabilities, walletCapabilityMessage } from '@/lib/services/wallet';

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
