import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';

import { ConnectedAccounts, LinkedBanksSummary } from '@/components/design/banklink';

const mockApiJson = jest.fn();
const mockAcquireSpendAttempt = jest.fn();
const mockClearSpendAttempt = jest.fn();
const mockNotify = jest.fn();
const mockOpenBrowser = jest.fn();
const mockReload = jest.fn();
const mockReloadLinked = jest.fn();

const linkedBank = {
  id: 7,
  bank_name: 'Test Bank',
  account_number: '****1234',
  account_name: 'TEST USER',
  balance: 1000,
  balance_updated: null,
  status: 'active',
};

jest.mock('@/lib/api', () => ({
  apiJson: (...args: unknown[]) => mockApiJson(...args),
}));
jest.mock('@/lib/pendingSpend', () => ({
  acquireSpendAttempt: (...args: unknown[]) => mockAcquireSpendAttempt(...args),
  clearSpendAttempt: (...args: unknown[]) => mockClearSpendAttempt(...args),
}));
jest.mock('@/components/design/Notify', () => ({
  notify: (...args: unknown[]) => mockNotify(...args),
}));
jest.mock('expo-web-browser', () => ({
  openBrowserAsync: (...args: unknown[]) => mockOpenBrowser(...args),
}));
jest.mock('expo-router', () => ({ router: { push: jest.fn() } }));
// These tests exercise funding actions, not native scrolling. The preset's
// ScrollView mock loads the actual iOS renderer on its first use.
jest.mock('react-native/Libraries/Components/ScrollView/ScrollView', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return function MockScrollView({ children, ...props }: import('react-native').ScrollViewProps) {
    return ReactActual.createElement(View, props, children);
  };
});
jest.mock('@/lib/wallet', () => ({
  useWallet: () => ({
    balance: 5000,
    totalBalance: 10000,
    linked: [linkedBank],
    showBal: true,
    reload: mockReload,
    reloadLinked: mockReloadLinked,
  }),
}));

jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: {
    amber: '#b9770e', brand: '#0FA295', ink1: '#111', ink2: '#222',
    ink3: '#333', line: '#ddd', surface: '#fff', surface3: '#eee',
  } }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/widgets', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    SectionLabel: ({ children }: { children: ReactNode }) => ReactActual.createElement(Text, null, children),
  };
});
jest.mock('@/components/design/flowkit', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { TextInput } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Monogram: () => null,
    AmountField: ({ value, onChangeText }: { value: string; onChangeText: (value: string) => void }) => ReactActual.createElement(
      TextInput,
      { accessibilityLabel: 'Linked bank amount', value, onChangeText },
    ),
  };
});
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Card: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Sheet: ({ open, children }: { open: boolean; children: ReactNode }) => open
      ? ReactActual.createElement(View, null, children)
      : null,
    NText: ({ children }: { children: ReactNode }) => ReactActual.createElement(Text, null, children),
    money: (amount: number) => `₦${amount.toLocaleString()}`,
  };
});

let mountedTree: renderer.ReactTestRenderer | undefined;

function mount(element: React.ReactElement) {
  // Both components have synchronous initial renders with these hook mocks.
  act(() => { mountedTree = renderer.create(element); });
  return mountedTree!;
}

afterEach(() => {
  act(() => { mountedTree?.unmount(); });
  mountedTree = undefined;
});

function openAndEnter(tree: renderer.ReactTestRenderer, amount: string) {
  act(() => tree.root.findByProps({ accessibilityLabel: 'Fund Zitch from Test Bank' }).props.onPress());
  act(() => tree.root.findByProps({ accessibilityLabel: 'Linked bank amount' }).props.onChangeText(amount));
}

async function submit(tree: renderer.ReactTestRenderer) {
  await act(async () => {
    await tree.root.findByProps({ accessibilityLabel: 'Confirm linked-bank funding' }).props.onPress();
  });
}

it('includes historical Zitch funds in the aggregate connected-bank total', () => {
  const tree = mount(<LinkedBanksSummary />);
  expect(JSON.stringify(tree.toJSON())).toContain('₦11,000');
});

describe('linked-bank funding durability', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAcquireSpendAttempt.mockResolvedValue('durable-bank-key');
    mockClearSpendAttempt.mockResolvedValue(undefined);
    mockOpenBrowser.mockResolvedValue({ type: 'dismiss' });
    mockReload.mockResolvedValue(undefined);
    mockReloadLinked.mockResolvedValue(undefined);
  });

  it('does not expose the unsupported linked-bank payout action', () => {
    const tree = mount(<ConnectedAccounts />);

    expect(tree.root.findAllByProps({ accessibilityLabel: 'Fund Test Bank from Zitch' })).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).not.toContain('Fund bank');
    expect(JSON.stringify(tree.toJSON())).not.toContain('/api/banklink/payout/');
  });

  it('acquires by current account and amount and retains ambiguous attempts', async () => {
    mockApiJson
      .mockResolvedValueOnce({ pending: true, message: 'Still processing', _httpOk: true, _httpStatus: 200 })
      .mockResolvedValueOnce({ offline: true });
    const tree = mount(<ConnectedAccounts />);

    openAndEnter(tree, '500');
    await submit(tree);
    act(() => tree.root.findByProps({ accessibilityLabel: 'Linked bank amount' }).props.onChangeText('600'));
    await submit(tree);

    expect(mockAcquireSpendAttempt).toHaveBeenNthCalledWith(1, 'banklink-fund', '7|500.00');
    expect(mockAcquireSpendAttempt).toHaveBeenNthCalledWith(2, 'banklink-fund', '7|600.00');
    expect(mockApiJson.mock.calls[0][1].idempotency_key).toBe('durable-bank-key');
    expect(mockApiJson.mock.calls[1][1].idempotency_key).toBe('durable-bank-key');
    expect(mockClearSpendAttempt).not.toHaveBeenCalled();
    expect(mockNotify).toHaveBeenCalledWith('Not confirmed', 'Still processing');
  });

  it('fails locally without dispatching when durable storage cannot be read', async () => {
    mockAcquireSpendAttempt.mockRejectedValueOnce(new Error('storage unavailable'));
    const tree = mount(<ConnectedAccounts />);

    openAndEnter(tree, '500');
    await submit(tree);

    expect(mockApiJson).not.toHaveBeenCalled();
    expect(mockClearSpendAttempt).not.toHaveBeenCalled();
    expect(mockNotify).toHaveBeenCalledWith(
      'Unable to start funding',
      'Could not safely prepare this request. Please try again.',
    );
  });

  it('keeps an initialized debit key and accurately resumes its authorization', async () => {
    mockApiJson.mockResolvedValueOnce({
      success: true,
      duplicate: true,
      authorization_url: 'https://pay.mono/resume',
      _httpOk: true,
      _httpStatus: 200,
    });
    const tree = mount(<ConnectedAccounts />);

    openAndEnter(tree, '500');
    await submit(tree);

    expect(mockClearSpendAttempt).not.toHaveBeenCalled();
    expect(mockOpenBrowser).toHaveBeenCalledWith('https://pay.mono/resume');
    expect(mockNotify).toHaveBeenCalledWith(
      'Continue earlier authorization',
      'Finish there — your Zitch wallet is credited only after your bank confirms.',
    );
  });

  it('clears only a definitive result and labels a settled replay as earlier funding', async () => {
    mockApiJson.mockResolvedValueOnce({
      success: true,
      duplicate: true,
      funded: true,
      reference: 'ZMONO-OLD',
      _httpOk: true,
      _httpStatus: 200,
    });
    const tree = mount(<ConnectedAccounts />);

    openAndEnter(tree, '500');
    await submit(tree);

    expect(mockClearSpendAttempt).toHaveBeenCalledWith(
      'banklink-fund', '7|500.00', 'durable-bank-key',
    );
    expect(mockNotify).toHaveBeenCalledWith(
      'Earlier funding confirmed',
      '₦500 was credited to your Zitch wallet. Start a new request if you want to fund it again.',
    );
    expect(mockReload).toHaveBeenCalled();
    expect(mockReloadLinked).toHaveBeenCalled();
  });

  it('clears a definitive refusal using the exact acquired marker', async () => {
    mockApiJson.mockResolvedValueOnce({
      success: false,
      message: 'Funding refused',
      _httpOk: false,
      _httpStatus: 422,
    });
    const tree = mount(<ConnectedAccounts />);

    openAndEnter(tree, '500');
    await submit(tree);

    expect(mockClearSpendAttempt).toHaveBeenCalledWith(
      'banklink-fund', '7|500.00', 'durable-bank-key',
    );
    expect(mockNotify).toHaveBeenCalledWith('Error', 'Funding refused');
  });
  it('rejects an insecure authorization URL without discarding the initialized debit', async () => {
    mockApiJson.mockResolvedValueOnce({ success: true, authorization_url: 'http://pay.example/authorize', _httpOk: true, _httpStatus: 200 });
    const tree = mount(<ConnectedAccounts />);
    openAndEnter(tree, '500');
    await submit(tree);
    expect(mockOpenBrowser).not.toHaveBeenCalled();
    expect(mockClearSpendAttempt).not.toHaveBeenCalled();
    expect(mockNotify).toHaveBeenCalledWith('Not confirmed', expect.stringContaining('authorization link was not confirmed'));
  });

});
