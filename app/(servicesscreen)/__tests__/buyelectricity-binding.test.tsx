import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import BuyElectricity from '@/app/(servicesscreen)/buyelectricity';

const mockApiPost = jest.fn();

jest.mock('@/components/design/billkit');
jest.mock('@/lib/recents');
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => 0, getToken: jest.fn(async () => null) }));
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

const recentsModule = jest.requireMock('@/lib/recents') as { useBillRecents: () => unknown; EMPTY_RECENTS: unknown };
const emptyRecents = recentsModule.useBillRecents;

describe('electricity meter validation binding', () => {
  beforeEach(() => { mockApiPost.mockReset(); recentsModule.useBillRecents = emptyRecents; });

  it('resolves the meter on its own and offers Try again only after a failed check', async () => {
    let resolveCheck!: (value: unknown) => void;
    mockApiPost
      .mockReturnValueOnce(new Promise((resolve) => { resolveCheck = resolve; }))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ success: true, customer_name: 'Retried Customer' }) });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<BuyElectricity />); });
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Validate meter' })).toHaveLength(0);
    await act(async () => { control(tree, 'Meter number').props.onChangeText('12345678'); control(tree, 'Pick amount').props.onPress(); });
    // Not complete yet: nothing checked, no button to press.
    expect(mockApiPost).not.toHaveBeenCalled();
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Try again' })).toHaveLength(0);

    await act(async () => { control(tree, 'Meter number').props.onChangeText('1234567890'); });
    expect(mockApiPost).toHaveBeenCalledTimes(1);
    expect(tree.root.findAllByProps({ children: 'Checking…' }).length).toBeGreaterThan(0);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Try again' })).toHaveLength(0);

    await act(async () => { resolveCheck({ ok: true, json: async () => ({ success: false, message: 'Lookup unavailable' }) }); });
    expect(control(tree, 'Continue').props.disabled).toBe(true);
    expect(tree.root.findAllByProps({ children: 'Checking…' })).toHaveLength(0);

    await act(async () => { await control(tree, 'Try again').props.onPress(); });
    expect(mockApiPost).toHaveBeenCalledTimes(2);
    expect(control(tree, 'Continue').props.disabled).toBe(false);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Try again' })).toHaveLength(0);
    act(() => tree.unmount());
  });

  it('requires current meter validation and ignores a late stale response', async () => {
    let resolveOld!: (value: unknown) => void;
    const oldResponse = new Promise((resolve) => { resolveOld = resolve; });
    mockApiPost
      .mockReturnValueOnce(oldResponse)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ success: true, customer_name: 'Current Customer' }) })
      .mockReturnValueOnce(new Promise(() => {}));

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<BuyElectricity />); });
    await act(async () => {
      control(tree, 'Meter number').props.onChangeText('1111111111');
      control(tree, 'Pick amount').props.onPress();
    });
    expect(control(tree, 'Continue').props.disabled).toBe(true);

    await act(async () => { control(tree, 'Meter number').props.onChangeText('2222222222'); });
    expect(control(tree, 'Continue').props.disabled).toBe(false);
    await act(async () => {
      resolveOld({ ok: true, json: async () => ({ success: true, customer_name: 'Stale Customer' }) });
      await Promise.resolve();
    });
    expect(tree.root.findAllByProps({ children: '✓ Stale Customer' })).toHaveLength(0);
    expect(tree.root.findAllByProps({ children: '✓ Current Customer' }).length).toBeGreaterThan(0);
    expect(control(tree, 'Continue').props.disabled).toBe(false);

    // A new meter type needs a new check; it runs on its own.
    await act(async () => { control(tree, 'Postpaid').props.onPress(); });
    expect(control(tree, 'Continue').props.disabled).toBe(true);
    expect(mockApiPost.mock.calls.map((call) => call[1])).toEqual([
      { meter: '1111111111', disco: '1', meter_type: 'prepaid' },
      { meter: '2222222222', disco: '1', meter_type: 'prepaid' },
      { meter: '2222222222', disco: '1', meter_type: 'postpaid' },
    ]);
  });

  it('opens on the disco and meter of the most recent payment', async () => {
    const recents = {
      phones: [], smartcards: [], betting: [], exam_phones: [],
      meters: [
        { meter: '45678901234', disco: '2', meter_type: 'postpaid', name: 'ADEYEMI STORES', last_used: '2026-11-01' },
        { meter: '12345678901', disco: '1', meter_type: 'prepaid', name: 'FOLAKE', last_used: '2026-10-01' },
      ],
    };
    recentsModule.useBillRecents = () => recents;
    mockApiPost.mockResolvedValue({ ok: true, json: async () => ({ success: true, customer_name: 'Adeyemi Stores' }) });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<BuyElectricity />); });
    expect(control(tree, 'Meter number').props.value).toBe('45678901234');
    expect(mockApiPost.mock.calls.map((call) => call[1])).toEqual([
      { meter: '45678901234', disco: '2', meter_type: 'postpaid' },
    ]);

    // Picking another disco fills that disco's last meter and keeps the choice.
    await act(async () => { control(tree, 'Ikeja (IKEDC)').props.onPress(); });
    expect(control(tree, 'Meter number').props.value).toBe('12345678901');
    act(() => tree.unmount());
  });
});
