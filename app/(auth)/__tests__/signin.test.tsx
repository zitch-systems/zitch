import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import Signin from '@/app/(auth)/signin';

const mockPublicPost = jest.fn();
const mockStoreSession = jest.fn();
const mockRegisterPush = jest.fn();
const mockReplace = jest.fn();
const mockGetToken = jest.fn();
const mockBioEnabled = jest.fn();
const mockAuthenticate = jest.fn();
const calls: string[] = [];

jest.mock('expo-router', () => ({
  router: { replace: (...args: unknown[]) => mockReplace(...args), push: jest.fn() },
  Link: ({ children }: { children: ReactNode }) => children,
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true, default: { setItem: jest.fn(async () => {}), getItem: jest.fn(async () => null) },
}));
jest.mock('@/lib/api', () => ({ publicPost: (...args: unknown[]) => mockPublicPost(...args) }));
jest.mock('@/lib/secureStore', () => ({
  storeSession: (...args: unknown[]) => { calls.push('storeSession'); return mockStoreSession(...args); },
  getToken: () => mockGetToken(),
  getRememberedIdentifier: async () => '',
  rememberIdentifier: async () => {},
}));
jest.mock('@/lib/session', () => ({
  enforceHardExpiry: async () => false,
  unlockSession: async () => { calls.push('unlockSession'); },
}));
jest.mock('@/lib/pendingApproval', () => ({ pendingWhatsAppApproval: async () => '' }));
jest.mock('@/lib/notifications', () => ({
  registerForPushNotifications: (...args: unknown[]) => { calls.push('registerPush'); return mockRegisterPush(...args); },
}));
jest.mock('@/lib/biometrics', () => ({
  isBiometricAvailable: async () => true,
  isBiometricEnabled: () => mockBioEnabled(),
  authenticate: (...args: unknown[]) => mockAuthenticate(...args),
}));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Brand', () => ({ ZMark: () => null }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/widgets', () => ({ Hero: ({ children }: { children: ReactNode }) => children }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { ink1: '#111', ink3: '#333', brand: '#090', line: '#ddd' } }),
  font: { extrabold: 'x', regular: 'r', semibold: 's', bold: 'b' },
}));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View, TextInput, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => R.createElement(View, null, children),
    Field: (props: any) => R.createElement(TextInput, { ...props, accessibilityLabel: props.label }),
    Btn: (props: any) => R.createElement(Pressable, { onPress: props.onPress, accessibilityLabel: props.label }),
  };
});

const field = (tree: renderer.ReactTestRenderer, label: string) => tree.root.findByProps({ accessibilityLabel: label });

beforeEach(() => {
  jest.clearAllMocks();
  calls.length = 0;
  mockGetToken.mockResolvedValue(null);
  mockBioEnabled.mockResolvedValue(false);
  mockRegisterPush.mockResolvedValue('registered');
  mockPublicPost.mockResolvedValue({ ok: true, json: async () => ({ access_token: 'access', refresh_token: 'refresh' }) });
});

it('binds the push token to the account that just signed in, without prompting', async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Signin />); });
  await act(async () => { field(tree, 'Email or phone').props.onChangeText('b@zitch.test'); });
  await act(async () => { field(tree, 'Password').props.onChangeText('secret-pass'); });
  await act(async () => { await field(tree, 'Sign in').props.onPress(); });
  expect(mockRegisterPush).toHaveBeenCalledWith(false);
  // Only once the new session is stored and unlocked, so the token binds to it.
  expect(calls.indexOf('registerPush')).toBeGreaterThan(calls.indexOf('storeSession'));
  expect(calls.indexOf('registerPush')).toBeGreaterThan(calls.indexOf('unlockSession'));
  expect(mockReplace).toHaveBeenCalledWith('/home');
});

it('still signs in when push registration fails', async () => {
  mockRegisterPush.mockRejectedValue(new Error('no network'));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Signin />); });
  await act(async () => { field(tree, 'Email or phone').props.onChangeText('b@zitch.test'); });
  await act(async () => { field(tree, 'Password').props.onChangeText('secret-pass'); });
  await act(async () => { await field(tree, 'Sign in').props.onPress(); });
  expect(mockReplace).toHaveBeenCalledWith('/home');
});

it('does not register for a failed sign-in', async () => {
  mockPublicPost.mockResolvedValue({ ok: false, json: async () => ({ message: 'Incorrect Details' }) });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Signin />); });
  await act(async () => { field(tree, 'Email or phone').props.onChangeText('b@zitch.test'); });
  await act(async () => { field(tree, 'Password').props.onChangeText('wrong-pass'); });
  await act(async () => { await field(tree, 'Sign in').props.onPress(); });
  expect(mockRegisterPush).not.toHaveBeenCalled();
});
