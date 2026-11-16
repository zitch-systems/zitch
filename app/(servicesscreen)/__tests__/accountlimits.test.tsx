import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import { router } from 'expo-router';
import { notify } from '@/components/design/Notify';
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
  const Sheet = ({ open, children }: { open: boolean; children: ReactNode }) => (open ? ReactActual.createElement(View, null, children) : null);
  return { Screen: Container, Card: Container, Sheet, NText: Text, Header: ({ title }: { title: string }) => ReactActual.createElement(Text, null, `header:${title}`), Progress: () => null, money: (value: number) => `₦${value}`,
    Btn: ({ label, onPress }: { label: string; onPress: () => void }) => ReactActual.createElement(Pressable, { accessibilityLabel: label, onPress }, ReactActual.createElement(Text, null, label)) };
});

const state = { success: true, tier: 1, transaction_limit: '50000', daily_transfer_limit: '50000',
  daily_bill_limit: '35000', bvn_verified: true, nin_verified: false, account_provider: 'partnership' };

beforeEach(() => {
  mockApiJson.mockReset();
  (router.push as jest.Mock).mockClear();
  (notify as jest.Mock).mockClear();
  mockWallet.accountNumber = '';
  mockWallet.accountName = '';
});

it.each([
  { provider: 'wema_vas', can_fund: true, available: true, has_account: true, account_number: '7121234567' },
  { provider: 'partnership', legacy_funding_review: true, account_setup_state: 'legacy_funding_review' },
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
      success: true, provider: 'partnership', legacy_funding_review: true, account_setup_state: 'legacy_funding_review',
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


it('never uses a reserved validation prefix even if funding flags claim the account is ready', async () => {
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

it('uses current Partnership account details and authoritative limits without inventing an unlimited balance', async () => {
  mockWallet.accountNumber = '0450000000';
  mockWallet.accountName = 'Stale Account';
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? {
    ...state, account_provider: 'partnership', daily_transfer_limit: '123456', daily_bill_limit: '65432',
    bank_tier_limits: { single_inflow: '77777', daily_spend: '222222', max_balance: null },
  } : { success: true, provider: 'partnership', account_number: '0454243073', account_name: 'Current Account', has_account: true, available: true, can_fund: true, account_setup_state: 'ready' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('045 424 3073');
  expect(output).not.toContain('045 000 0000');
  // Today's daily caps as two tiles, from the server; nothing invented.
  expect(output).toContain('₦123456');
  expect(output).toContain('₦65432');
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
  } : { success: true, provider: 'partnership', account_number: '0454243073',
    account_name: 'Current Account', has_account: true, available: true, can_fund: true,
    account_setup_state: 'ready' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  // The transfers tile cannot read `true` as money; the bill tile keeps a real zero.
  expect(output.match(/Not confirmed/g)).toHaveLength(1);
  expect(output.match(/₦0/g)).toHaveLength(1);
  expect(output).not.toMatch(/₦(true|false)/);
  act(() => tree.unmount());
});

const ladder = [
  { tier: 1, name: 'Verified', per_transfer: '50000', daily_transfer: '50000', daily_bill: '20000' },
  { tier: 2, name: 'Enhanced', per_transfer: '200000', daily_transfer: '1000000', daily_bill: '100000' },
  { tier: 3, name: 'Premium', per_transfer: '5000000', daily_transfer: '5000000', daily_bill: '500000' },
];

it('shows the tier ladder from the server and answers Verify Tier 3 with what the server says', async () => {
  mockApiJson.mockImplementation(async (path: string, body?: { tier?: number }) => path === '/api/kyc/status/'
    ? { ...state, tier: 2, address: '12 Allen Avenue, Ikeja, Lagos', tier_ladder: ladder }
    : path === '/api/kyc/claim/' ? (expect(body).toEqual({ tier: 3 }), { success: true, state: 'processing', tier: 2 })
    : { success: true, provider: 'partnership', account_number: '0454243073', account_name: 'Current Account', has_account: true, available: true, can_fund: true, account_setup_state: 'ready' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  let output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Your tier');
  expect(output).toContain('₦5000000');
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Verify Tier 3' }).props.onPress(); });
  output = JSON.stringify(tree.toJSON());
  // never claims an upgrade the server did not make
  expect(output).not.toContain('now Tier 3');
  expect(output).toContain('Tier 3 is processing');
  expect(output).toContain('12 Allen Avenue, Ikeja, Lagos');
  expect(mockApiJson.mock.calls.map(([p]) => p)).not.toContain('/api/kyc/address/');
  act(() => tree.unmount());
});

it('sends Verify Tier 3 to the address page when the bank did not confirm it, and to Tier 2 first when needed', async () => {
  let answer: Record<string, unknown> = { success: true, state: 'not_confirmed', tier: 2 };
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/'
    ? { ...state, tier: 2, tier_ladder: ladder } : path === '/api/kyc/claim/' ? answer : ready);
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Verify Tier 3' }).props.onPress(); });
  expect(router.push).toHaveBeenCalledWith('/tier3?from=limits');
  answer = { success: true, state: 'needs_tier2', tier: 2, message: 'Verify Tier 2 first. Tier 3 opens after it.' };
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Verify Tier 3' }).props.onPress(); });
  expect(notify).toHaveBeenLastCalledWith('Verify Tier 2 first', 'Verify Tier 2 first. Tier 3 opens after it.');
  act(() => tree.unmount());
});

const ready = { success: true, provider: 'partnership', account_number: '0454243073', account_name: 'Current Account', has_account: true, available: true, can_fund: true, account_setup_state: 'ready' };

it('titles the error state Account Limits', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state : { success: false });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  expect(JSON.stringify(tree.toJSON())).toContain('header:Account Limits');
  act(() => tree.unmount());
});

it('sends Verify Tier 2 to the Tier 2 page when the sign-up check was not confirmed', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? state
    : path === '/api/kyc/claim/' ? { success: true, state: 'not_confirmed', tier: 1 } : ready);
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Verify Tier 2' }).props.onPress(); });
  expect(router.push).toHaveBeenCalledWith('/tier2?from=limits&recheck=1');
  act(() => tree.unmount());
});

it('moves to Tier 2 at once when the bank already confirmed the sign-up check', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? { ...state, tier_ladder: ladder }
    : path === '/api/kyc/claim/' ? { success: true, state: 'upgraded', tier: 2 } : ready);
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Verify Tier 2' }).props.onPress(); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('You’re now Tier 2');
  expect(output).toContain('₦1000000');
  expect(router.push).not.toHaveBeenCalledWith(expect.stringContaining('/tier2'));
  act(() => tree.unmount());
});

it('shows Tier 2 as processing while the bank is still checking the sign-up check', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/' ? { ...state, tier_ladder: ladder }
    : path === '/api/kyc/claim/' ? { success: true, state: 'processing', tier: 1 } : ready);
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Verify Tier 2' }).props.onPress(); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Tier 2 is processing');
  expect(output).toContain('From your sign-up');
  expect(output).not.toContain('now Tier 2');
  // the row turns into a status that asks again when tapped
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Verify Tier 2' })).toHaveLength(0);
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Tier 2 processing. Check again' }).length).toBeGreaterThan(0);
  act(() => tree.unmount());
});

it('shows Tier 3 in progress as a status, not a button, and drops the Checking pill', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/'
    ? { ...state, tier: 2, nin_verified: true, address_verification_pending: true } : ready);
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Processing');
  expect(output).not.toContain('Checking');
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Verify Tier 3' })).toHaveLength(0);
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Tier 3 processing. Check again' }).length).toBeGreaterThan(0);
  act(() => tree.unmount());
});

it('shows the Tier 3 limits when the address check had already come through', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === '/api/kyc/status/'
    ? { ...state, tier: 2, nin_verified: true, address: '12 Allen Avenue, Ikeja, Lagos', tier_ladder: ladder }
    : path === '/api/kyc/claim/' ? { success: true, state: 'upgraded', tier: 3 } : ready);
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<AccountLimits />); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Verify Tier 3' }).props.onPress(); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('You’re now Tier 3');
  expect(output).toContain('Your address check had already come through, so your new limits apply right away.');
  expect(output.match(/₦5000000/g)?.length).toBeGreaterThanOrEqual(4);
  act(() => tree.unmount());
});
