import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import Loans from '@/app/(homepage)/loan';
import Convert from '@/app/(homepage)/convert';

const mockLoanStatus = jest.fn();
const mockRate = jest.fn();
const mockCopy = jest.fn();
const mockNotify = jest.fn();
const mockPush = jest.fn();
const mockReload = jest.fn();
jest.mock('@/lib/services/loans', () => ({ loansService: { getStatus: () => mockLoanStatus(), repay: jest.fn() } }));
jest.mock('@/lib/services/bills', () => ({ convertService: { getRate: () => mockRate() } }));
jest.mock('@/lib/secureStore', () => ({ getToken: async () => 'session' }));
jest.mock('@/lib/pendingSpend', () => ({ acquireSpendAttempt: jest.fn(), clearSpendAttempt: jest.fn() }));
jest.mock('@/lib/wallet', () => ({ useWallet: () => ({ reload: mockReload }) }));
jest.mock('expo-router', () => ({
  router: { push: (...args: unknown[]) => mockPush(...args) },
  useFocusEffect: (callback: () => void) => jest.requireActual<typeof import('react')>('react').useEffect(callback, [callback]),
}));
jest.mock('expo-clipboard', () => ({ setStringAsync: (...args: unknown[]) => mockCopy(...args) }));
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: {} }), font: {} }));
jest.mock('@/components/design/widgets', () => ({ Hero: ({ children }: { children: ReactNode }) => children, SectionLabel: () => null }));
jest.mock('@/components/design/flowkit', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text, TextInput } = jest.requireActual<typeof import('react-native')>('react-native');
  return { Label: Text, QuickAmounts: () => null, AmountField: (props: any) => ReactActual.createElement(TextInput, { ...props, accessibilityLabel: props.label }) };
});
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View, Text, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  const Container = ({ children, ...props }: { children: ReactNode }) => ReactActual.createElement(View, props, children);
  return { Screen: Container, Card: Container, NText: Text, Sheet: () => null, PinPad: () => null,
    Btn: ({ label, ...props }: any) => ReactActual.createElement(Pressable, { ...props, accessibilityLabel: label }, ReactActual.createElement(Text, null, label)),
    money: (value: number) => `₦${value}` };
});

const mount = async (Page: React.ComponentType) => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Page />); });
  return tree;
};
beforeEach(() => { jest.clearAllMocks(); mockLoanStatus.mockReset(); mockRate.mockReset(); mockCopy.mockReset(); });

it('distinguishes failed loan status from product unavailability and retries into the real state', async () => {
  mockLoanStatus.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ success: true, product_available: false, repayment_available: false, active_loan: null });
  const tree = await mount(Loans);
  expect(JSON.stringify(tree.toJSON())).toContain('Loan availability could not be confirmed');
  expect(JSON.stringify(tree.toJSON())).not.toContain('New loans are unavailable');
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Retry loading loans' }).props.onPress(); });
  expect(mockLoanStatus).toHaveBeenCalledTimes(2);
  expect(JSON.stringify(tree.toJSON())).toContain('New loans are unavailable');
  expect(JSON.stringify(tree.toJSON())).toContain('No active loans');
  act(() => tree.unmount());
});

it('keeps unavailable loan repayment disabled and provides a support path', async () => {
  mockLoanStatus.mockResolvedValue({ success: true, product_available: false, repayment_available: false, active_loan: { reference: 'loan1', outstanding: '5000', due_date: '2026-10-30', tenure_days: 30 } });
  const tree = await mount(Loans);
  expect(tree.root.findByProps({ accessibilityLabel: 'Repayment unavailable' }).props.disabled).toBe(true);
  act(() => tree.root.findByProps({ accessibilityLabel: 'Contact support' }).props.onPress());
  expect(mockPush).toHaveBeenCalledWith('/support');
  act(() => tree.unmount());
});

it('retries exchange rates and keeps estimates visible if clipboard copying fails', async () => {
  mockRate.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ success: true, currencies: [{ code: 'USD', name: 'US Dollar', symbol: '$', rate: 0.001 }], updated: '' });
  const tree = await mount(Convert);
  expect(JSON.stringify(tree.toJSON())).toContain('Could not load rates');
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Retry loading exchange rates' }).props.onPress(); });
  act(() => tree.root.findByProps({ accessibilityLabel: 'Or enter amount' }).props.onChangeText('1000'));
  expect(JSON.stringify(tree.toJSON())).toContain('$1.00');
  expect(JSON.stringify(tree.toJSON())).toContain('No money is exchanged');
  mockCopy.mockRejectedValueOnce(new Error('clipboard unavailable'));
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Copy USD value' }).props.onPress(); });
  expect(mockNotify).toHaveBeenCalledWith('Could not copy', 'Please try again.');
  expect(tree.root.findByProps({ accessibilityLabel: 'Copy USD value' })).toBeTruthy();
  expect(JSON.stringify(tree.toJSON())).toContain('$1.00');
  act(() => tree.unmount());
});

it('does not display or copy infinite currency amounts', async () => {
  mockRate.mockResolvedValue({ success: true, currencies: [{ code: 'USD', name: 'US Dollar', symbol: '$', rate: 0.001 }] });
  const tree = await mount(Convert);
  act(() => tree.root.findByProps({ accessibilityLabel: 'Or enter amount' }).props.onChangeText('9'.repeat(400)));
  expect(tree.root.findByProps({ accessibilityLabel: 'Copy USD value' }).props.disabled).toBe(true);
  expect(JSON.stringify(tree.toJSON())).toContain('Enter a valid amount');
  expect(JSON.stringify(tree.toJSON())).not.toContain('$∞');
  expect(mockCopy).not.toHaveBeenCalled();
  act(() => tree.unmount());
});
