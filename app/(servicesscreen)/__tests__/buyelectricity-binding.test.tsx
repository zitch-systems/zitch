import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import BuyElectricity from '@/app/(servicesscreen)/buyelectricity';

const mockApiPost = jest.fn();

jest.mock('@/lib/secureStore', () => ({ getToken: jest.fn(async () => null) }));
jest.mock('@/lib/api', () => ({ apiPost: (...args: unknown[]) => mockApiPost(...args) }));
jest.mock('@/lib/pendingSpend', () => ({ acquireSpendAttempt: jest.fn(), clearSpendAttempt: jest.fn() }));
jest.mock('@/lib/spendOutcome', () => ({ classifySpendResponse: jest.fn(), isRecoveredSpendResponse: jest.fn() }));
jest.mock('expo-router', () => ({ router: { back: jest.fn(), replace: jest.fn() } }));
jest.mock('@/lib/wallet', () => ({ useWallet: () => ({ balance: 20000, billPaymentsAvailable: true, transfersAvailable: true, reload: jest.fn() }) }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/Receipt', () => ({ __esModule: true, default: () => null }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brandDeep: '#08766d', ink2: '#222', ink3: '#333' } }),
  font: { bold: 'bold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text, TextInput, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Header: () => null,
    Field: ({ label, placeholder, value, onChangeText }: { label?: string; placeholder?: string; value: string; onChangeText: (value: string) => void }) =>
      ReactActual.createElement(TextInput, { accessibilityLabel: label || placeholder, value, onChangeText }),
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) =>
      ReactActual.createElement(
        Pressable,
        { accessibilityLabel: label, onPress, disabled },
        ReactActual.createElement(Text, null, label),
      ),
    Sheet: () => null,
    PinPad: () => null,
    money: (amount: number) => `₦${amount}`,
    Naira: () => null,
  };
});
jest.mock('@/components/design/flowkit', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Label: ({ children }: { children: ReactNode }) => ReactActual.createElement(Text, null, children),
    ProviderGrid: () => null,
    Segmented: ({ onChange }: { onChange: (value: string) => void }) => ReactActual.createElement(
      Pressable,
      { accessibilityLabel: 'Postpaid', onPress: () => onChange('postpaid') },
      ReactActual.createElement(Text, null, 'Postpaid'),
    ),
    QuickAmounts: ({ onPick }: { onPick: (value: string) => void }) => ReactActual.createElement(
      Pressable,
      { accessibilityLabel: 'Pick amount', onPress: () => onPick('1000') },
      ReactActual.createElement(Text, null, '1000'),
    ),
    ConfirmSheet: () => null,
    BalanceHint: () => null,
  };
});

const control = (tree: renderer.ReactTestRenderer, label: string) =>
  tree.root.findByProps({ accessibilityLabel: label });

describe('electricity meter validation binding', () => {
  beforeEach(() => mockApiPost.mockReset());

  it('does not mark an HTTP 200 failure envelope as verified', async () => {
    mockApiPost.mockResolvedValueOnce({ ok: true, json: async () => ({ success: false, message: 'Lookup unavailable' }) });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<BuyElectricity />); });
    await act(async () => { control(tree, 'Meter number').props.onChangeText('12345678'); control(tree, 'Pick amount').props.onPress(); });
    await act(async () => { await control(tree, 'Validate meter').props.onPress(); });
    expect(control(tree, 'Continue').props.disabled).toBe(true);
    act(() => tree.unmount());
  });

  it('requires current meter validation and ignores a late stale response', async () => {
    let resolveOld!: (value: unknown) => void;
    const oldResponse = new Promise((resolve) => { resolveOld = resolve; });
    mockApiPost
      .mockReturnValueOnce(oldResponse)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ success: true, customer_name: 'Current Customer' }) });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<BuyElectricity />); });
    await act(async () => {
      control(tree, 'Meter number').props.onChangeText('11111111');
      control(tree, 'Pick amount').props.onPress();
    });
    expect(control(tree, 'Continue').props.disabled).toBe(true);

    let pending!: Promise<void>;
    await act(async () => {
      pending = control(tree, 'Validate meter').props.onPress();
      await Promise.resolve();
    });
    await act(async () => { control(tree, 'Meter number').props.onChangeText('22222222'); });
    await act(async () => {
      resolveOld({ ok: true, json: async () => ({ success: true, customer_name: 'Stale Customer' }) });
      await pending;
    });
    expect(control(tree, 'Continue').props.disabled).toBe(true);

    await act(async () => { await control(tree, 'Validate meter').props.onPress(); });
    expect(control(tree, 'Continue').props.disabled).toBe(false);

    await act(async () => { control(tree, 'Postpaid').props.onPress(); });
    expect(control(tree, 'Continue').props.disabled).toBe(true);
    expect(mockApiPost.mock.calls.map((call) => call[1])).toEqual([
      { meter: '11111111', disco: '1', meter_type: 'prepaid' },
      { meter: '22222222', disco: '1', meter_type: 'prepaid' },
    ]);
  });
});
