import React, { type ReactNode } from 'react';
import { Text } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import GetLoan from '@/app/(servicesscreen)/getloan';

const mockGetRates = jest.fn();
const mockGetLoanStatus = jest.fn();
const mockConfirm = jest.fn();

jest.mock('@/lib/services/savings', () => ({
  savingsService: { getRates: (...args: unknown[]) => mockGetRates(...args), create: jest.fn() },
}));
jest.mock('@/lib/services/loans', () => ({
  loansService: { getStatus: (...args: unknown[]) => mockGetLoanStatus(...args), request: jest.fn() },
}));
jest.mock('@/lib/pendingSpend', () => ({ acquireSpendAttempt: jest.fn(), clearSpendAttempt: jest.fn() }));
jest.mock('@/lib/spendOutcome', () => ({ classifySpendResponse: jest.fn(), isRecoveredSpendResponse: jest.fn() }));
jest.mock('expo-router', () => ({ router: { back: jest.fn(), replace: jest.fn(), push: jest.fn() } }));
jest.mock('@/lib/wallet', () => ({ useWallet: () => ({ balance: 20000, billPaymentsAvailable: true, transfersAvailable: true, reload: jest.fn() }) }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Receipt', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { surface3: '#eee', ink1: '#111', ink3: '#333' } }),
  font: { bold: 'bold', regular: 'regular' },
}));
jest.mock('@/components/design/widgets', () => ({ Hero: () => null }));
jest.mock('@/components/design/flowkit', () => ({
  Label: () => null,
  QuickAmounts: () => null,
  ConfirmSheet: (props: Record<string, unknown>) => { mockConfirm(props); return null; },
  BalanceHint: () => null,
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text: NativeText, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Header: () => null,
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) => ReactActual.createElement(
      Pressable,
      { accessibilityLabel: label, onPress, disabled },
      ReactActual.createElement(NativeText, null, label),
    ),
    Field: () => null,
    Sheet: () => null,
    PinPad: () => null,
    money: (amount: number) => `₦${amount}`,
    Naira: () => null,
    NText: NativeText,
  };
});


it('asks to Get the loan into the wallet, not to Pay for it', async () => {
  mockGetLoanStatus.mockResolvedValueOnce({
    success: true, product_available: true, repayment_available: true,
    limit: 50000, available: 50000, quote_rate: '0.045', active_loan: null,
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<GetLoan />); });
  await act(async () => { await Promise.resolve(); });
  const props = mockConfirm.mock.calls[mockConfirm.mock.calls.length - 1][0] as Record<string, unknown>;
  expect(props.title).toBe('Confirm loan');
  expect(String(props.cta)).toMatch(/^Get ₦/);
  expect(props.methodTitle).toBe('Sent to');
  expect(String(props.methodSub)).toContain('Your credit available');
  act(() => tree.unmount());
});
