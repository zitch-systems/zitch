import React from 'react';
import renderer, { act } from 'react-test-renderer';
import { WalletProvider, useWallet } from '@/lib/wallet';

const mockApiPost = jest.fn();
jest.mock('@/lib/api', () => ({
  apiPost: (...args: unknown[]) => mockApiPost(...args),
  apiJson: jest.fn().mockResolvedValue({ accounts: [] }),
}));
jest.mock('@/lib/secureStore', () => ({
  getToken: jest.fn().mockResolvedValue('test-token'),
  saveDisplayName: jest.fn().mockResolvedValue(undefined),
  saveSpendAccountNamespace: jest.fn().mockResolvedValue(undefined),
}));

let current: ReturnType<typeof useWallet>;
const Capture = () => { current = useWallet(); return null; };

describe('wallet context purchase balance', () => {
  let tree: renderer.ReactTestRenderer;
  beforeEach(() => { jest.useFakeTimers(); mockApiPost.mockReset(); });
  afterEach(() => { act(() => { tree?.unmount(); }); jest.useRealTimers(); });

  it.each([true, false])('publishes canonical VAS funds to existing bill consumers, bill gate: %s', async (enabled) => {
    mockApiPost.mockImplementation(async (path: string) => ({ json: async () => path === '/api/wallet_balance/'
      ? { success: true, provider: 'wema_vas', wallet: '6000.00', available_balance: enabled ? '1000.00' : '0.00',
          historical_balance: '5000.00', vas_balance: '1000.00', bill_payments_available: enabled, transfers_available: false }
      : { status: true, all_site_transactions: [] } }));
    act(() => { tree = renderer.create(<WalletProvider><Capture /></WalletProvider>); });
    await act(async () => { await current.reload(); });
    expect(current.balance).toBe(enabled ? 1000 : 0);
    expect(current.availableBalance).toBe(current.balance);
    expect(current.totalBalance).toBe(6000);
    expect(current.historicalBalance).toBe(5000);
    expect(current.transfersAvailable).toBe(false);
  });

  it('keeps a test account out of ordinary funding and payment surfaces', async () => {
    mockApiPost.mockImplementation(async (path: string) => ({ json: async () => path === '/api/wallet_balance/'
      ? { success: true, provider: 'wema_vas', test_mode: true, account_number: '7111234567',
          available: true, has_account: true, account_setup_state: 'ready', spending_available: true,
          wallet: '1000.00', available_balance: '1000.00', bill_payments_available: true, transfers_available: true }
      : { status: true, all_site_transactions: [] } }));
    act(() => { tree = renderer.create(<WalletProvider><Capture /></WalletProvider>); });
    await act(async () => { await current.reload(); });
    expect(current.accountNumber).toBe('');
    expect(current.balance).toBe(0);
    expect(current.spendingAvailable).toBe(false);
    expect(current.billPaymentsAvailable).toBe(false);
    expect(current.transfersAvailable).toBe(false);
  });

  it('never publishes a reserved validation-prefix account as a live funding number', async () => {
    mockApiPost.mockImplementation(async (path: string) => ({ json: async () => path === '/api/wallet_balance/'
      ? { success: true, provider: 'wema_vas', test_mode: false, account_number: '7111234567',
          available: true, has_account: true, account_setup_state: 'ready' }
      : { status: true, all_site_transactions: [] } }));
    act(() => { tree = renderer.create(<WalletProvider><Capture /></WalletProvider>); });
    await act(async () => { await current.reload(); });
    expect(current.accountNumber).toBe('');
  });
});
