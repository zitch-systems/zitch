import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import Remita from '@/app/(servicesscreen)/remita';

const mockApiJson = jest.fn();
const mockReload = jest.fn();
const mockAcquireSpendAttempt = jest.fn();
const mockClearSpendAttempt = jest.fn();
const mockNotify = jest.fn();

jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'));
jest.mock('@/lib/api', () => ({
  apiJson: (...args: unknown[]) => mockApiJson(...args),
  newIdempotencyKey: () => 'app-remita-key-1',
}));
jest.mock('@/lib/pendingSpend', () => ({
  acquireSpendAttempt: (...args: unknown[]) => mockAcquireSpendAttempt(...args),
  clearSpendAttempt: (...args: unknown[]) => mockClearSpendAttempt(...args),
}));
jest.mock('expo-router', () => ({
  router: { back: jest.fn(), push: jest.fn(), replace: jest.fn() },
}));
jest.mock('@/lib/wallet', () => ({
  useWallet: () => ({ balance: 20000, reload: mockReload }),
}));
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: {
    brand: '#0FA295', brandDeep: '#08766d', ink1: '#111', ink2: '#222',
    ink3: '#333', lime: '#0f0', line: '#ddd', surface: '#fff',
  } }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Receipt', () => ({
  __esModule: true,
  default: ({ title, status }: { title: string; status: string }) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    const { Text: ActualText } = jest.requireActual<typeof import('react-native')>('react-native');
    return ReactActual.createElement(
      ActualText,
      { accessibilityLabel: 'remita-receipt' },
      `${title}|${status}`,
    );
  },
}));
jest.mock('@/components/design/ui', () => ({
  ...(() => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    const {
      Pressable: ActualPressable,
      Text: ActualText,
      TextInput: ActualTextInput,
      View: ActualView,
    } = jest.requireActual<typeof import('react-native')>('react-native');
    return {
      Screen: ({ children }: { children: ReactNode }) =>
        ReactActual.createElement(ActualView, null, children),
      Header: () => null,
      HeaderLink: () => null,
      Field: ({ value, onChangeText, placeholder }: {
        value: string;
        onChangeText: (value: string) => void;
        placeholder?: string;
      }) => ReactActual.createElement(ActualTextInput, {
        accessibilityLabel: placeholder,
        value,
        onChangeText,
      }),
      Btn: ({ label, onPress, disabled }: {
        label: string;
        onPress: () => void;
        disabled?: boolean;
      }) => ReactActual.createElement(
        ActualPressable,
        { accessibilityLabel: label, onPress, disabled },
        ReactActual.createElement(ActualText, null, label),
      ),
      Sheet: ({ open, children }: { open: boolean; children: ReactNode }) => open
        ? ReactActual.createElement(ActualView, null, children)
        : null,
      PinPad: ({ onComplete }: { onComplete: (pin: string) => void }) =>
        ReactActual.createElement(ActualPressable, {
          accessibilityLabel: 'Submit PIN',
          onPress: () => onComplete('1234'),
        }),
      money: (amount: number) => `₦${amount.toLocaleString()}`,
    };
  })(),
}));
jest.mock('@/components/design/flowkit', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const {
    Pressable: ActualPressable,
    Text: ActualText,
    TextInput: ActualTextInput,
  } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Label: ({ children }: { children: ReactNode }) =>
      ReactActual.createElement(ActualText, null, children),
    BalanceHint: () => null,
    AmountField: ({ value, onChangeText }: {
      value: string;
      onChangeText: (value: string) => void;
    }) => ReactActual.createElement(ActualTextInput, {
      accessibilityLabel: 'Amount',
      value,
      onChangeText,
    }),
    ConfirmSheet: ({ open, onPay }: { open: boolean; onPay: () => void }) => open
      ? ReactActual.createElement(ActualPressable, {
        accessibilityLabel: 'Confirm payment',
        onPress: onPay,
      })
      : null,
  };
});

const findControl = (tree: renderer.ReactTestRenderer, label: string) =>
  tree.root.findByProps({ accessibilityLabel: label });

describe('Remita idempotent retry receipts', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockApiJson.mockReset();
    mockReload.mockReset();
    mockAcquireSpendAttempt.mockReset();
    mockClearSpendAttempt.mockReset();
    mockNotify.mockReset();
    mockAcquireSpendAttempt.mockImplementation(async (_scope: string, fingerprint: string) => `key:${fingerprint}`);
    mockClearSpendAttempt.mockResolvedValue(undefined);
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('keeps a duplicate pending payment processing instead of displaying success', async () => {
    mockApiJson
      .mockResolvedValueOnce({ success: true, name: 'Ada Eze', amount: '5000' })
      .mockResolvedValueOnce({
        pending: true,
        duplicate: true,
        reference: 'ZTC-PENDING-1',
        message: 'This request is still processing. Its final status will be updated after provider confirmation.',
      });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Remita />); });
    await act(async () => { jest.runOnlyPendingTimers(); });
    await act(async () => {
      findControl(tree, 'Enter the RRR on your bill').props.onChangeText('120000000001');
    });
    await act(async () => { jest.runOnlyPendingTimers(); });
    await act(async () => {
      await findControl(tree, 'Verify RRR').props.onPress();
    });
    await act(async () => {
      findControl(tree, 'Pay · ₦5,000').props.onPress();
    });
    await act(async () => {
      findControl(tree, 'Confirm payment').props.onPress();
      jest.runOnlyPendingTimers();
    });
    await act(async () => {
      await findControl(tree, 'Submit PIN').props.onPress();
    });

    expect(findControl(tree, 'remita-receipt').props.children)
      .toBe('Payment processing|Processing');
    expect(mockReload).toHaveBeenCalledTimes(1);
  });

  it('acquires a new durable key when revalidation changes the fixed amount', async () => {
    mockApiJson
      .mockResolvedValueOnce({ success: true, name: 'Ada Eze', amount: '5000' })
      .mockResolvedValueOnce({ success: false, code: 'pin_incorrect', message: 'Incorrect PIN' })
      .mockResolvedValueOnce({ success: true, name: 'Ada Eze', amount: '6000' })
      .mockResolvedValueOnce({ pending: true, reference: 'ZTC-PENDING-2' });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Remita />); });
    await act(async () => { jest.runOnlyPendingTimers(); });
    await act(async () => {
      findControl(tree, 'Enter the RRR on your bill').props.onChangeText('120000000001');
    });
    await act(async () => { jest.runOnlyPendingTimers(); });
    await act(async () => { await findControl(tree, 'Verify RRR').props.onPress(); });
    await act(async () => { findControl(tree, 'Pay · ₦5,000').props.onPress(); });
    await act(async () => {
      findControl(tree, 'Confirm payment').props.onPress();
      jest.runOnlyPendingTimers();
    });
    await act(async () => { await findControl(tree, 'Submit PIN').props.onPress(); });

    // Force a fresh lookup of the same RRR; the biller now returns a changed,
    // authoritative amount while the first key remains durable after PIN error.
    await act(async () => {
      findControl(tree, 'Enter the RRR on your bill').props.onChangeText('120000000002');
      jest.runOnlyPendingTimers();
    });
    await act(async () => {
      findControl(tree, 'Enter the RRR on your bill').props.onChangeText('120000000001');
      jest.runOnlyPendingTimers();
    });
    await act(async () => { await findControl(tree, 'Verify RRR').props.onPress(); });
    await act(async () => { findControl(tree, 'Pay · ₦6,000').props.onPress(); });
    await act(async () => {
      findControl(tree, 'Confirm payment').props.onPress();
      jest.runOnlyPendingTimers();
    });
    await act(async () => { await findControl(tree, 'Submit PIN').props.onPress(); });

    expect(mockAcquireSpendAttempt.mock.calls).toEqual([
      ['remita', '120000000001|5000'],
      ['remita', '120000000001|6000'],
    ]);
    expect(mockApiJson.mock.calls[1][1].idempotency_key).toBe('key:120000000001|5000');
    expect(mockApiJson.mock.calls[3][1].idempotency_key).toBe('key:120000000001|6000');
  });

  it('discards a late validation result after the RRR changes', async () => {
    let resolveFirst!: (value: unknown) => void;
    const first = new Promise((resolve) => { resolveFirst = resolve; });
    mockApiJson
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce({ success: true, name: 'Current payer', amount: '6000' });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Remita />); });
    await act(async () => {
      findControl(tree, 'Enter the RRR on your bill').props.onChangeText('120000000001');
    });

    let firstRequest!: Promise<void>;
    await act(async () => {
      firstRequest = findControl(tree, 'Verify RRR').props.onPress();
      await Promise.resolve();
    });
    await act(async () => {
      findControl(tree, 'Enter the RRR on your bill').props.onChangeText('120000000002');
    });
    await act(async () => {
      resolveFirst({ success: true, name: 'Stale payer', amount: '5000' });
      await firstRequest;
    });

    expect(findControl(tree, 'Pay').props.disabled).toBe(true);
    expect(findControl(tree, 'Verify RRR')).toBeTruthy();

    await act(async () => { await findControl(tree, 'Verify RRR').props.onPress(); });
    expect(findControl(tree, 'Pay · ₦6,000').props.disabled).toBe(false);
    expect(mockApiJson.mock.calls.slice(0, 2).map((call) => call[1].rrr)).toEqual([
      '120000000001',
      '120000000002',
    ]);
  });

  it('handles durable-storage rejection without sending an unkeyed payment', async () => {
    mockApiJson.mockResolvedValueOnce({ success: true, name: 'Ada Eze', amount: '5000' });
    mockAcquireSpendAttempt.mockRejectedValueOnce(new Error('storage unavailable'));

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Remita />); });
    await act(async () => { jest.runOnlyPendingTimers(); });
    await act(async () => {
      findControl(tree, 'Enter the RRR on your bill').props.onChangeText('120000000001');
    });
    await act(async () => { jest.runOnlyPendingTimers(); });
    await act(async () => { await findControl(tree, 'Verify RRR').props.onPress(); });
    await act(async () => { findControl(tree, 'Pay · ₦5,000').props.onPress(); });
    await act(async () => {
      findControl(tree, 'Confirm payment').props.onPress();
      jest.runOnlyPendingTimers();
    });
    await act(async () => { await findControl(tree, 'Submit PIN').props.onPress(); });

    expect(mockApiJson).toHaveBeenCalledTimes(1);
    expect(mockNotify).toHaveBeenCalledWith(
      'Unable to start payment',
      'Could not safely prepare this request. Please try again.',
    );
    expect(tree.root.findAllByProps({ accessibilityLabel: 'remita-receipt' })).toHaveLength(0);
  });
});
