import React, { type ReactNode } from 'react';
import { Text } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import SendMoney from '@/app/(servicesscreen)/sendmoney';

const mockResolveLegacy = jest.fn();
const mockResolveBank = jest.fn();
const mockNotify = jest.fn();
const mockAcquireSpendAttempt = jest.fn();
const mockClearSpendAttempt = jest.fn();
const mockClassifySpendResponse = jest.fn();
const mockSendLegacy = jest.fn();
const mockBiometricAvailable = jest.fn();
const mockAuthenticate = jest.fn();
const mockGetToken = jest.fn();
const mockApiPost = jest.fn();
const mockRouterPush = jest.fn();
const mockReloadWallet = jest.fn();
const originalFetch = global.fetch;

jest.mock('expo-router', () => ({
  router: { back: jest.fn(), push: (...args: unknown[]) => mockRouterPush(...args), replace: jest.fn() },
  useLocalSearchParams: () => ({}),
}));
jest.mock('@/lib/secureStore', () => ({ getToken: (...args: unknown[]) => mockGetToken(...args) }));
jest.mock('@/lib/api', () => ({ apiPost: (...args: unknown[]) => mockApiPost(...args) }));
jest.mock('@/lib/pendingSpend', () => ({
  acquireSpendAttempt: (...args: unknown[]) => mockAcquireSpendAttempt(...args),
  clearSpendAttempt: (...args: unknown[]) => mockClearSpendAttempt(...args),
}));
jest.mock('@/lib/spendOutcome', () => ({
  classifySpendResponse: (...args: unknown[]) => mockClassifySpendResponse(...args),
  isRecoveredSpendResponse: jest.fn(),
}));
jest.mock('@/lib/services/transfers', () => ({
  transfersService: {
    resolve: (...args: unknown[]) => mockResolveBank(...args),
    resolveLegacy: (...args: unknown[]) => mockResolveLegacy(...args),
    send: jest.fn(),
    sendLegacy: (...args: unknown[]) => mockSendLegacy(...args),
  },
}));
jest.mock('@/lib/biometrics', () => ({
  isBiometricAvailable: (...args: unknown[]) => mockBiometricAvailable(...args),
  authenticate: (...args: unknown[]) => mockAuthenticate(...args),
}));
jest.mock('@/lib/wallet', () => ({
  useWallet: () => ({ balance: 200000, reload: (...args: unknown[]) => mockReloadWallet(...args) }),
}));
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Receipt', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text: NativeText, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    __esModule: true,
    default: ({ title, message, status, footer }: {
      title: string; message: string; status: string; footer?: ReactNode;
    }) => ReactActual.createElement(
      View,
      null,
      ReactActual.createElement(NativeText, null, title),
      ReactActual.createElement(NativeText, null, message),
      ReactActual.createElement(NativeText, null, status),
      footer,
    ),
  };
});
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: {
    brand: '#0FA295', brandDeep: '#08766d', red: '#c00', ink1: '#111', ink2: '#222',
    ink3: '#333', line: '#ddd', surface: '#fff',
  } }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold', medium: 'medium' },
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
    PinPad: ({ onComplete }: { onComplete: (pin: string) => void }) =>
      ReactActual.createElement(Pressable, {
        accessibilityLabel: 'Submit PIN',
        onPress: () => onComplete('1234'),
      }),
    money: (amount: number) => `₦${amount.toLocaleString()}`,
    Naira: ({ children }: { children?: ReactNode }) => ReactActual.createElement(NativeText, null, children),
  };
});
jest.mock('@/components/design/flowkit', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text: NativeText, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Label: ({ children }: { children: ReactNode }) => ReactActual.createElement(NativeText, null, children),
    Segmented: ({ options, onChange }: { options: { v: string; label: string }[]; onChange: (v: string) => void }) =>
      ReactActual.createElement(
        View,
        null,
        ...options.map((option) => ReactActual.createElement(
          Pressable,
          { key: option.v, accessibilityLabel: option.label, onPress: () => onChange(option.v) },
          ReactActual.createElement(NativeText, null, option.label),
        )),
      ),
    QuickAmounts: () => null,
    ConfirmSheet: ({ open, onPay }: { open: boolean; onPay: () => void }) => open
      ? ReactActual.createElement(Pressable, {
        accessibilityLabel: 'Confirm transfer',
        onPress: onPay,
      })
      : null,
    BalanceHint: () => null,
    Monogram: () => null,
  };
});

const control = (tree: renderer.ReactTestRenderer, label: string) =>
  tree.root.findByProps({ accessibilityLabel: label });

const pressAncestor = (node: renderer.ReactTestInstance) => {
  let current = node.parent;
  while (current && typeof current.props.onPress !== 'function') current = current.parent;
  if (!current) throw new Error('No pressable ancestor found');
  return current.props.onPress();
};

const pressText = (tree: renderer.ReactTestRenderer, label: string) => {
  const node = tree.root.findAllByType(Text).find((candidate) => candidate.props.children === label);
  if (!node) throw new Error(`No text found for ${label}`);
  return pressAncestor(node);
};

const loadBankCatalogue = () => {
  mockGetToken.mockResolvedValue('session-token');
  global.fetch = jest.fn(async () => ({
    json: async () => ({
      banks: [
        { code: 'bank-a', name: 'Bank A', color: '#111111' },
        { code: 'bank-b', name: 'Bank B', color: '#222222' },
      ],
    }),
  })) as any;
  mockApiPost.mockResolvedValue({ json: async () => ({ beneficiaries: [] }) });
};

describe('SendMoney recipient response binding', () => {
  beforeEach(() => {
    mockResolveLegacy.mockReset();
    mockResolveBank.mockReset();
    mockNotify.mockReset();
    mockAcquireSpendAttempt.mockReset();
    mockClearSpendAttempt.mockReset();
    mockClassifySpendResponse.mockReset();
    mockSendLegacy.mockReset();
    mockBiometricAvailable.mockReset().mockResolvedValue(false);
    mockAuthenticate.mockReset().mockResolvedValue(false);
    mockGetToken.mockReset().mockResolvedValue(null);
    mockApiPost.mockReset();
    mockRouterPush.mockReset();
    mockReloadWallet.mockReset();
    global.fetch = originalFetch;
  });

  afterEach(() => {
    jest.useRealTimers();
    global.fetch = originalFetch;
  });

  it('does not apply a late recipient response to an edited identifier', async () => {
    let resolveFirst!: (value: unknown) => void;
    const first = new Promise((resolve) => { resolveFirst = resolve; });
    mockResolveLegacy
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce({
        success: true,
        name: 'Current Recipient',
        phone: '08020000002',
        recipient_key: 'recipient-key-2',
      });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<SendMoney />); });
    await act(async () => { control(tree, 'To Zitch').props.onPress(); });
    await act(async () => {
      control(tree, 'Zitch tag or phone').props.onChangeText('08010000001');
    });

    let firstRequest!: Promise<void>;
    await act(async () => {
      firstRequest = control(tree, 'Confirm recipient').props.onPress();
      await Promise.resolve();
    });
    await act(async () => {
      control(tree, 'Zitch tag or phone').props.onChangeText('08020000002');
    });
    await act(async () => {
      resolveFirst({
        success: true,
        name: 'Stale Recipient',
        phone: '08010000001',
        recipient_key: 'recipient-key-1',
      });
      await firstRequest;
    });

    expect(control(tree, 'Continue').props.disabled).toBe(true);
    expect(control(tree, 'Confirm recipient')).toBeTruthy();
    expect(tree.root.findAllByType(Text).some((node) =>
      String(node.props.children).includes('Stale Recipient'))).toBe(false);

    await act(async () => { await control(tree, 'Confirm recipient').props.onPress(); });
    await act(async () => { control(tree, 'Enter amount').props.onChangeText('1000'); });

    expect(control(tree, 'Continue').props.disabled).toBe(false);
    expect(tree.root.findAllByType(Text).some((node) =>
      String(node.props.children).includes('Current Recipient'))).toBe(true);
    expect(mockResolveLegacy.mock.calls.map((call) => call[0])).toEqual([
      '08010000001',
      '08020000002',
    ]);
  });

  it('does not enable payment without the backend opaque recipient key', async () => {
    mockResolveLegacy.mockResolvedValueOnce({
      success: true,
      name: 'Legacy Recipient',
      phone: '08020000002',
    });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<SendMoney />); });
    await act(async () => { control(tree, 'To Zitch').props.onPress(); });
    await act(async () => {
      control(tree, 'Zitch tag or phone').props.onChangeText('08020000002');
      control(tree, 'Enter amount').props.onChangeText('1000');
    });
    await act(async () => { await control(tree, 'Confirm recipient').props.onPress(); });

    expect(control(tree, 'Continue').props.disabled).toBe(true);
    expect(mockNotify).toHaveBeenCalledWith(
      'Unable to confirm recipient',
      'Refresh the app and confirm this recipient again.',
    );
  });

  it('binds a manual bank-name response to the latest account and bank selection', async () => {
    jest.useFakeTimers();
    loadBankCatalogue();
    let resolveFirst!: (value: unknown) => void;
    const first = new Promise((resolve) => { resolveFirst = resolve; });
    mockResolveBank
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce({ success: true, name: 'Current Holder' });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => {
      tree = renderer.create(<SendMoney />);
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      control(tree, 'Account number').props.onChangeText('0123456789');
      control(tree, 'Enter amount').props.onChangeText('1000');
    });

    let firstRequest!: Promise<void>;
    await act(async () => { pressAncestor(control(tree, 'Bank')); });
    await act(async () => {
      firstRequest = pressText(tree, 'Bank A');
      await Promise.resolve();
    });
    expect(control(tree, 'Continue').props.disabled).toBe(true);

    await act(async () => { pressAncestor(control(tree, 'Bank')); });
    await act(async () => { await pressText(tree, 'Bank B'); });
    expect(control(tree, 'Continue').props.disabled).toBe(false);
    expect(tree.root.findAllByType(Text).some((node) =>
      String(node.props.children).includes('Current Holder'))).toBe(true);

    await act(async () => {
      resolveFirst({ success: true, name: 'Stale Holder' });
      await firstRequest;
    });
    expect(control(tree, 'Continue').props.disabled).toBe(false);
    expect(tree.root.findAllByType(Text).some((node) =>
      String(node.props.children).includes('Stale Holder'))).toBe(false);
    expect(mockResolveBank.mock.calls).toEqual([
      ['0123456789', 'bank-a'],
      ['0123456789', 'bank-b'],
    ]);
    act(() => tree.unmount());
  });

  it('keeps bank transfer disabled when manual holder-name verification fails', async () => {
    jest.useFakeTimers();
    loadBankCatalogue();
    mockResolveBank.mockResolvedValueOnce({ success: false, message: 'Name enquiry unavailable' });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => {
      tree = renderer.create(<SendMoney />);
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      control(tree, 'Account number').props.onChangeText('0123456789');
      control(tree, 'Enter amount').props.onChangeText('1000');
    });
    await act(async () => { pressAncestor(control(tree, 'Bank')); });
    await act(async () => { await pressText(tree, 'Bank A'); });

    expect(control(tree, 'Continue').props.disabled).toBe(true);
    expect(tree.root.findAllByType(Text).some((node) =>
      String(node.props.children).includes('Name enquiry unavailable'))).toBe(true);
    act(() => tree.unmount());
  });

  it('does not claim pending or dispatch when biometric authorization fails locally', async () => {
    jest.useFakeTimers();
    mockResolveLegacy.mockResolvedValueOnce({
      success: true,
      name: 'Large Recipient',
      recipient_key: 'recipient-key-large',
    });
    mockBiometricAvailable.mockResolvedValueOnce(true);
    mockAuthenticate.mockRejectedValueOnce(new Error('biometric service unavailable'));

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<SendMoney />); });
    await act(async () => { control(tree, 'To Zitch').props.onPress(); });
    await act(async () => {
      control(tree, 'Zitch tag or phone').props.onChangeText('08020000002');
      control(tree, 'Enter amount').props.onChangeText('100000');
    });
    await act(async () => { await control(tree, 'Confirm recipient').props.onPress(); });
    await act(async () => { control(tree, 'Continue').props.onPress(); });
    await act(async () => {
      control(tree, 'Confirm transfer').props.onPress();
      jest.advanceTimersByTime(320);
    });
    await act(async () => { await control(tree, 'Submit PIN').props.onPress(); });

    expect(mockAcquireSpendAttempt).not.toHaveBeenCalled();
    expect(mockSendLegacy).not.toHaveBeenCalled();
    expect(mockNotify).toHaveBeenCalledWith(
      'Unable to start transfer',
      'Could not safely prepare or authorize this request. Please try again.',
    );
    expect(control(tree, 'Continue')).toBeTruthy();
    act(() => tree.unmount());
  });

  it('keeps an under-review P2P attempt durable and opens live status by reference', async () => {
    jest.useFakeTimers();
    mockResolveLegacy.mockResolvedValueOnce({
      success: true,
      name: 'Review Recipient',
      recipient_key: 'recipient-key-review',
    });
    mockAcquireSpendAttempt.mockResolvedValueOnce('durable-transfer-key');
    mockSendLegacy.mockResolvedValueOnce({
      pending: true,
      under_review: true,
      reference: 'P2P-REVIEW-1',
      message: 'We are confirming the final outcome. Do not send this transfer again.',
    });
    mockClassifySpendResponse.mockReturnValueOnce('pending');

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<SendMoney />); });
    await act(async () => { control(tree, 'To Zitch').props.onPress(); });
    await act(async () => {
      control(tree, 'Zitch tag or phone').props.onChangeText('08020000002');
      control(tree, 'Enter amount').props.onChangeText('1000');
    });
    await act(async () => { await control(tree, 'Confirm recipient').props.onPress(); });
    await act(async () => { control(tree, 'Continue').props.onPress(); });
    await act(async () => {
      control(tree, 'Confirm transfer').props.onPress();
      jest.advanceTimersByTime(320);
    });
    await act(async () => { await control(tree, 'Submit PIN').props.onPress(); });

    expect(mockAcquireSpendAttempt).toHaveBeenCalledWith(
      'zitch-transfer',
      'recipient-key-review|1000',
    );
    expect(mockSendLegacy).toHaveBeenCalledWith(expect.objectContaining({
      recipient_key: 'recipient-key-review',
      idempotency_key: 'durable-transfer-key',
    }));
    expect(mockClearSpendAttempt).not.toHaveBeenCalled();
    expect(mockReloadWallet).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(tree.toJSON())).toContain('Transfer under review');
    expect(JSON.stringify(tree.toJSON())).toContain('Do not send this transfer again');

    await act(async () => { control(tree, 'View transfer status').props.onPress(); });
    expect(mockRouterPush).toHaveBeenCalledWith({
      pathname: '/txndetail',
      params: expect.objectContaining({
        reference: 'P2P-REVIEW-1',
        status: 'Under review',
        underReview: '1',
        spendScope: 'zitch-transfer',
        spendFingerprint: 'recipient-key-review|1000',
        spendKey: 'durable-transfer-key',
      }),
    });
    act(() => tree.unmount());
  });

  it('dispatches only once when the PIN completion callback fires twice', async () => {
    jest.useFakeTimers();
    mockResolveLegacy.mockResolvedValueOnce({
      success: true,
      name: 'One Recipient',
      recipient_key: 'recipient-key-once',
    });
    mockAcquireSpendAttempt.mockResolvedValue('same-durable-key');
    let finishSend!: (value: unknown) => void;
    mockSendLegacy.mockReturnValue(new Promise((resolve) => { finishSend = resolve; }));
    mockClassifySpendResponse.mockReturnValue('pending');

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<SendMoney />); });
    await act(async () => { control(tree, 'To Zitch').props.onPress(); });
    await act(async () => {
      control(tree, 'Zitch tag or phone').props.onChangeText('08020000002');
      control(tree, 'Enter amount').props.onChangeText('1000');
    });
    await act(async () => { await control(tree, 'Confirm recipient').props.onPress(); });
    await act(async () => { control(tree, 'Continue').props.onPress(); });
    await act(async () => {
      control(tree, 'Confirm transfer').props.onPress();
      jest.advanceTimersByTime(320);
    });

    await act(async () => {
      const submit = control(tree, 'Submit PIN');
      void submit.props.onPress();
      void submit.props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mockAcquireSpendAttempt).toHaveBeenCalledTimes(1);
    expect(mockSendLegacy).toHaveBeenCalledTimes(1);

    await act(async () => {
      finishSend({ pending: true, reference: 'P2P-PENDING-1' });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mockClearSpendAttempt).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });
});
