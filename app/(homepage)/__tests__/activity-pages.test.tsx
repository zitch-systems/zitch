import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import Analysis from '@/app/(homepage)/analysis';
import History from '@/app/(homepage)/history';
import Notifications from '@/app/(homepage)/notifications';
import { monthKey, monthLabel } from '@/lib/format';

const mockReload = jest.fn().mockResolvedValue(undefined);
const mockPush = jest.fn();
const mockMonth = monthKey(Date.now());
const mockWallet = { txns: [] as any[], hydrated: true, loading: false, historyError: '', reload: mockReload };
jest.mock('@/lib/wallet', () => ({ useWallet: () => mockWallet, transactionParams: (row: any) => ({ reference: row.reference }) }));
jest.mock('@/lib/notifications', () => ({ clearPendingNotificationOpen: async () => {} }));
jest.mock('expo-router', () => ({
  router: { back: jest.fn(), push: (...args: unknown[]) => mockPush(...args) },
  useLocalSearchParams: () => ({ month: mockMonth }),
  useFocusEffect: (callback: () => void) => jest.requireActual<typeof import('react')>('react').useEffect(callback, [callback]),
}));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Skeleton', () => ({ SkeletonRow: () => null }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: {}, theme: 'light' }), font: {}, ICON_COLORS: {}, iconTint: () => '' }));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View, Text, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  const status = jest.requireActual<typeof import('@/lib/transactionStatus')>('@/lib/transactionStatus');
  const Container = ({ children, ...props }: { children: ReactNode }) => ReactActual.createElement(View, props, children);
  return {
    Screen: Container, Card: Container, NText: Text, Progress: () => null, Header: () => null, HeaderLink: () => null,
    money: (value: number) => `₦${value}`, txnState: status.txnState, settledTransactionTotal: status.settledTransactionTotal,
    Btn: ({ label, ...props }: any) => ReactActual.createElement(Pressable, { ...props, accessibilityLabel: label }, ReactActual.createElement(Text, null, label)),
    SelectRow: ({ value, onPress }: any) => ReactActual.createElement(Pressable, { accessibilityLabel: value, onPress }),
    PickerSheet: ({ open, options, onPick, onClose }: any) => open ? ReactActual.createElement(View, null, options.map((option: any) => ReactActual.createElement(Pressable, { key: option.v, accessibilityLabel: option.label, onPress: () => { onPick(option.v); onClose(); } }))) : null,
    TxnRow: ({ txn, onPress, onSelect }: any) => ReactActual.createElement(Pressable, { accessibilityLabel: `Transaction ${txn.id}`, onPress: onPress || (() => onSelect(txn)) }),
  };
});

const row = (id: string, amount: number, status = 'Successful', dir = 'out') => ({
  id, reference: `ref-${id}`, amount, status, dir, icon: 'send', type: 'Transfer', detail: 'Today', ts: Date.now(),
});
const mount = async (Page: React.ComponentType) => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Page />); });
  return tree;
};
const press = async (tree: renderer.ReactTestRenderer, label: string) => {
  await act(async () => { tree.root.findByProps({ accessibilityLabel: label }).props.onPress(); });
};

beforeEach(() => { mockWallet.txns = []; mockWallet.hydrated = true; mockWallet.historyError = ''; mockReload.mockClear(); mockPush.mockClear(); });

it('does not call unloaded or failed analytics an empty spending period and retries successfully', async () => {
  mockWallet.hydrated = false;
  const tree = await mount(Analysis);
  expect(JSON.stringify(tree.toJSON())).toContain('Loading spending…');
  expect(JSON.stringify(tree.toJSON())).not.toContain('No completed spending');
  mockWallet.hydrated = true; mockWallet.historyError = 'Offline';
  act(() => tree.update(<Analysis />));
  expect(JSON.stringify(tree.toJSON())).toContain('Spending is unavailable');
  await press(tree, 'Retry loading activity');
  expect(mockReload).toHaveBeenCalledTimes(2);
  mockWallet.historyError = '';
  mockWallet.txns = [row('settled', 100), row('pending', 900, 'Pending'), row('bad', NaN), row('received', 50, 'Successful', 'in')];
  act(() => tree.update(<Analysis />));
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('₦100'); expect(output).toContain('₦50'); expect(output).not.toContain('₦900'); expect(output).not.toContain('₦NaN');
  act(() => tree.unmount());
});

it('clears empty history filters, collapses months and opens their analysis', async () => {
  mockWallet.txns = [row('settled', 100)];
  const tree = await mount(History);
  await press(tree, 'All statuses'); await press(tree, 'Failed');
  expect(JSON.stringify(tree.toJSON())).toContain('No transactions match these filters');
  await press(tree, 'Clear filters');
  expect(tree.root.findByProps({ accessibilityLabel: 'Transaction settled' })).toBeTruthy();
  const label = monthLabel(mockMonth);
  await press(tree, `${label}, collapse`);
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Transaction settled' })).toHaveLength(0);
  expect(tree.root.findByProps({ accessibilityLabel: `${label}, expand` }).props.accessibilityState.expanded).toBe(false);
  await press(tree, `Spending analysis for ${label}`);
  expect(mockPush).toHaveBeenCalledWith({ pathname: '/analysis', params: { month: mockMonth } });
  act(() => tree.unmount());
});

it.each([['History', History, 'Retry loading transactions'], ['Notifications', Notifications, 'Retry loading activity']] as const)('%s retries unavailable activity without claiming an empty account', async (_name, Page, retryLabel) => {
  mockWallet.historyError = 'Offline';
  const tree = await mount(Page);
  expect(JSON.stringify(tree.toJSON())).toContain('unavailable');
  expect(JSON.stringify(tree.toJSON())).not.toContain('Nothing here yet');
  await press(tree, retryLabel);
  expect(mockReload).toHaveBeenCalledTimes(2);
  act(() => tree.unmount());
});

it('opens notification activity using its transaction reference', async () => {
  mockWallet.txns = [row('pending', 100, 'Pending')];
  const tree = await mount(Notifications);
  await press(tree, 'Transaction pending');
  expect(mockPush).toHaveBeenCalledWith({ pathname: '/txndetail', params: { reference: 'ref-pending' } });
  act(() => tree.unmount());
});
