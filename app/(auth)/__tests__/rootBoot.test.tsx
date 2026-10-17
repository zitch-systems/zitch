import React, { type ReactNode } from 'react';
import { Linking } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import RootLayout from '@/app/_layout';
import Index from '@/app/index';
import { SplashScreen } from 'expo-router';

const mockGetToken = jest.fn();
const mockRead = jest.fn();
const mockRemove = jest.fn();
const mockReplace = jest.fn();
let mockGeneration = 0;
let mockFontState: [boolean, Error | null] = [false, null];
jest.mock('expo-font', () => ({ useFonts: () => mockFontState }));
jest.mock('expo-status-bar', () => ({ StatusBar: () => null }));
jest.mock('react-native-gesture-handler', () => ({ GestureHandlerRootView: ({ children }: { children: ReactNode }) => children }));
jest.mock('react-native-safe-area-context', () => ({ SafeAreaProvider: ({ children }: { children: ReactNode }) => children, SafeAreaView: ({ children }: { children: ReactNode }) => children }));
jest.mock('@/lib/theme', () => ({
  appFonts: {}, font: { medium: 'Manrope' },
  ThemeProvider: ({ children }: { children: ReactNode }) => children,
  useTheme: () => ({ theme: 'light', c: { bg: '#fff' } }),
}));
jest.mock('@/lib/wallet', () => ({ WalletProvider: ({ children }: { children: ReactNode }) => children }));
jest.mock('@/components/design/Notify', () => ({ NotifyHost: () => null }));
jest.mock('@/lib/session', () => ({}));
jest.mock('@/lib/notifications', () => ({ subscribeToNotificationOpens: () => () => {} }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockGeneration, getToken: () => mockGetToken() }));
jest.mock('@react-native-async-storage/async-storage', () => ({ __esModule: true, default: { getItem: (...args: unknown[]) => mockRead(...args), removeItem: (...args: unknown[]) => mockRemove(...args) } }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/WhatsAppGlyph', () => ({ WhatsAppGlyph: () => null }));
jest.mock('@/components/design/Brand', () => ({ ZMark: () => null }));
jest.mock('@/components/design/Naira', () => ({ NText: ({ children }: { children: ReactNode }) => children }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: ({ children }: { children: ReactNode }) => children }));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return { Btn: (props: any) => R.createElement(Pressable, { ...props, accessibilityLabel: props.label }) };
});
jest.mock('@/lib/biometrics', () => ({ reconcileCachedPin: async () => {} }));
jest.mock('@/lib/pendingApproval', () => ({ rememberWhatsAppApprovalUrl: async () => {} }));
jest.mock('expo-router', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  const Stack = ({ children }: { children: ReactNode }) => R.createElement(View, { testID: 'app-navigation' }, children);
  Stack.Screen = () => null;
  const navigation = { isReady: () => false, addListener: () => () => {} };
  return { Stack, router: { replace: (...args: unknown[]) => mockReplace(...args) }, usePathname: () => '/', useNavigationContainerRef: () => navigation,
    SplashScreen: { preventAutoHideAsync: jest.fn().mockResolvedValue(undefined), hideAsync: jest.fn().mockResolvedValue(undefined) } };
});

beforeEach(() => {
  jest.useFakeTimers();
  mockFontState = [false, null];
  mockGeneration = 0;
  mockGetToken.mockReset().mockResolvedValue(null);
  mockRead.mockReset().mockResolvedValue(null);
  mockRemove.mockReset().mockResolvedValue(undefined);
  mockReplace.mockReset();
  jest.spyOn(Linking, 'getInitialURL').mockResolvedValue(null);
  jest.spyOn(Linking, 'addEventListener').mockReturnValue({ remove: jest.fn() } as any);
  (SplashScreen.hideAsync as jest.Mock).mockClear();
});
afterEach(() => { jest.useRealTimers(); jest.restoreAllMocks(); });

it('renders navigation and hides the native splash even when fonts fail', async () => {
  mockFontState = [false, new Error('missing font')];
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<RootLayout />); });
  expect(tree.root.findByProps({ testID: 'app-navigation' })).toBeTruthy();
  expect(SplashScreen.hideAsync).toHaveBeenCalledTimes(1);
  act(() => tree.unmount());
});

it('opens the app after four seconds if font loading never settles', async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<RootLayout />); });
  expect(tree.toJSON()).toBeNull();
  expect(SplashScreen.hideAsync).not.toHaveBeenCalled();
  await act(async () => { jest.advanceTimersByTime(4000); });
  expect(tree.root.findByProps({ testID: 'app-navigation' })).toBeTruthy();
  expect(SplashScreen.hideAsync).toHaveBeenCalledTimes(1);
  act(() => tree.unmount());
});


it('routes an existing session to authentication before showing introduction', async () => {
  mockGetToken.mockResolvedValue('saved-session');
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Index />); });
  expect(mockReplace).toHaveBeenCalledWith('/signin');
  expect(mockRead).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('resumes a recent pending phone verification', async () => {
  mockRead.mockResolvedValue(String(Date.now() - 60_000));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Index />); });
  expect(mockReplace).toHaveBeenCalledWith('/otp');
  expect(mockRemove).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it.each(['expired', 'future', 'malformed'])('clears an invalid %s OTP marker and offers introduction', async (kind) => {
  mockRead.mockResolvedValue(kind === 'expired' ? String(Date.now() - 3_600_000)
    : kind === 'future' ? String(Date.now() + 60_000) : 'not-a-time');
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Index />); });
  expect(mockRemove).toHaveBeenCalledWith('otpPending');
  expect(mockReplace).not.toHaveBeenCalled();
  expect(tree.root.findByProps({ accessibilityLabel: 'Skip introduction' })).toBeTruthy();
  act(() => tree.unmount());
});

it.each(['unmount', 'new session'])('does not navigate from a delayed boot read after %s', async (reason) => {
  let finish!: (value: string) => void;
  mockGetToken.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Index />); });
  if (reason === 'unmount') act(() => tree.unmount());
  else mockGeneration += 1;
  await act(async () => { finish('old-session'); });
  expect(mockReplace).not.toHaveBeenCalled();
  expect(mockRead).not.toHaveBeenCalled();
  if (reason === 'new session') act(() => tree.unmount());
});

it('does not resume an OTP marker read for a previous session', async () => {
  let finish!: (value: string) => void;
  mockRead.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Index />); });
  mockGeneration += 1;
  await act(async () => { finish(String(Date.now())); });
  expect(mockReplace).not.toHaveBeenCalled();
  expect(mockRemove).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('offers sign-in if device startup storage cannot be read', async () => {
  mockGetToken.mockRejectedValue(new Error('keychain unavailable'));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Index />); });
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Skip introduction' }).props.onPress(); });
  expect(mockReplace).toHaveBeenCalledWith('/signin');
  act(() => tree.unmount());
});

it('advances the introduction and starts registration only from its final page', async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Index />); });
  expect(tree.root.findByProps({ accessibilityLabel: 'Introduction 1 of 3' })).toBeTruthy();
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Next' }).props.onPress(); });
  expect(tree.root.findByProps({ accessibilityLabel: 'Introduction 2 of 3' })).toBeTruthy();
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Next' }).props.onPress(); });
  expect(mockReplace).not.toHaveBeenCalled();
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Get started' }).props.onPress(); });
  expect(mockReplace).toHaveBeenCalledWith('/register');
  act(() => tree.unmount());
});
