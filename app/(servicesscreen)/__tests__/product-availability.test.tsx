import React, { type ReactNode } from 'react';
import { Text } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import FixedSave from '@/app/(servicesscreen)/fixedsave';
import GetLoan from '@/app/(servicesscreen)/getloan';

const mockGetRates = jest.fn();
const mockGetLoanStatus = jest.fn();

jest.mock('@/lib/services/savings', () => ({
  savingsService: { getRates: (...args: unknown[]) => mockGetRates(...args), create: jest.fn() },
}));
jest.mock('@/lib/services/loans', () => ({
  loansService: { getStatus: (...args: unknown[]) => mockGetLoanStatus(...args), request: jest.fn() },
}));
jest.mock('@/lib/pendingSpend', () => ({ acquireSpendAttempt: jest.fn(), clearSpendAttempt: jest.fn() }));
jest.mock('@/lib/spendOutcome', () => ({ classifySpendResponse: jest.fn(), isRecoveredSpendResponse: jest.fn() }));
jest.mock('expo-router', () => ({ router: { back: jest.fn(), replace: jest.fn(), push: jest.fn() } }));
jest.mock('@/lib/wallet', () => ({ useWallet: () => ({ balance: 20000, reload: jest.fn() }) }));
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
  ConfirmSheet: () => null,
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

const textContent = (tree: renderer.ReactTestRenderer) =>
  tree.root.findAllByType(Text).map((node) => String(node.props.children)).join(' ');

describe('unsupported product screens', () => {
  it('does not present a fixed-save authorization when savings are unavailable', async () => {
    mockGetRates.mockResolvedValueOnce({
      success: true,
      product_available: false,
      unavailable_message: 'Savings are coming later.',
      rates: [],
    });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<FixedSave />); });
    await act(async () => { await Promise.resolve(); });

    expect(textContent(tree)).toContain('Savings are coming later.');
    expect(tree.root.findByProps({ accessibilityLabel: 'View existing saves' })).toBeTruthy();
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Continue' })).toHaveLength(0);
  });

  it('does not present a loan authorization when new loans are unavailable', async () => {
    mockGetLoanStatus.mockResolvedValueOnce({
      success: true,
      product_available: false,
      unavailable_message: 'Loans are coming later.',
      available: '0.00',
      quote_rate: '0.00',
      active_loan: null,
    });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<GetLoan />); });
    await act(async () => { await Promise.resolve(); });

    expect(textContent(tree)).toContain('Loans are coming later.');
    expect(tree.root.findByProps({ accessibilityLabel: 'View loans' })).toBeTruthy();
    expect(tree.root.findAll((node) => String(node.props.accessibilityLabel || '').startsWith('Get ₦'))).toHaveLength(0);
  });
});
