import React from 'react';
import renderer, { act } from 'react-test-renderer';
import BuyAirtime from '../buyairtime';
import BuyData from '../buydata';
import BuyCable from '../buycable';
import BuyElectricity from '../buyelectricity';
import Betting from '../betting';
import Exams from '../exams';
import Remita from '../remita';

let mockGeneration = 0;
let mockShowAllSheets = true;
const mockNotify = jest.fn();
const mockReceipt = jest.fn();
const mockSubmit = jest.fn();
const mockAcquire = jest.fn();
const mockClear = jest.fn();
const mockWallet = { balance: 20000, phoneNumber: '+2348012345678', billPaymentsAvailable: true, reload: jest.fn() };
const mockResponse = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

jest.mock('expo-router', () => ({ router: { back: jest.fn(), push: jest.fn(), replace: jest.fn() }, useLocalSearchParams: () => ({}) }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockGeneration }));
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
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/components/design/Receipt', () => ({ __esModule: true, default: (props: any) => { mockReceipt(props); return props.footer || null; } }));
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
    Sheet: ({ open, children }: any) => mockShowAllSheets || open ? R.createElement(View, null, children) : null,
    PinPad: ({ onComplete }: any) => R.createElement(Pressable, { accessibilityLabel: 'Authorize', onPress: () => onComplete('123456') }),
    money: (value: number) => `₦${value}`, Naira: () => null,
  };
});
jest.mock('@/components/design/flowkit', () => {
  const R = jest.requireActual('react');
  const { View, TextInput, Pressable } = jest.requireActual('react-native');
  return {
    Label: () => null, ProviderGrid: () => null, Segmented: () => null, QuickAmounts: () => null, ConfirmSheet: ({ open, onPay }: any) => open ? R.createElement(Pressable, { accessibilityLabel: 'Confirm payment', onPress: onPay }) : null, BalanceHint: () => null, Monogram: () => null,
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
  mockGeneration = 0; mockShowAllSheets = true; mockReceipt.mockClear(); mockNotify.mockClear(); mockWallet.reload.mockClear();
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

describe.each(cases)('%s account session boundaries', (_name, Component, prepare, rawResponse) => {
  it('does not deliver a request after the account changes while acquiring its retry key', async () => {
    let finish!: (value: string) => void;
    mockAcquire.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Component />); });
    await prepare(tree);
    await act(async () => { void control(tree, 'Authorize').props.onPress(); });
    mockGeneration += 1;
    await act(async () => { finish('original-account-key'); });
    expect(mockSubmit).not.toHaveBeenCalled();
    expect(mockClear).not.toHaveBeenCalled();
    expect(mockReceipt).not.toHaveBeenCalled();
    expect(mockNotify).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });

  it.each(['resolve', 'reject'] as const)('ignores a late %s after an account switch without clearing retry history', async (completion) => {
    let finish!: (value: unknown) => void;
    let fail!: (error: Error) => void;
    mockSubmit.mockReturnValue(new Promise((resolve, reject) => { finish = resolve; fail = reject; }));
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Component />); });
    await prepare(tree);
    await act(async () => { void control(tree, 'Authorize').props.onPress(); });
    expect(mockSubmit).toHaveBeenCalledTimes(1);
    mockGeneration += 1;
    await act(async () => {
      if (completion === 'resolve') finish(rawResponse ? mockResponse({ success: true }) : { success: true });
      else fail(new Error('Your session changed'));
    });
    expect(mockClear).not.toHaveBeenCalled();
    expect(mockReceipt).not.toHaveBeenCalled();
    expect(mockWallet.reload).not.toHaveBeenCalled();
    expect(mockNotify).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });

  it('does not repaint a previous account receipt if the session changes during marker cleanup', async () => {
    mockSubmit.mockResolvedValue(rawResponse ? mockResponse({ success: true }) : { success: true });
    mockClear.mockImplementation(async () => { mockGeneration += 1; });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Component />); });
    await prepare(tree);
    await tap(tree, 'Authorize');
    expect(mockClear).toHaveBeenCalledTimes(1);
    expect(mockReceipt).not.toHaveBeenCalled();
    expect(mockWallet.reload).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });

  it('requires review before PIN and routes an unresolved payment to History', async () => {
    mockShowAllSheets = false;
    mockSubmit.mockResolvedValue(rawResponse ? mockResponse({ pending: true, reference: 'pending-ref' }) : { pending: true, reference: 'pending-ref' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Component />); });
    await prepare(tree);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Authorize' })).toHaveLength(0);
    const continueButton = tree.root.findAll((node) => typeof node.props.accessibilityLabel === 'string' && /^(Continue|Pay)/.test(node.props.accessibilityLabel))[0];
    expect(continueButton.props.disabled).toBe(false);
    await act(async () => { continueButton.props.onPress(); });
    expect(mockSubmit).not.toHaveBeenCalled();
    jest.useFakeTimers();
    await tap(tree, 'Confirm payment');
    await act(async () => { jest.advanceTimersByTime(320); });
    jest.useRealTimers();
    await tap(tree, 'Authorize');
    expect(mockReceipt).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'Processing', reference: 'pending-ref' }));
    expect(mockClear).not.toHaveBeenCalled();
    expect(control(tree, 'Check transaction history')).toBeTruthy();
    act(() => tree.unmount());
  });
});

it.each([
  [BuyAirtime, 'Or enter amount'], [BuyElectricity, 'Enter amount (min 500)'], [Betting, 'Enter amount'],
] as const)('preserves pasted kobo and rejects ambiguous edits in %s', async (Component, label) => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Component />); });
  await type(tree, label, '1,000.50');
  expect(control(tree, label).props.value).toBe('1000.50');
  await type(tree, label, '1e3');
  expect(control(tree, label).props.value).toBe('1000.50');
  act(() => tree.unmount());
});

it.each([
  [BuyAirtime, 'Or enter amount', '2000', async (_tree: renderer.ReactTestRenderer) => {}],
  [Betting, 'Enter amount', '2000', async (tree: renderer.ReactTestRenderer) => { await type(tree, 'User ID', '123456'); }],
] as const)('requires another review if the amount changes during the review-to-PIN transition', async (Component, label, changedAmount, prepare) => {
  mockShowAllSheets = false;
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Component />); });
  await prepare(tree);
  await type(tree, label, '1000');
  const continueButton = tree.root.findAll((node) => typeof node.props.accessibilityLabel === 'string' && node.props.accessibilityLabel.startsWith('Continue'))[0];
  await act(async () => { continueButton.props.onPress(); });
  jest.useFakeTimers();
  await tap(tree, 'Confirm payment');
  await type(tree, label, changedAmount);
  await act(async () => { jest.advanceTimersByTime(320); });
  jest.useRealTimers();
  await tap(tree, 'Authorize');
  expect(mockAcquire).not.toHaveBeenCalled();
  expect(mockSubmit).not.toHaveBeenCalled();
  expect(mockNotify).toHaveBeenCalledWith('Review payment again', expect.any(String));
  expect(control(tree, 'Confirm payment')).toBeTruthy();
  act(() => tree.unmount());
});
