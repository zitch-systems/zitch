import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';

import TxnDetail from '@/app/(homepage)/txndetail';

const mockApiJson = jest.fn();
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
};

jest.mock('@/lib/api', () => ({
  apiJson: (...args: unknown[]) => mockApiJson(...args),
}));
jest.mock('@/lib/endpoints', () => ({
  EP: { wallet: { transactionStatus: '/api/wallet/transaction/status/' } },
}));
jest.mock('expo-router', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  return {
    router: { back: jest.fn() },
    useLocalSearchParams: () => mockParams,
    useFocusEffect: (effect: () => void | (() => void)) => ReactActual.useEffect(effect, [effect]),
  };
});
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

const transaction = (status: string) => ({
  success: true,
  transaction: {
    amount: '1000',
    date: 'Today',
    direction: 'out',
    reference: 'ZTC-POLL-1',
    service: 'Transfer',
    transaction_status: status,
  },
});

describe('transaction-detail pending refresh', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockApiJson.mockReset();
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
});
