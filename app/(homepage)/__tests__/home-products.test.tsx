import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import { Text as RNText } from 'react-native';
import Home from '@/app/(homepage)/home';
import { EP } from '@/lib/endpoints';

const mockWallet = {
  balance: 0, totalBalance: 1200, historicalBalance: 1200, fundingProvider: 'partnership',
  firstName: 'Test', fullName: 'Test Customer', avatar: '', accountNumber: '0454243073', bankName: 'Wema Bank',
  billPaymentsAvailable: false, transfersAvailable: false,
  fundingMessage: 'Your account balance needs review before you can spend.',
  txns: [] as unknown[], showBal: true, setShowBal: jest.fn(), reload: jest.fn(), linked: [], reloadLinked: jest.fn(),
  hydrated: true, balanceLoaded: true, balanceError: '', historyError: '', loading: false,
};

// The refusal pop-up has its own tests (components/kyc/__tests__/detailsReviewPrompt.test.tsx).
jest.mock('@/components/kyc/DetailsReviewPrompt', () => ({ DetailsReviewPrompt: () => null }));
jest.mock('@/lib/wallet', () => ({ useWallet: () => mockWallet, transactionParams: jest.fn() }));
const mockApiJson = jest.fn();
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('expo-router', () => ({
  router: { push: jest.fn() },
  useFocusEffect: (callback: () => void) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    ReactActual.useEffect(callback, [callback]);
  },
}));
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn() }));
jest.mock('expo-linear-gradient', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return { LinearGradient: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children) };
});
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Brand', () => ({ Avatar: () => null }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/SmartPaste', () => () => null);
jest.mock('@/components/design/ConnectedAccounts', () => ({ ConnectedAccounts: () => null }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#0FA295', brandDeep: '#08766d', ink1: '#111', ink2: '#222', ink3: '#333', line: '#ddd', surface: '#fff' } }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold', medium: 'medium' },
  fontA: { display: 'Sora', displaySemi: 'Sora', displayX: 'Sora', body: 'DMSans', bodyMed: 'DMSans', bodySemi: 'DMSans', bodyBold: 'DMSans' },
  palette: { teal50: '#E6F7F4', teal700: '#00847B' },
  ICON_COLORS: {}, iconTint: () => '#eee',
}));
jest.mock('@/components/design/widgets', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  const { Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return { Hero: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children), SectionLabel: () => null,
    ServiceTile: ({ label, badge, onPress }: { label: string; badge?: string; onPress: () => void }) =>
      ReactActual.createElement(Pressable, { accessibilityLabel: [label, badge].filter(Boolean).join(', '), onPress, testID: 'service-tile' }) };
});
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text, View, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  const Container = ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children);
  // The Screen's header (Home's frozen top) renders above its children, as in the app.
  const Screen = ({ header, children, onRefresh }: { header?: ReactNode; children: ReactNode; onRefresh?: () => void }) => ReactActual.createElement(View, null,
    ReactActual.createElement(View, { testID: 'frozen-top' }, header),
    ReactActual.createElement(View, { testID: 'scrolls', ...({ onRefresh } as object) }, children));
  // A sheet shows its title and contents only while open, as the app's pop-up does.
  const Sheet = ({ open, title, children }: { open: boolean; title?: string; children: ReactNode }) => open
    ? ReactActual.createElement(View, { testID: 'sheet' }, ReactActual.createElement(Text, null, title), children) : null;
  return { Screen, Card: Container, NText: Text, Sheet, TxnRow: () => null,
    Btn: ({ label, ...props }: any) => ReactActual.createElement(Pressable, { ...props, accessibilityLabel: label }),
    money: (value: number) => `₦${value}`, settledTransactionTotal: () => 0 };
});


// Home design A: savings and loans show only when the server says they are open.
const text = (tree: renderer.ReactTestRenderer) => JSON.stringify(tree.toJSON());
const labels = (tree: renderer.ReactTestRenderer) =>
  tree.root.findAll((n) => typeof n.props.accessibilityLabel === 'string' && typeof n.props.onPress === 'function')
    .map((n) => n.props.accessibilityLabel);

it('keeps savings and loans out while the server has them off', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.savings.rates
    ? { product_available: false, rates: [] } : { product_available: false });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Home />); });
  expect(labels(tree)).toEqual(expect.arrayContaining(['Electricity', 'Save', 'Exams', 'More']));
  expect(labels(tree)).not.toContain('Loan');
  expect(labels(tree)).not.toContain('Fixed Save');
  expect(text(tree)).not.toContain('Bank on WhatsApp');
  expect(text(tree)).toContain('Zitch account');
  act(() => tree.unmount());
});

it('shows the Loan tile and the Fixed Save card, at the best server rate, when both are open', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.savings.rates
    ? { product_available: true, rates: [{ days: 30, rate: '0.12' }, { days: 365, rate: '0.22' }] }
    : { product_available: true });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Home />); });
  expect(labels(tree)).toEqual(expect.arrayContaining(['Loan', 'Fixed Save']));
  expect(labels(tree)).not.toContain('Electricity');
  expect(text(tree)).toContain('Fixed Save · up to 22% p.a');
  expect(text(tree)).toContain('HOT');
  act(() => tree.unmount());
});

it('falls back to the products-off home when the lookups fail', async () => {
  mockApiJson.mockRejectedValue(new Error('offline'));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Home />); });
  expect(labels(tree)).toContain('Electricity');
  expect(labels(tree)).not.toContain('Fixed Save');
  act(() => tree.unmount());
});

it('keeps the greeting, balance card and quick actions still, and scrolls and refreshes only what is below', async () => {
  mockApiJson.mockImplementation(async () => ({ product_available: false, rates: [] }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Home />); });
  const part = (id: string) => tree.root.findByProps({ testID: id });
  const words = (id: string) => part(id).findAll((n) => typeof n.props.children === 'string').map((n) => n.props.children).join(' | ');
  // 17 Nov 2026: this side of Home never scrolls
  for (const fixed of ['AVAILABLE BALANCE', 'Wallet', 'Transfer', 'Add Money', 'Statement', 'History']) expect(words('frozen-top')).toContain(fixed);
  expect(words('frozen-top')).not.toContain('Pay a bill');
  expect(words('scrolls')).toContain('Pay a bill');
  expect(words('scrolls')).not.toContain('AVAILABLE BALANCE');
  // and the part that scrolls is the one that pulls down to refresh
  expect(typeof tree.root.findByProps({ testID: 'scrolls' }).props.onRefresh).toBe('function');
  act(() => tree.unmount());
});

it('opens More as a pop-up of Transfer, History, Statement and every bill payment, not a page', async () => {
  jest.useFakeTimers();
  const { router } = jest.requireMock('expo-router');
  mockApiJson.mockImplementation(async (path: string) => path === EP.savings.rates
    ? { product_available: false, rates: [] } : { product_available: false });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Home />); });
  router.push.mockClear();
  expect(tree.root.findAllByProps({ testID: 'sheet' })).toHaveLength(0);
  await act(async () => { tree.root.findAll((n) => n.props.accessibilityLabel === 'More' && typeof n.props.onPress === 'function')[0].props.onPress(); });
  expect(router.push).not.toHaveBeenCalled();
  const sheets = tree.root.findAll((n) => n.props.testID === 'sheet' && typeof n.type === 'string');
  expect(sheets).toHaveLength(1);
  const inSheet = sheets[0].findAll((n) => n.props.testID === 'service-tile' && typeof n.type === 'string').map((n) => n.props.accessibilityLabel);
  expect(inSheet.slice(0, 3)).toEqual(['Transfer', 'History', 'Statement']);
  expect(inSheet).toEqual(expect.arrayContaining(['Airtime', 'Data', 'Cable TV', 'Electricity', 'Betting', 'Exams', 'Water, Soon', 'Remita', 'Movies', 'Insurance']));
  expect(JSON.stringify(sheets[0].findAllByType(RNText).map((n) => n.props.children))).toContain('Pay a bill');
  // a tap closes the pop-up, then opens the service
  await act(async () => { sheets[0].findAll((n) => n.props.accessibilityLabel === 'Transfer' && typeof n.props.onPress === 'function')[0].props.onPress(); });
  expect(tree.root.findAll((n) => n.props.testID === 'sheet')).toHaveLength(0);
  await act(async () => { jest.advanceTimersByTime(300); });
  expect(router.push).toHaveBeenCalledWith('/sendmoney');
  act(() => tree.unmount());
  jest.useRealTimers();
});
