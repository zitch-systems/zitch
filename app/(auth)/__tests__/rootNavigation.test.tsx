import React, { type ReactNode } from 'react';
import { AppState, Linking, type AppStateStatus } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import RootLayout from '@/app/_layout';
import { store } from 'expo-router/build/global-state/router-store';
import { router } from 'expo-router';
import { enforceHardExpiry } from '@/lib/session';

let mockStackRenders = 0;
jest.mock('expo-font', () => ({ useFonts: () => [true, null] }));
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
jest.mock('@/lib/session', () => ({
  enforceHardExpiry: jest.fn(async () => false),
  lockIfAwayTooLong: jest.fn(async () => false),
  enforceIdleTimeout: jest.fn(async () => false),
  isSessionLocked: jest.fn(async () => false),
  isExternalActivityActive: () => false,
}));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => 0 }));
jest.mock('@/lib/biometrics', () => ({ reconcileCachedPin: async () => {} }));
jest.mock('@/lib/pendingApproval', () => ({ rememberWhatsAppApprovalUrl: async () => {} }));
jest.mock('expo-router', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  // Exercise the installed Expo Router hooks and its real external store. Only
  // the native stack view is replaced; auth/session work stays deterministic.
  const hooks = jest.requireActual('expo-router/build/hooks');
  const Stack = ({ children }: { children: ReactNode }) => {
    if (++mockStackRenders > 30) throw new Error('Unbounded root navigation renders');
    return R.createElement(View, { testID: 'app-navigation' }, children);
  };
  Stack.Screen = () => null;
  return { ...hooks, Stack, router: { replace: jest.fn() },
    SplashScreen: { preventAutoHideAsync: jest.fn().mockResolvedValue(undefined), hideAsync: jest.fn().mockResolvedValue(undefined) } };
});

let navigationReady: boolean;
let navigationListeners: Set<() => void>;
let appStateListener: (state: AppStateStatus) => void;
let tree: renderer.ReactTestRenderer | undefined;

// React Navigation rehydrates a newly mounted nested navigator on read before
// its child state has been stored on the parent. Its identity changes while the
// active route stays the same. This reproduces that boundary without a device.
const freshNestedState = () => ({
  stale: false as const, type: 'stack', key: 'root-stack', index: 0,
  routeNames: ['index', '(auth)'], routes: [{ key: 'auth-route', name: '(auth)', state: {
    stale: false as const, type: 'stack', key: 'auth-stack', index: 0,
    routeNames: ['signin'], routes: [{ key: 'signin-route', name: 'signin' }],
  } }],
});

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  mockStackRenders = 0;
  navigationReady = true;
  navigationListeners = new Set();
  store.rootStateSubscribers.clear();
  store.storeSubscribers.clear();
  store.rootState = undefined;
  store.nextState = undefined;
  store.linking = { config: { screens: { index: '', '(auth)': { path: '(auth)', screens: { signin: 'signin' } } } } } as any;
  store.routeInfo = store.getRouteInfo(freshNestedState() as any);
  store.navigationRef = {
    isReady: () => navigationReady,
    getRootState: freshNestedState,
    addListener: (_name: string, callback: () => void) => {
      navigationListeners.add(callback);
      return () => navigationListeners.delete(callback);
    },
  } as any;
  jest.spyOn(Linking, 'getInitialURL').mockResolvedValue(null);
  jest.spyOn(Linking, 'addEventListener').mockReturnValue({ remove: jest.fn() } as any);
  jest.spyOn(AppState, 'addEventListener').mockImplementation((_name, callback) => {
    appStateListener = callback;
    return { remove: jest.fn() } as any;
  });
});

afterEach(() => {
  if (tree) act(() => tree?.unmount());
  tree = undefined;
  store.rootStateSubscribers.clear();
  store.storeSubscribers.clear();
  jest.useRealTimers();
  jest.restoreAllMocks();
});

it('settles when a nested navigator returns fresh root snapshots for the same route', async () => {
  await act(async () => { tree = renderer.create(<RootLayout />); });
  expect(tree!.root.findByProps({ testID: 'app-navigation' })).toBeTruthy();
  expect(mockStackRenders).toBeLessThan(5);
  expect(enforceHardExpiry).toHaveBeenCalledTimes(1);
  await act(async () => { jest.advanceTimersByTime(30_000); });
  expect(enforceHardExpiry).toHaveBeenCalledTimes(2);
  expect(router.replace).not.toHaveBeenCalled();
});

it('starts enforcement when navigation becomes ready and retains foreground checks', async () => {
  navigationReady = false;
  await act(async () => { tree = renderer.create(<RootLayout />); });
  expect(enforceHardExpiry).not.toHaveBeenCalled();
  await act(async () => {
    navigationReady = true;
    navigationListeners.forEach((callback) => callback());
  });
  expect(enforceHardExpiry).toHaveBeenCalledTimes(1);
  await act(async () => { appStateListener('active'); });
  expect(enforceHardExpiry).toHaveBeenCalledTimes(2);
  act(() => { tree?.unmount(); tree = undefined; });
  expect(navigationListeners.size).toBe(0);
});
