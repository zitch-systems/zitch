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
  const { Text, View, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  const Container = ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children);
  return { Screen: Container, Card: Container, NText: Text, Header: () => null, Progress: () => null, money: (value: number) => `₦${value}`,
    Btn: ({ label, onPress }: { label: string; onPress: () => void }) => ReactActual.createElement(Pressable, { accessibilityLabel: label, onPress }, ReactActual.createElement(Text, null, label)) };
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
    success: true, provider: 'wema_vas', test_mode: true, account_setup_state: 'vas_validation', bill_payments_available: false, transfers_available: false,
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
  expect(output).toContain("Couldn't load account details");
  expect(output).not.toContain('Account activation pending');
  expect(output).not.toContain('Level Benefit');
  expect(output).not.toContain('₦50000');
  await act(async () => tree.unmount());
});

it('shows the real verification step before an account has been allocated', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state : {
    success: true, provider: 'wema_vas', test_mode: true, account_setup_state: 'vas_enrollment_required',
    enrollment_status: 'verification_required', enrollment_message: 'Confirm ownership to finish your account setup.',
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  expect(JSON.stringify(tree.toJSON())).toContain('Verify your identity');
  expect(JSON.stringify(tree.toJSON())).toContain('Confirm ownership to finish your account setup.');
  expect(JSON.stringify(tree.toJSON())).not.toContain('Account activation pending');
  await act(async () => tree.unmount());
});

it('ends a stalled fetch and lets the customer retry without stale responses replacing the result', async () => {
  jest.useFakeTimers();
  const resolveOld: ((value: unknown) => void)[] = [];
  mockApiJson.mockImplementation(() => new Promise((resolve) => { resolveOld.push(resolve); }));
  let tree!: renderer.ReactTestRenderer;
  try {
    await act(async () => { tree = renderer.create(<AccountLimits />); });
    await act(async () => { jest.advanceTimersByTime(8000); });
    expect(JSON.stringify(tree.toJSON())).toContain('taking longer than expected');
    mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state : {
      success: true, provider: 'wema_vas', account_setup_state: 'vas_validation',
    });
    await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Try again' }).props.onPress(); });
    await act(async () => { resolveOld.forEach((resolve) => resolve({ success: false })); });
    expect(JSON.stringify(tree.toJSON())).toContain('Account activation pending');
    expect(JSON.stringify(tree.toJSON())).not.toContain('taking longer than expected');
  } finally {
    act(() => tree.unmount());
    jest.useRealTimers();
  }
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

it('uses current Partnership account details and authoritative limits without inventing an unlimited balance', async () => {
  mockWallet.accountNumber = '0450000000';
  mockWallet.accountName = 'Stale Account';
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? {
    ...state, account_provider: 'partnership', daily_transfer_limit: '123456', daily_bill_limit: '65432',
    bank_tier_limits: { single_inflow: '77777', daily_spend: '222222', max_balance: null },
  } : { success: true, provider: 'partnership', account_number: '0454243073', account_name: 'Current Account', has_account: true, available: true });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('045 424 3073');
  expect(output).not.toContain('045 000 0000');
  expect(output).toContain('₦123456');
  expect(output).toContain('₦65432');
  expect(output).toContain('₦77777');
  expect(output).toContain('₦222222');
  expect(output).toContain('Not confirmed');
  expect(output).not.toContain('Unlimited');
  expect(output).not.toContain('Level Benefit');
  act(() => tree.unmount());
});

it('does not expose a stale cached Partnership number during account review', async () => {
  mockWallet.accountNumber = '0450000000';
  mockWallet.accountName = 'Stale Account';
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? {
    ...state, account_provider: 'partnership',
  } : { success: true, provider: 'partnership', account_number: '', available: false, has_account: false,
    account_setup_state: 'partnership_review', migration_message: 'Your account needs review.' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  expect(JSON.stringify(tree.toJSON())).not.toContain('045 000 0000');
  expect(JSON.stringify(tree.toJSON())).toContain('Your account needs review.');
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
  act(() => tree.unmount());
});
