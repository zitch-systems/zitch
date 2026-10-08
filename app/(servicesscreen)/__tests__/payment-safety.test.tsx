import React from 'react';
import renderer, { act } from 'react-test-renderer';
import BuyAirtime from '../buyairtime';
import BuyData from '../buydata';
import BuyCable from '../buycable';
import BuyElectricity from '../buyelectricity';
import Betting from '../betting';
import Exams from '../exams';
import Remita from '../remita';

const mockSubmit = jest.fn();
const mockAcquire = jest.fn();
const mockClear = jest.fn();
const mockWallet = { balance: 20000, phoneNumber: '+2348012345678', billPaymentsAvailable: true, reload: jest.fn() };
const mockResponse = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

jest.mock('expo-router', () => ({ router: { back: jest.fn(), push: jest.fn(), replace: jest.fn() }, useLocalSearchParams: () => ({}) }));
jest.mock('@/lib/wallet', () => ({ useWallet: () => mockWallet }));
jest.mock('@/lib/pendingSpend', () => ({ acquireSpendAttempt: (...args: unknown[]) => mockAcquire(...args), clearSpendAttempt: (...args: unknown[]) => mockClear(...args) }));
jest.mock('@/lib/api', () => ({
  apiPost: (path: string, body: unknown) => path.includes('validate')
    ? Promise.resolve(mockResponse({ success: true, customer_name: 'Verified customer' })) : mockSubmit(path, body),
  apiJson: (path: string, body: unknown) => path.includes('validate')
    ? Promise.resolve({ success: true, name: 'Verified payer', amount: 1000 }) : mockSubmit(path, body),
  publicPost: async (path: string) => mockResponse(path.includes('get_data_plans_price') ? { price: '1000' }
    : path.includes('get_data_plans') ? { data_plans: [{ plan_code: 'bundle-1', name: 'Data plan', price: 1000 }] }
      : path.includes('get_cable_plans_price') ? { cable_plans_price: '1000' }
        : path.includes('get_cable_plans') ? { cable_plans: [{ cable_plan_code: 'cable-1', name: 'TV plan', price: 1000 }] }
          : path.includes('betting') ? { platforms: [{ code: 'book', name: 'Book', color: '#000' }] }
            : { exams: [{ code: 'waec', name: 'WAEC', description: 'Result checker', price: '1000' }] }),
}));
jest.mock('@/lib/services/bills', () => ({
  bettingService: { fund: (...args: unknown[]) => mockSubmit('betting', args) },
  examsService: { buy: (...args: unknown[]) => mockSubmit('exam', args) },
}));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/Receipt', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: { ink1: '#111', ink2: '#222', ink3: '#333' } }), font: { regular: 'r', bold: 'b' } }));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual('react');
  const { View, Text, TextInput, Pressable } = jest.requireActual('react-native');
  return {
    Screen: ({ children }: any) => R.createElement(View, null, children), Header: () => null, HeaderLink: () => null,
    Field: ({ label, placeholder, value, onChangeText }: any) => R.createElement(TextInput, { accessibilityLabel: label || placeholder, value, onChangeText }),
    Btn: ({ label, ...props }: any) => R.createElement(Pressable, { ...props, accessibilityLabel: label }, R.createElement(Text, null, label)),
    Sheet: ({ children }: any) => R.createElement(View, null, children),
    PinPad: ({ onComplete }: any) => R.createElement(Pressable, { accessibilityLabel: 'Authorize', onPress: () => onComplete('123456') }),
    money: (value: number) => `₦${value}`, Naira: () => null,
  };
});
jest.mock('@/components/design/flowkit', () => {
  const R = jest.requireActual('react');
  const { View, TextInput, Pressable } = jest.requireActual('react-native');
  return {
    Label: () => null, ProviderGrid: () => null, Segmented: () => null, QuickAmounts: () => null, ConfirmSheet: () => null, BalanceHint: () => null, Monogram: () => null,
    PlanList: ({ plans, onPick }: any) => R.createElement(View, null, ...plans.map((plan: any) => R.createElement(Pressable, { key: plan.id, accessibilityLabel: plan.label, onPress: () => onPick(plan.id) }))),
    AmountField: ({ value, onChangeText }: any) => R.createElement(TextInput, { accessibilityLabel: 'Remita amount', value, onChangeText }),
  };
});

const control = (tree: renderer.ReactTestRenderer, label: string) => tree.root.findByProps({ accessibilityLabel: label });
const type = async (tree: renderer.ReactTestRenderer, label: string, value: string) => act(async () => { control(tree, label).props.onChangeText(value); });
const tap = async (tree: renderer.ReactTestRenderer, label: string) => act(async () => { await control(tree, label).props.onPress(); });
const cases: [string, React.ComponentType, (tree: renderer.ReactTestRenderer) => Promise<void>, boolean][] = [
  ['airtime', BuyAirtime, async (t) => { await type(t, 'Or enter amount', '1000'); }, true],
  ['data', BuyData, async (t) => { await tap(t, 'Data plan'); }, true],
  ['cable', BuyCable, async (t) => { await type(t, 'Smartcard / IUC number', '12345678'); await tap(t, 'Validate IUC'); await tap(t, 'TV plan'); }, true],
  ['electricity', BuyElectricity, async (t) => { await type(t, 'Meter number', '12345678'); await tap(t, 'Validate meter'); await type(t, 'Enter amount (min 500)', '1000'); }, true],
  ['betting', Betting, async (t) => { await type(t, 'User ID', '123456'); await type(t, 'Enter amount', '1000'); }, false],
  ['exams', Exams, async () => {}, false],
  ['remita', Remita, async (t) => { await type(t, 'Enter the RRR on your bill', '123456789012'); await tap(t, 'Verify RRR'); }, false],
];

beforeEach(() => {
  mockSubmit.mockReset(); mockAcquire.mockReset().mockResolvedValue('durable-attempt'); mockClear.mockReset();
  mockWallet.billPaymentsAvailable = true; mockWallet.balance = 20000; mockWallet.phoneNumber = '+2348012345678';
});

describe.each(cases)('%s payment authorization', (_name, Component, prepare, rawResponse) => {
  it('submits at most once for simultaneous PIN callbacks and retains unresolved keys', async () => {
    let finish!: (value: unknown) => void;
    mockSubmit.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Component />); });
    await prepare(tree);
    await act(async () => {
      const authorize = control(tree, 'Authorize').props.onPress;
      void authorize(); void authorize();
      await Promise.resolve();
    });
    expect(mockSubmit).toHaveBeenCalledTimes(1);
    expect(mockAcquire).toHaveBeenCalledTimes(1);
    await act(async () => { finish(rawResponse ? mockResponse({ pending: true }) : { pending: true }); });
    expect(mockClear).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });

  it('fails closed when the individual bill-payment capability is disabled', async () => {
    mockWallet.billPaymentsAvailable = false;
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Component />); });
    await prepare(tree);
    await tap(tree, 'Authorize');
    expect(mockAcquire).not.toHaveBeenCalled();
    expect(mockSubmit).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });
});

it('defaults airtime to the customer phone, preserves edits and rejects incomplete numbers', async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<BuyAirtime />); });
  expect(control(tree, 'Phone number').props.value).toBe('08012345678');
  await type(tree, 'Or enter amount', '1000');
  await type(tree, 'Phone number', '0801234567');
  await tap(tree, 'Authorize');
  expect(mockSubmit).not.toHaveBeenCalled();
  expect(control(tree, 'Phone number').props.value).toBe('0801234567');
  act(() => tree.unmount());
});

it.each([['data', BuyData, 'Data plan'], ['cable', BuyCable, 'TV plan']] as const)('binds %s payment to the displayed authoritative price', async (name, Component, plan) => {
  mockSubmit.mockResolvedValue(mockResponse({ success: false, code: 'price_changed', current_price: '1200' }, 409));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Component />); });
  if (name === 'cable') { await type(tree, 'Smartcard / IUC number', '12345678'); await tap(tree, 'Validate IUC'); }
  await tap(tree, plan);
  await tap(tree, 'Authorize');
  expect(mockSubmit).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ expected_amount: '1000', idempotency_key: 'durable-attempt' }));
  expect(mockClear).toHaveBeenCalledTimes(1);
  act(() => tree.unmount());
});
