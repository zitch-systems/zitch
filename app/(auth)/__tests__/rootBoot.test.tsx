import React, { type ReactNode } from 'react';
import { Linking } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import RootLayout from '@/app/_layout';
import { SplashScreen } from 'expo-router';

let mockFontState: [boolean, Error | null] = [false, null];
jest.mock('expo-font', () => ({ useFonts: () => mockFontState }));
jest.mock('expo-status-bar', () => ({ StatusBar: () => null }));
jest.mock('react-native-gesture-handler', () => ({ GestureHandlerRootView: ({ children }: { children: ReactNode }) => children }));
jest.mock('react-native-safe-area-context', () => ({ SafeAreaProvider: ({ children }: { children: ReactNode }) => children }));
jest.mock('@/lib/theme', () => ({
  appFonts: {}, font: { medium: 'Manrope' },
  ThemeProvider: ({ children }: { children: ReactNode }) => children,
  useTheme: () => ({ theme: 'light', c: { bg: '#fff' } }),
}));
jest.mock('@/lib/wallet', () => ({ WalletProvider: ({ children }: { children: ReactNode }) => children }));
jest.mock('@/components/design/Notify', () => ({ NotifyHost: () => null }));
jest.mock('@/lib/session', () => ({}));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => 0 }));
jest.mock('@/lib/biometrics', () => ({ reconcileCachedPin: async () => {} }));
jest.mock('@/lib/pendingApproval', () => ({ rememberWhatsAppApprovalUrl: async () => {} }));
jest.mock('expo-router', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  const Stack = ({ children }: { children: ReactNode }) => R.createElement(View, { testID: 'app-navigation' }, children);
  Stack.Screen = () => null;
  const navigation = { isReady: () => false, addListener: () => () => {} };
  return { Stack, usePathname: () => '/', useNavigationContainerRef: () => navigation,
    SplashScreen: { preventAutoHideAsync: jest.fn().mockResolvedValue(undefined), hideAsync: jest.fn().mockResolvedValue(undefined) } };
});

beforeEach(() => {
  jest.useFakeTimers();
  mockFontState = [false, null];
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
