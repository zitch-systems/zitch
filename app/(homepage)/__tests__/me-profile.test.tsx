import React, { type ReactNode } from 'react';
import { Pressable } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import Me from '@/app/(homepage)/me';
import { apiPost } from '@/lib/api';
import { clearSession } from '@/lib/secureStore';

const mockPush = jest.fn();
const mockReplace = jest.fn();
let mockProvider = 'partnership';
const mockReload = jest.fn();
const mockGetStatus = jest.fn().mockResolvedValue({ success: true, tier: 1 });
const mockSetShowBal = jest.fn();
let mockGeneration = 0;
const mockAuthenticate = jest.fn();
const mockSetBiometricEnabled = jest.fn();
jest.mock('expo-router', () => ({
  router: { push: (...args: unknown[]) => mockPush(...args), replace: (...args: unknown[]) => mockReplace(...args) },
  useFocusEffect: (callback: () => void) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    ReactActual.useEffect(callback, [callback]);
  },
}));
jest.mock('@/lib/wallet', () => ({ useWallet: () => ({
  totalBalance: 0, firstName: 'Ada', showBal: true, setShowBal: mockSetShowBal, fundingProvider: mockProvider, reload: mockReload,
}) }));
jest.mock('@/lib/api', () => ({ apiPost: jest.fn() }));
jest.mock('@/lib/services/kyc', () => ({ kycService: { getStatus: () => mockGetStatus() } }));
jest.mock('@/lib/secureStore', () => ({ getToken: async () => 'session', getSessionGeneration: () => mockGeneration, clearSession: jest.fn() }));
jest.mock('@/lib/biometrics', () => ({
  isBiometricEnabled: async () => false,
  isBiometricAvailable: async () => true,
  authenticate: (...args: unknown[]) => mockAuthenticate(...args),
  setBiometricEnabled: (...args: unknown[]) => mockSetBiometricEnabled(...args),
}));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Brand', () => ({ Avatar: () => null }));
jest.mock('@/components/design/WhatsAppGlyph', () => ({ WhatsAppGlyph: () => null }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/widgets', () => ({ Hero: ({ children }: { children: ReactNode }) => children }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: {}, theme: 'light', setTheme: jest.fn() }), font: {},
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text, View, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Card: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    NText: Text, money: String,
    ZItem: ({ title, sub, onPress }: { title: string; sub?: string; onPress: () => void }) =>
      ReactActual.createElement(Pressable, { accessibilityLabel: title, onPress },
        ReactActual.createElement(Text, null, sub)),
  };
});

beforeEach(() => {
  jest.clearAllMocks();
  mockGeneration = 0;
  mockAuthenticate.mockReset().mockResolvedValue(true);
  mockSetBiometricEnabled.mockReset().mockResolvedValue(undefined);
  (apiPost as jest.Mock).mockReset();
  (clearSession as jest.Mock).mockReset().mockImplementation(async () => { mockGeneration += 1; });
});

it('shows Tier 0, gives the balance eye an action and opens linked accounts', async () => {
  mockGetStatus.mockResolvedValueOnce({ success: true, tier: 0 });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Me />); });
  expect(JSON.stringify(tree.toJSON())).toContain('Tier 0');
  expect(JSON.stringify(tree.toJSON())).not.toContain('Tier unavailable');
  act(() => tree.root.findByProps({ accessibilityLabel: 'Hide balance' }).props.onPress());
  expect(mockSetShowBal).toHaveBeenCalledWith(false);
  act(() => tree.root.findByProps({ accessibilityLabel: 'Linked bank accounts' }).props.onPress());
  expect(mockPush).toHaveBeenCalledWith('/wallet');
  // USSD, BizPayment and Junior are not on Me any more (review, Nov 2026)
  for (const gone of ['Zitch USSD', 'My BizPayment', 'Zitch Junior']) expect(JSON.stringify(tree.toJSON())).not.toContain(gone);
  act(() => tree.unmount());
});

it('lays the menu straight on the page: no white cards, only the WhatsApp card keeps one', async () => {
  mockGetStatus.mockResolvedValueOnce({ success: true, tier: 2 });
  const ui = jest.requireMock('@/components/design/ui');
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Me />); });
  const rows = tree.root.findAll((n) => n.type === ui.ZItem);
  expect(rows.length).toBeGreaterThanOrEqual(8);
  expect(rows.every((r) => r.props.bare === true)).toBe(true);
  const cards = tree.root.findAll((n) => n.type === ui.Card);
  expect(cards).toHaveLength(1);
  expect(cards[0].findAll((n) => n.props.children === 'Bank on WhatsApp').length).toBeGreaterThan(0);
  act(() => tree.unmount());
});

it('links Security Center once and does not list the transaction PIN again beside it', async () => {
  mockGetStatus.mockResolvedValueOnce({ success: true, tier: 2 });
  const ui = jest.requireMock('@/components/design/ui');
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Me />); });
  const titles = tree.root.findAll((n) => n.type === ui.ZItem).map((r) => r.props.title);
  expect(new Set(titles).size).toBe(titles.length);
  expect(titles.filter((t) => t === 'Security Center')).toHaveLength(1);
  expect(titles).not.toContain('Change Transaction PIN');
  const security = tree.root.findAll((n) => n.type === ui.ZItem && n.props.title === 'Security Center')[0];
  expect(security.props.sub).toBe('Password, transaction PIN and biometrics');
  act(() => tree.unmount());
});

it('does not present an invalid or unsuccessful tier response as verified', async () => {
  mockGetStatus.mockResolvedValueOnce({ success: false, tier: 3 });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Me />); });
  expect(JSON.stringify(tree.toJSON())).toContain('Tier unavailable');
  expect(JSON.stringify(tree.toJSON())).not.toContain('Tier 3');
  act(() => tree.unmount());
});

it('keeps the profile focused on the current account and verification', async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Me />); });
  expect(tree.root.findAllByType(Pressable).some((node) => /VAS/.test(node.props.accessibilityLabel || ''))).toBe(false);
  expect(tree.root.findByProps({ accessibilityLabel: 'Log out' })).toBeTruthy();
  act(() => tree.unmount());
});

it('bounds profile logout and clears the device after an offline response with duplicate taps suppressed', async () => {
  let reject!: (reason: Error) => void;
  (apiPost as jest.Mock).mockReturnValueOnce(new Promise((_resolve, rejectPromise) => { reject = rejectPromise; }));
  (clearSession as jest.Mock).mockClear();
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Me />); });
  const logout = tree.root.findByProps({ accessibilityLabel: 'Log out' });
  await act(async () => { void logout.props.onPress(); void logout.props.onPress(); });
  expect(apiPost).toHaveBeenCalledTimes(1);
  expect(apiPost).toHaveBeenCalledWith('/api/logout/', {}, 5000);
  await act(async () => { reject(new Error('offline')); });
  expect(clearSession).toHaveBeenCalledTimes(1);
  expect(mockReplace).toHaveBeenCalledWith('/signin');
  act(() => tree.unmount());
});

it.each(['resolve', 'reject'] as const)('does not clear a newer account when an old logout request later %ss', async (outcome) => {
  let resolve!: (value: unknown) => void;
  let reject!: (reason: Error) => void;
  (apiPost as jest.Mock).mockReturnValueOnce(new Promise((done, fail) => { resolve = done; reject = fail; }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Me />); });
  await act(async () => { void tree.root.findByProps({ accessibilityLabel: 'Log out' }).props.onPress(); });
  mockGeneration += 1;
  await act(async () => { if (outcome === 'resolve') resolve({ ok: true }); else reject(new Error('Session changed')); });
  expect(clearSession).not.toHaveBeenCalled();
  expect(mockReplace).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('does not apply logout cleanup from an unmounted screen', async () => {
  let resolve!: (value: unknown) => void;
  (apiPost as jest.Mock).mockReturnValueOnce(new Promise(done => { resolve = done; }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Me />); });
  await act(async () => { void tree.root.findByProps({ accessibilityLabel: 'Log out' }).props.onPress(); });
  act(() => tree.unmount());
  await act(async () => { resolve({ ok: true }); });
  expect(clearSession).not.toHaveBeenCalled();
  expect(mockReplace).not.toHaveBeenCalled();
});

it('does not redirect a new login that arrives during old-session storage cleanup', async () => {
  let resolve!: () => void;
  (apiPost as jest.Mock).mockResolvedValueOnce({ ok: true });
  (clearSession as jest.Mock).mockImplementationOnce(() => {
    mockGeneration += 1;
    return new Promise<void>(done => { resolve = done; });
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Me />); });
  await act(async () => { void tree.root.findByProps({ accessibilityLabel: 'Log out' }).props.onPress(); });
  expect(clearSession).toHaveBeenCalledTimes(1);
  mockGeneration += 1;
  await act(async () => { resolve(); });
  expect(mockReplace).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('repeats no setting on Me: biometrics and dark mode live in Security Center and Settings', async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Me />); });
  expect(tree.root.findAll((n) => n.props.accessibilityRole === 'switch')).toHaveLength(0);
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Biometric sign-in' })).toHaveLength(0);
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Dark mode' })).toHaveLength(0);
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Settings' }).length).toBeGreaterThan(0);
  act(() => tree.unmount());
});
