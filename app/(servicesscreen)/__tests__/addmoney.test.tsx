import React from 'react';
import renderer, { act } from 'react-test-renderer';
import AddMoney from '../addmoney';

let mockGeneration = 1;
let mockAuthenticated = true;
const mockStatus = jest.fn();
const mockCopy = jest.fn();
const mockPush = jest.fn();
jest.mock('@/components/AuthGuard', () => ({ children }: any) => mockAuthenticated ? children : null);
jest.mock('@/lib/services/banking', () => ({ banking: { status: () => mockStatus() } }));
jest.mock('@/lib/api', () => ({ apiJson: jest.fn() }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockGeneration }));
jest.mock('expo-clipboard', () => ({ setStringAsync: (...args: any[]) => mockCopy(...args) }));
jest.mock('expo-router', () => ({ router: { push: (...args: any[]) => mockPush(...args) } }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: { ink1: '#111' } }), font: { regular: 'regular' } }));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual('react');
  const { View, Text, Pressable } = jest.requireActual('react-native');
  return { Screen: ({ children }: any) => ReactActual.createElement(View, null, children), Header: () => null,
    Btn: ({ label, ...props }: any) => ReactActual.createElement(Pressable, { accessibilityLabel: label, ...props }, ReactActual.createElement(Text, null, label)) };
});

const ready = { success: true, provider: '9psb', can_fund: true, available: true, has_account: true,
  account_setup_state: 'ready', account_number: '1100000001', account_name: 'Ada Eze', bank_tier: 1,
  maximum_single_deposit: '50000', maximum_balance: '300000' };
let tree: renderer.ReactTestRenderer;
const output = () => JSON.stringify(tree.toJSON());
const button = (label: string) => tree.root.findByProps({ accessibilityLabel: label });
async function mount() { await act(async () => { tree = renderer.create(<AddMoney />); }); }
beforeEach(() => { jest.clearAllMocks(); mockGeneration = 1; mockAuthenticated = true; mockStatus.mockResolvedValue(ready); mockCopy.mockResolvedValue(undefined); });
afterEach(async () => { if (tree) await act(async () => tree.unmount()); });

it('shows only the confirmed account and exact bank deposit limits', async () => {
  await mount();
  expect(output()).toContain('1100000001');
  expect(output()).toContain('50,000');
  expect(output()).toContain('300,000');
  expect(output()).not.toMatch(/unlimited|instantly/i);
  await act(async () => { await button('Copy account number').props.onPress(); });
  expect(mockCopy).toHaveBeenCalledWith('1100000001');
});

it.each([{ provider: 'partnership' }, { provider: undefined }, { can_fund: false }, { can_fund: undefined },
  { available: false }, { has_account: false }, { account_setup_state: 'opening' },
  { legacy_funding_review: true }, { account_number: '123' }])('hides an unconfirmed funding destination: %j', async (override) => {
  mockStatus.mockResolvedValue({ ...ready, ...override }); await mount();
  expect(output()).not.toContain('Copy account number');
  expect(output()).not.toContain('1100000001');
});

it('does not invent account setup while the server status is unknown', async () => {
  mockStatus.mockResolvedValue({ success: false, message: 'Temporarily unavailable' }); await mount();
  expect(output()).toContain('Temporarily unavailable');
  expect(output()).not.toContain('Set up my account');
});

it('handles a connection failure and allows a successful retry', async () => {
  mockStatus.mockRejectedValueOnce(new Error('offline')); await mount();
  expect(output()).toContain('Could not load your account');
  await act(async () => { await button('Refresh account').props.onPress(); });
  expect(output()).toContain('1100000001');
});

it('does not fetch customer information while signed out', async () => {
  mockAuthenticated = false; await mount(); expect(mockStatus).not.toHaveBeenCalled();
});

it('ignores an account response completed after session rotation', async () => {
  let finish!: (value: unknown) => void;
  mockStatus.mockReturnValue(new Promise((resolve) => { finish = resolve; })); await mount();
  mockGeneration += 1;
  await act(async () => { finish(ready); });
  expect(output()).not.toContain('1100000001');
});

it('keeps a funding account available when only outgoing spending is held', async () => {
  mockStatus.mockResolvedValue({ ...ready, spending_available: false, pnd: true }); await mount();
  expect(output()).toContain('1100000001');
});

it('does not promise an unlimited balance when no bank limit is confirmed', async () => {
  mockStatus.mockResolvedValue({ ...ready, maximum_balance: null }); await mount();
  expect(output()).not.toContain('Maximum balance:'); expect(output()).not.toMatch(/unlimited/i);
});

it('handles clipboard failure without an unhandled rejection', async () => {
  mockCopy.mockRejectedValue(new Error('unavailable')); await mount();
  await act(async () => { await button('Copy account number').props.onPress(); });
  expect(output()).toContain('Could not copy your account number');
});
