import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import AccountLimits from '@/app/(servicesscreen)/accountlimits';

const mockApiJson = jest.fn();
const mockWallet = { accountNumber: '', accountName: '' };
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('@/lib/wallet', () => ({ useWallet: () => mockWallet }));
jest.mock('expo-router', () => ({
  router: { back: jest.fn(), push: jest.fn() },
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
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#0FA295', ink1: '#111', ink2: '#222', ink3: '#333', line: '#ddd', surface: '#fff' }, theme: 'light' }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text, View } = jest.requireActual<typeof import('react-native')>('react-native');
  const Container = ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children);
  return { Screen: Container, Card: Container, NText: Text, Header: () => null, Progress: () => null, money: (value: number) => `₦${value}` };
});

const state = { success: true, tier: 1, transaction_limit: '50000', daily_transfer_limit: '50000',
  daily_bill_limit: '35000', bvn_verified: true, nin_verified: false, account_provider: 'wema_vas' };

beforeEach(() => {
  mockApiJson.mockReset();
  mockWallet.accountNumber = '';
  mockWallet.accountName = '';
});

it('does not promise spendable VAS limits or a legacy tier ladder during testing', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state : {
    success: true, provider: 'wema_vas', test_mode: true, bill_payments_available: false, transfers_available: false,
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Account activation pending');
  expect(output).toContain('Transfers and bill payments are currently unavailable.');
  expect(output).not.toMatch(/testing|sample account|test account/i);
  expect(output).not.toContain('Level Benefit');
  expect(output).not.toContain('₦50000');
  expect(output).not.toContain('Unlimited');
  await act(async () => tree.unmount());
});

it('shows enabled VAS payment capabilities without incidental transaction limits', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state : {
    success: true, provider: 'wema_vas', test_mode: false, bill_payments_available: true, transfers_available: false,
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).not.toContain('Per-transaction limit');
  expect(output).not.toContain('Current daily limits');
  expect(output).not.toContain('₦50000');
  expect(output).not.toContain('₦35000');
  expect(output).toContain('Transfers are currently unavailable.');
  expect(output).not.toContain('Level Benefit');
  expect(output).not.toContain('Unlimited');
  await act(async () => tree.unmount());
});

it('keeps VAS limits unavailable when funding capability cannot be fetched', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state : { success: false });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Account activation pending');
  expect(output).not.toContain('Level Benefit');
  expect(output).not.toContain('₦50000');
  await act(async () => tree.unmount());
});


it('does not expose an old cached number or a validation number as the VAS funding account', async () => {
  mockWallet.accountNumber = '7111234567';
  mockWallet.accountName = 'Zitch/Old Name';
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state : {
    success: true, provider: 'wema_vas', test_mode: false, available: true, has_account: true,
    account_setup_state: 'ready', account_number: '7111234567', account_name: 'Zitch/Sample',
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).not.toContain('711 123 4567');
  expect(output).not.toContain('ZITCH/OLD NAME');
  expect(output).not.toContain('ZITCH/SAMPLE');
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
  await act(async () => tree.unmount());
});

it('uses the current ready VAS account for display and preserves available services', async () => {
  mockWallet.accountNumber = '7111234567';
  mockWallet.accountName = 'Zitch/Old Name';
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state : {
    success: true, provider: 'wema_vas', test_mode: false, available: true, has_account: true,
    account_setup_state: 'ready', account_number: '7121234567', account_name: 'Zitch/Current',
    bill_payments_available: true, transfers_available: true,
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('712 123 4567');
  expect(output).toContain('ZITCH/CURRENT');
  expect(output).not.toContain('711 123 4567');
  expect(output).toContain('Bill payments and transfers are available.');
  expect(output).not.toContain('₦50000');
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' }).length).toBeGreaterThan(0);
  await act(async () => tree.unmount());
});
