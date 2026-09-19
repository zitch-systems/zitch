import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import BuyCable from '@/app/(servicesscreen)/buycable';

const mockFetch = jest.fn();
const mockApiPost = jest.fn();

jest.mock('@/lib/secureStore', () => ({ getToken: jest.fn(async () => null) }));
jest.mock('@/lib/api', () => ({ apiPost: (...args: unknown[]) => mockApiPost(...args) }));
jest.mock('@/lib/pendingSpend', () => ({ acquireSpendAttempt: jest.fn(), clearSpendAttempt: jest.fn() }));
jest.mock('@/lib/spendOutcome', () => ({
  classifySpendResponse: jest.fn(),
  isRecoveredSpendResponse: jest.fn(),
}));
jest.mock('expo-router', () => ({ router: { back: jest.fn(), replace: jest.fn() } }));
jest.mock('@/lib/wallet', () => ({ useWallet: () => ({ balance: 20000, reload: jest.fn() }) }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Receipt', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brandDeep: '#08766d', ink1: '#111', ink2: '#222', ink3: '#333' } }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const {
    Pressable, Text: NativeText, TextInput, View,
  } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Header: () => null,
    Field: ({ label, placeholder, value, onChangeText }: {
      label?: string; placeholder?: string; value: string; onChangeText?: (value: string) => void;
    }) => ReactActual.createElement(TextInput, {
      accessibilityLabel: label || placeholder,
      value,
      onChangeText,
    }),
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) =>
      ReactActual.createElement(
        Pressable,
        { accessibilityLabel: label, onPress, disabled },
        ReactActual.createElement(NativeText, null, label),
      ),
    Sheet: ({ open, children }: { open: boolean; children: ReactNode }) => open
      ? ReactActual.createElement(View, null, children)
      : null,
    PinPad: () => null,
    money: (amount: number) => `₦${amount.toLocaleString()}`,
  };
});
jest.mock('@/components/design/flowkit', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text: NativeText, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Label: ({ children }: { children: ReactNode }) => ReactActual.createElement(NativeText, null, children),
    ProviderGrid: ({ items, onPick }: { items: { id: string; name: string }[]; onPick: (id: string) => void }) =>
      ReactActual.createElement(View, null, ...items.map((item) => ReactActual.createElement(
        Pressable,
        { key: item.id, accessibilityLabel: item.name, onPress: () => onPick(item.id) },
        ReactActual.createElement(NativeText, null, item.name),
      ))),
    PlanList: ({ plans, onPick }: { plans: { id: string; label: string }[]; onPick: (id: string) => void }) =>
      ReactActual.createElement(View, null, ...plans.map((plan) => ReactActual.createElement(
        Pressable,
        { key: plan.id, accessibilityLabel: plan.label, onPress: () => onPick(plan.id) },
        ReactActual.createElement(NativeText, null, plan.label),
      ))),
    ConfirmSheet: () => null,
    BalanceHint: () => null,
  };
});

const control = (tree: renderer.ReactTestRenderer, label: string) =>
  tree.root.findByProps({ accessibilityLabel: label });

describe('BuyCable response binding', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    mockFetch.mockReset();
    mockApiPost.mockReset();
    global.fetch = mockFetch as unknown as typeof fetch;
  });

  afterAll(() => { global.fetch = originalFetch; });

  it('keeps catalogues, prices and IUC validation bound to current inputs', async () => {
    let resolveGotvPlans!: (value: unknown) => void;
    let resolveFirstPrice!: (value: unknown) => void;
    let resolveOldIuc!: (value: unknown) => void;
    const gotvPlans = new Promise((resolve) => { resolveGotvPlans = resolve; });
    const firstPrice = new Promise((resolve) => { resolveFirstPrice = resolve; });
    const oldIuc = new Promise((resolve) => { resolveOldIuc = resolve; });

    mockFetch.mockImplementation((_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body || '{}'));
      if (body.cablenetwork === '1') return gotvPlans;
      if (body.cablenetwork === '2') {
        return Promise.resolve({
          json: async () => ({
            cable_plans: [
              { cable_plan_code: 'dstv-one', name: 'DStv One', price: '1000' },
              { cable_plan_code: 'dstv-two', name: 'DStv Two', price: '2000' },
            ],
          }),
        });
      }
      if (body.cable_plan_code === 'dstv-one') return firstPrice;
      if (body.cable_plan_code === 'dstv-two') {
        return Promise.resolve({ json: async () => ({ cable_plans_price: '2200' }) });
      }
      throw new Error(`Unexpected request ${String(init?.body)}`);
    });
    mockApiPost
      .mockReturnValueOnce(oldIuc)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ customer_name: 'Current Subscriber' }) });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<BuyCable />); });
    await act(async () => { control(tree, 'DSTV').props.onPress(); });
    await act(async () => { await Promise.resolve(); });

    expect(control(tree, 'DStv One')).toBeTruthy();
    await act(async () => {
      resolveGotvPlans({ json: async () => ({
        cable_plans: [{ cable_plan_code: 'gotv-stale', name: 'Stale GoTV Plan', price: '900' }],
      }) });
      await Promise.resolve();
    });
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Stale GoTV Plan' })).toHaveLength(0);

    await act(async () => {
      control(tree, 'Smartcard / IUC number').props.onChangeText('11111111');
    });
    let oldValidation!: Promise<void>;
    await act(async () => {
      oldValidation = control(tree, 'Validate IUC').props.onPress();
      await Promise.resolve();
    });
    await act(async () => {
      control(tree, 'Smartcard / IUC number').props.onChangeText('22222222');
    });
    await act(async () => {
      resolveOldIuc({ ok: true, json: async () => ({ customer_name: 'Stale Subscriber' }) });
      await oldValidation;
    });

    expect(control(tree, 'Continue').props.disabled).toBe(true);
    expect(control(tree, 'Validate IUC')).toBeTruthy();

    await act(async () => { await control(tree, 'Validate IUC').props.onPress(); });

    await act(async () => {
      control(tree, 'DStv One').props.onPress();
      await Promise.resolve();
    });
    expect(control(tree, 'Continue').props.disabled).toBe(true);

    await act(async () => { control(tree, 'DStv Two').props.onPress(); });
    await act(async () => { await Promise.resolve(); });
    expect(control(tree, 'Continue · ₦2,200').props.disabled).toBe(false);

    await act(async () => {
      resolveFirstPrice({ json: async () => ({ cable_plans_price: '1100' }) });
      await Promise.resolve();
    });
    expect(control(tree, 'Continue · ₦2,200').props.disabled).toBe(false);

    expect(mockApiPost.mock.calls.map((call) => call[1])).toEqual([
      { iuc: '11111111', cablenetwork: '2' },
      { iuc: '22222222', cablenetwork: '2' },
    ]);
  });
});
