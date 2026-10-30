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
  daily_bill_limit: '35000', bvn_verified: true, nin_verified: false, account_provider: '9psb' };

beforeEach(() => {
  mockApiJson.mockReset();
  mockWallet.accountNumber = '';
  mockWallet.accountName = '';
});

it.each([
  { provider: 'wema_vas', can_fund: true, available: true, has_account: true, account_number: '7121234567' },
  { provider: '9psb', legacy_funding_review: true, account_setup_state: 'legacy_funding_review' },
])('does not advertise held or retired funding or spendable limits: %j', async (funding) => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state : {
    success: true, ...funding, bill_payments_available: true, transfers_available: true,
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Account review');
  expect(output).toContain('Transfers and bill payments are currently unavailable.');
  expect(output).not.toContain('712 123 4567');
  expect(output).not.toContain('₦50000');
  expect(output).not.toContain('Per-transaction limit');
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
  act(() => tree.unmount());
});

it('keeps account details unavailable when current funding capability cannot be fetched', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state : { success: false });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain("Couldn't load account details");
  expect(output).not.toContain('₦50000');
  act(() => tree.unmount());
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
      success: true, provider: '9psb', legacy_funding_review: true, account_setup_state: 'legacy_funding_review',
    });
    await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Try again' }).props.onPress(); });
    await act(async () => { resolveOld.forEach((resolve) => resolve({ success: false })); });
    expect(JSON.stringify(tree.toJSON())).toContain('Account review');
    expect(JSON.stringify(tree.toJSON())).not.toContain('taking longer than expected');
  } finally {
    act(() => tree.unmount());
    jest.useRealTimers();
  }
});


it('never uses a retired funding provider even if its flags claim the account is ready', async () => {
  mockWallet.accountNumber = '7111234567';
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state : {
    success: true, provider: 'partnership', available: true, has_account: true, can_fund: true,
    account_setup_state: 'ready', account_number: '7111234567', account_name: 'Validation Account',
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  expect(JSON.stringify(tree.toJSON())).not.toContain('711 123 4567');
  expect(JSON.stringify(tree.toJSON())).not.toContain('VALIDATION ACCOUNT');
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
  act(() => tree.unmount());
});

it('uses current 9PSB account details and authoritative limits without inventing an unlimited balance', async () => {
  mockWallet.accountNumber = '0450000000';
  mockWallet.accountName = 'Stale Account';
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? {
    ...state, account_provider: '9psb', daily_transfer_limit: '123456', daily_bill_limit: '65432',
    bank_tier_limits: { single_inflow: '77777', daily_spend: '222222', max_balance: null },
  } : { success: true, provider: '9psb', account_number: '0454243073', account_name: 'Current Account', has_account: true, available: true, can_fund: true, account_setup_state: 'ready' });
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

it('does not expose a stale cached 9PSB number during account review', async () => {
  mockWallet.accountNumber = '0450000000';
  mockWallet.accountName = 'Stale Account';
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? {
    ...state, account_provider: '9psb',
  } : { success: true, provider: '9psb', account_number: '', available: false, has_account: false,
    legacy_funding_review: true, account_setup_state: 'legacy_funding_review', migration_message: 'Your account needs review.' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  expect(JSON.stringify(tree.toJSON())).not.toContain('045 000 0000');
  expect(JSON.stringify(tree.toJSON())).toContain('Your account needs review.');
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
  act(() => tree.unmount());
});

it('does not turn boolean limit values into money while retaining a confirmed zero', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? {
    ...state, transaction_limit: false, daily_transfer_limit: true, daily_bill_limit: '0.00',
    bank_tier_limits: { single_inflow: false, daily_spend: true, max_balance: 0 },
  } : { success: true, provider: '9psb', account_number: '0454243073',
    account_name: 'Current Account', has_account: true, available: true, can_fund: true,
    account_setup_state: 'ready' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output.match(/Not confirmed/g)).toHaveLength(4);
  expect(output.match(/₦0/g)).toHaveLength(2);
  expect(output).not.toContain('₦1');
  act(() => tree.unmount());
});
