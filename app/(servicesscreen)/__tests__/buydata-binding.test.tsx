import React, { type ReactNode } from 'react';
import { Text } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import BuyData from '@/app/(servicesscreen)/buydata';

const mockFetch = jest.fn();

jest.mock('@/lib/secureStore', () => ({ getToken: jest.fn(async () => null) }));
jest.mock('@/lib/api', () => ({ apiPost: jest.fn() }));
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
  useTheme: () => ({ c: { ink1: '#111', ink2: '#222', ink3: '#333' } }),
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
    Segmented: () => null,
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

describe('BuyData catalogue response binding', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    mockFetch.mockReset();
    global.fetch = mockFetch as unknown as typeof fetch;
  });

  afterAll(() => { global.fetch = originalFetch; });

  it('ignores late plans and prices from a superseded selection', async () => {
    let resolveMtnPlans!: (value: unknown) => void;
    let resolveFirstPrice!: (value: unknown) => void;
    const mtnPlans = new Promise((resolve) => { resolveMtnPlans = resolve; });
    const firstPrice = new Promise((resolve) => { resolveFirstPrice = resolve; });

    mockFetch.mockImplementation((_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body || '{}'));
      if (body.datanetwork === '1') return mtnPlans;
      if (body.datanetwork === '2') {
        return Promise.resolve({
          json: async () => ({
            data_plans: [
              { plan_code: 'glo-one', name: 'Glo One', price: '1000' },
              { plan_code: 'glo-two', name: 'Glo Two', price: '2000' },
            ],
          }),
        });
      }
      if (body.selectedDataPlan === 'glo-one') return firstPrice;
      if (body.selectedDataPlan === 'glo-two') {
        return Promise.resolve({ json: async () => ({ price: '2200' }) });
      }
      throw new Error(`Unexpected request ${String(init?.body)}`);
    });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<BuyData />); });
    await act(async () => { control(tree, 'GLO').props.onPress(); });
    await act(async () => { await Promise.resolve(); });

    expect(control(tree, 'Glo One')).toBeTruthy();
    await act(async () => {
      resolveMtnPlans({ json: async () => ({
        data_plans: [{ plan_code: 'mtn-stale', name: 'Stale MTN Plan', price: '900' }],
      }) });
      await Promise.resolve();
    });
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Stale MTN Plan' })).toHaveLength(0);
    expect(control(tree, 'Glo One')).toBeTruthy();

    await act(async () => {
      control(tree, 'Phone number').props.onChangeText('08012345678');
      control(tree, 'Glo One').props.onPress();
      await Promise.resolve();
    });
    expect(control(tree, 'Continue').props.disabled).toBe(true);

    await act(async () => { control(tree, 'Glo Two').props.onPress(); });
    await act(async () => { await Promise.resolve(); });
    expect(control(tree, 'Continue · ₦2,200').props.disabled).toBe(false);

    await act(async () => {
      resolveFirstPrice({ json: async () => ({ price: '1100' }) });
      await Promise.resolve();
    });

    expect(control(tree, 'Continue · ₦2,200').props.disabled).toBe(false);
    expect(tree.root.findAllByType(Text).some((node) =>
      String(node.props.children).includes('Stale MTN Plan'))).toBe(false);
  });
});
