import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import Home from '@/app/(homepage)/home';
import Wallet from '@/app/(homepage)/wallet';

const mockWallet = {
  balance: 0, totalBalance: 1200, historicalBalance: 1200, fundingProvider: 'partnership',
  firstName: 'Test', fullName: 'Test Customer', avatar: '', accountNumber: '0454243073', bankName: 'Wema Bank',
  billPaymentsAvailable: false, transfersAvailable: false,
  fundingMessage: 'Your account balance needs review before you can spend.',
  txns: [], showBal: true, setShowBal: jest.fn(), reload: jest.fn(), linked: [], reloadLinked: jest.fn(),
  hydrated: true, balanceLoaded: true, balanceError: '', historyError: '', loading: false,
};

jest.mock('@/lib/wallet', () => ({ useWallet: () => mockWallet, transactionParams: jest.fn() }));
jest.mock('@/lib/api', () => ({ apiJson: jest.fn() }));
jest.mock('expo-router', () => ({
  router: { push: jest.fn() },
  useFocusEffect: (callback: () => void) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    ReactActual.useEffect(callback, [callback]);
  },
}));
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn() }));
jest.mock('expo-linear-gradient', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return { LinearGradient: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children) };
});
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Brand', () => ({ Avatar: () => null }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/SmartPaste', () => () => null);
jest.mock('@/components/design/ConnectedAccounts', () => ({ ConnectedAccounts: () => null }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#0FA295', brandDeep: '#08766d', ink1: '#111', ink2: '#222', ink3: '#333', line: '#ddd', surface: '#fff' } }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold', medium: 'medium' },
}));
jest.mock('@/components/design/widgets', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return { Hero: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children), SectionLabel: () => null, ServiceTile: () => null };
});
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text, View } = jest.requireActual<typeof import('react-native')>('react-native');
  const Container = ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children);
  return { Screen: Container, Card: Container, NText: Text, Sheet: () => null, TxnRow: () => null,
    money: (value: number) => `₦${value}`, settledTransactionTotal: () => 0 };
});

beforeEach(() => { mockWallet.showBal = true; });

it.each([['Home', Home], ['Wallet', Wallet]] as const)('%s preserves the total while Partnership funds cannot be spent', async (_name, Page) => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Page />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Total balance: ');
  expect(output).toContain('₦1200');
  expect(output).toContain('₦0');
  expect(output).toContain('Funds under review');
  expect(output).toContain('Your account balance needs review before you can spend.');
  expect(output).not.toContain('Historical funds');
  act(() => tree.unmount());
});

it.each([['Home', Home], ['Wallet', Wallet]] as const)('%s respects balance privacy for held Partnership funds', async (_name, Page) => {
  mockWallet.showBal = false;
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Page />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Funds under review');
  expect(output).not.toContain('₦1200');
  act(() => tree.unmount());
});
