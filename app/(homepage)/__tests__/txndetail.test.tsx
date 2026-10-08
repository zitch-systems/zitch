import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';

import TxnDetail from '@/app/(homepage)/txndetail';

const mockApiJson = jest.fn();
const mockClearSpendAttempt = jest.fn();
const mockParams = {
  type: 'Transfer',
  amount: '1000',
  status: 'PENDING',
  dir: 'out',
  detail: 'Today',
  reference: 'ZTC-POLL-1',
  underReview: '',
  statusMessage: '',
  reviewKind: '',
  spendScope: 'zitch-transfer',
  spendFingerprint: 'recipient-key|1000',
  spendKey: 'durable-key',
};

jest.mock('@/lib/api', () => ({
  apiJson: (...args: unknown[]) => mockApiJson(...args),
}));
jest.mock('@/lib/endpoints', () => ({
  EP: { wallet: { transactionStatus: '/api/wallet/transaction/status/' } },
}));
jest.mock('@/lib/pendingSpend', () => ({
  clearSpendAttempt: (...args: unknown[]) => mockClearSpendAttempt(...args),
}));
jest.mock('expo-router', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  return {
    router: { back: jest.fn() },
    useLocalSearchParams: () => mockParams,
    useFocusEffect: (effect: () => void | (() => void)) => ReactActual.useEffect(effect, [effect]),
  };
});
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn().mockResolvedValue(undefined) }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: {
    amber: '#b9770e', bg: '#fff', brand: '#0FA295', ink1: '#111', ink3: '#333',
    lime: '#128c4a', line: '#ddd', red: '#c0392b', surface: '#fff',
  } }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/flowkit', () => ({ Monogram: () => null }));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const {
    Pressable, Text, View,
  } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Header: () => null,
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) =>
      ReactActual.createElement(
        Pressable,
        { accessibilityLabel: label, disabled, onPress },
        ReactActual.createElement(Text, null, label),
      ),
    money: (amount: number) => `₦${amount.toLocaleString()}`,
  };
});

const transaction = (status: string, reference = 'ZTC-POLL-1') => ({
  success: true,
  transaction: {
    amount: '1000',
    date: 'Today',
    direction: 'out',
    reference,
    service: 'Transfer',
    transaction_status: status,
  },
});

describe('transaction-detail pending refresh', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockApiJson.mockReset();
    mockClearSpendAttempt.mockReset();
    mockParams.status = 'PENDING';
    mockParams.underReview = '';
    mockParams.statusMessage = '';
    mockParams.reviewKind = '';
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it.each(['SUCCESSFUL', 'FAILED'])(
    'polls a pending transaction until it becomes %s',
    async (terminalStatus) => {
      mockApiJson
        .mockResolvedValueOnce(transaction('PENDING'))
        .mockResolvedValueOnce(transaction(terminalStatus));

      let tree!: renderer.ReactTestRenderer;
      await act(async () => { tree = renderer.create(<TxnDetail />); });
      expect(mockApiJson).toHaveBeenCalledTimes(1);
      expect(tree.root.findByProps({ accessibilityLabel: 'Refresh status' })).toBeTruthy();

      await act(async () => {
        jest.advanceTimersByTime(4000);
        await Promise.resolve();
      });

      expect(mockApiJson).toHaveBeenCalledTimes(2);
      expect(mockClearSpendAttempt).toHaveBeenCalledWith(
        'zitch-transfer',
        'recipient-key|1000',
        'durable-key',
      );
      expect(JSON.stringify(tree.toJSON())).toContain(terminalStatus);
      act(() => tree.unmount());
      jest.advanceTimersByTime(20000);
      expect(mockApiJson).toHaveBeenCalledTimes(2);
    },
  );

  it('cancels the next pending poll when the screen blurs', async () => {
    mockApiJson.mockResolvedValue(transaction('PENDING'));

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<TxnDetail />); });
    expect(mockApiJson).toHaveBeenCalledTimes(1);
    expect(mockClearSpendAttempt).not.toHaveBeenCalled();

    act(() => tree.unmount());
    jest.advanceTimersByTime(20000);
    await Promise.resolve();

    expect(mockApiJson).toHaveBeenCalledTimes(1);
  });

  it('labels an active provider conflict under review and shows do-not-retry guidance', async () => {
    mockApiJson.mockResolvedValue({
      ...transaction('PENDING'),
      transaction: {
        ...transaction('PENDING').transaction,
        under_review: true,
        review_kind: 'reversal',
        status_message: "We are confirming the bank's final outcome. Do not retry.",
      },
    });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<TxnDetail />); });
    const rendered = JSON.stringify(tree.toJSON());
    expect(rendered).toContain('Under review');
    expect(rendered).toContain('Do not retry');
    expect(tree.root.findByProps({ accessibilityLabel: 'Share status' })).toBeTruthy();
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Share receipt' })).toHaveLength(0);
    act(() => tree.unmount());
  });

  it('does not clear an attempt for a terminal response with a different reference', async () => {
    mockApiJson.mockResolvedValue(transaction('SUCCESSFUL', 'ZTC-DIFFERENT'));

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<TxnDetail />); });

    expect(mockClearSpendAttempt).not.toHaveBeenCalled();
    expect(JSON.stringify(tree.toJSON())).toContain('PENDING');
    expect(JSON.stringify(tree.toJSON())).not.toContain('SUCCESSFUL');
    act(() => tree.unmount());
  });

  it('does not let an older pending response overwrite a newer terminal refresh', async () => {
    let resolveInitial!: (value: unknown) => void;
    mockApiJson
      .mockReturnValueOnce(new Promise((resolve) => { resolveInitial = resolve; }))
      .mockResolvedValueOnce(transaction('SUCCESSFUL'));

    let tree!: renderer.ReactTestRenderer;
    await act(async () => {
      tree = renderer.create(<TxnDetail />);
      await Promise.resolve();
    });
    await act(async () => {
      await tree.root.findByProps({ accessibilityLabel: 'Refresh status' }).props.onPress();
    });
    expect(JSON.stringify(tree.toJSON())).toContain('SUCCESSFUL');

    await act(async () => {
      resolveInitial(transaction('PENDING'));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(JSON.stringify(tree.toJSON())).toContain('SUCCESSFUL');
    expect(JSON.stringify(tree.toJSON())).not.toContain('PENDING');
    expect(mockClearSpendAttempt).toHaveBeenCalledTimes(1);
    act(() => tree.unmount());
  });

  it('announces a manual refresh failure inline', async () => {
    mockApiJson
      .mockResolvedValueOnce(transaction('PENDING'))
      .mockRejectedValueOnce(new Error('offline'));

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<TxnDetail />); });
    await act(async () => {
      await tree.root.findByProps({ accessibilityLabel: 'Refresh status' }).props.onPress();
    });

    const alert = tree.root.findByProps({ accessibilityRole: 'alert' });
    expect(alert.props.children).toContain('Check your connection');
    act(() => tree.unmount());
  });
  it('keeps a stale successful ledger under review and preserves the retry guard', async () => {
    mockApiJson.mockResolvedValue({ ...transaction('SUCCESSFUL'), transaction: {
      ...transaction('SUCCESSFUL').transaction, under_review: true, token: 'secret-token',
    } });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<TxnDetail />); });
    expect(mockClearSpendAttempt).not.toHaveBeenCalled();
    expect(JSON.stringify(tree.toJSON())).toContain('Under review');
    expect(JSON.stringify(tree.toJSON())).not.toContain('secret-token');
    act(() => tree.unmount());
  });

  it('recovers and allows copying a confirmed electricity token from history', async () => {
    mockApiJson.mockResolvedValue({ ...transaction('SUCCESSFUL'), transaction: {
      ...transaction('SUCCESSFUL').transaction, service: 'Electricity', token: '1234-5678-9012-3456-7890',
      meter: '12345678901', electricity_units: '42.5 kWh',
    } });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<TxnDetail />); });
    expect(JSON.stringify(tree.toJSON())).toContain('1234-5678-9012-3456-7890');
    expect(JSON.stringify(tree.toJSON())).toContain('42.5 kWh');
    await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Copy electricity token' }).props.onPress(); });
    expect(require('expo-clipboard').setStringAsync).toHaveBeenCalledWith('1234-5678-9012-3456-7890');
    act(() => tree.unmount());
  });

});
