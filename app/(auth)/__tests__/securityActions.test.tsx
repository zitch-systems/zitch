import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import SetPassword from '@/app/(auth)/setpassword';
import ResetPassword from '@/app/(auth)/resetpassword';
import SetThumbprint from '@/app/(auth)/setthumbprint';

const mockPost = jest.fn();
const mockSaveRefresh = jest.fn();
const mockSavePin = jest.fn();
const mockStoreSession = jest.fn();
const mockUnlock = jest.fn();
const mockReplace = jest.fn();
const mockBack = jest.fn();
const mockEnablePay = jest.fn();
const mockParams: { change?: string; ident?: string } = {};

jest.mock('expo-router', () => ({
  router: { replace: (...args: unknown[]) => mockReplace(...args), back: () => mockBack() },
  useLocalSearchParams: () => mockParams,
  Link: ({ children }: { children: ReactNode }) => children,
}));
jest.mock('@/lib/api', () => ({ apiPost: (...args: unknown[]) => mockPost(...args), publicPost: (...args: unknown[]) => mockPost(...args) }));
jest.mock('@/lib/secureStore', () => ({
  saveRefreshToken: (...args: unknown[]) => mockSaveRefresh(...args),
  saveTransactionPin: (...args: unknown[]) => mockSavePin(...args),
  storeSession: (...args: unknown[]) => mockStoreSession(...args),
}));
jest.mock('@/lib/session', () => ({ unlockSession: () => mockUnlock() }));
jest.mock('@/lib/screenCapture', () => ({ usePinScreenProtection: jest.fn() }));
jest.mock('@/components/AuthGuard', () => ({ __esModule: true, default: ({ children }: { children: ReactNode }) => children }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/Brand', () => ({ ZMark: () => null }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/biometrics', () => ({
  isBiometricAvailable: async () => true,
  biometricLabel: async () => 'fingerprint',
  isBiometricEnabled: async () => false,
  isBiometricTxnEnabled: async () => false,
  setBiometricTxnEnabled: (...args: unknown[]) => mockEnablePay(...args),
}));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#090', ink1: '#111', ink3: '#333', surface: '#fff' } }),
  font: { bold: 'bold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View, Text, TextInput, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => R.createElement(View, null, children),
    Card: ({ children }: { children: ReactNode }) => R.createElement(View, null, children),
    Header: () => null,
    Field: (props: any) => R.createElement(TextInput, { ...props, accessibilityLabel: props.label }),
    Btn: (props: any) => R.createElement(Pressable, { ...props, accessibilityLabel: props.label }, R.createElement(Text, null, props.label)),
    Toggle: (props: any) => R.createElement(Pressable, { accessibilityLabel: 'toggle', onPress: () => props.onChange(true) }),
    ZItem: ({ title, right }: any) => R.createElement(View, { accessibilityLabel: title }, right),
    PinSheet: (props: any) => props.open ? R.createElement(View, { testID: 'pin-sheet', ...props }) : null,
  };
});
const field = (tree: renderer.ReactTestRenderer, name: string) => tree.root.findByProps({ accessibilityLabel: name });

beforeEach(() => {
  jest.clearAllMocks();
  delete mockParams.change;
  delete mockParams.ident;
  mockPost.mockResolvedValue({ ok: true, json: async () => ({ success: true, refresh_token: 'rotated-refresh' }) });
});

it('changes an existing password with current-password proof and persists the rotated refresh before returning', async () => {
  mockParams.change = '1';
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<SetPassword />); });
  await act(async () => { field(tree, 'Current password').props.onChangeText('Oldpass12!'); });
  await act(async () => { field(tree, 'Password').props.onChangeText('newpass123!'); });
  await act(async () => { field(tree, 'Confirm password').props.onChangeText('newpass123!'); });
  expect(field(tree, 'Change password').props.disabled).toBe(false);
  await act(async () => { await field(tree, 'Change password').props.onPress(); });
  expect(mockPost).toHaveBeenCalledWith('/api/set-password/', { password: 'newpass123!', current_password: 'Oldpass12!' });
  expect(mockSaveRefresh).toHaveBeenCalledWith('rotated-refresh');
  expect(mockSaveRefresh.mock.invocationCallOrder[0]).toBeLessThan(mockBack.mock.invocationCallOrder[0]);
  expect(mockReplace).not.toHaveBeenCalledWith('/setpin');
  act(() => tree.unmount());
});

it('requires the server password composition policy during onboarding', async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<SetPassword />); });
  await act(async () => { field(tree, 'Password').props.onChangeText('Password123'); });
  await act(async () => { field(tree, 'Confirm password').props.onChangeText('Password123'); });
  expect(field(tree, 'Continue').props.disabled).toBe(true);
  act(() => tree.unmount());
});

it('unlocks a previously locked device after password recovery', async () => {
  mockParams.ident = 'ada@example.com';
  mockPost.mockResolvedValue({ ok: true, json: async () => ({ access_token: 'new-access', refresh_token: 'new-refresh' }) });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<ResetPassword />); });
  await act(async () => { field(tree, 'Reset code').props.onChangeText('654321'); });
  await act(async () => { field(tree, 'New password').props.onChangeText('newpass123!'); });
  await act(async () => { field(tree, 'Confirm password').props.onChangeText('newpass123!'); });
  await act(async () => { await field(tree, 'Reset password').props.onPress(); });
  expect(mockStoreSession).toHaveBeenCalledWith({ access_token: 'new-access', refresh_token: 'new-refresh' });
  expect(mockUnlock).toHaveBeenCalledTimes(1);
  expect(mockUnlock.mock.invocationCallOrder[0]).toBeLessThan(mockReplace.mock.invocationCallOrder[0]);
  act(() => tree.unmount());
});

async function openPay() {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<SetThumbprint />); });
  await act(async () => { await field(tree, 'Approve payments').findByProps({ accessibilityLabel: 'toggle' }).props.onPress(); });
  return tree;
}

it('does not cache an incorrect transaction PIN or enable biometric payments', async () => {
  mockPost.mockResolvedValue({ ok: false, json: async () => ({ message: 'Incorrect PIN' }) });
  const tree = await openPay();
  await act(async () => { await tree.root.findByProps({ testID: 'pin-sheet' }).props.onComplete('123456'); });
  expect(mockPost).toHaveBeenCalledWith('/api/verify-transaction-pin/', { pin: '123456' });
  expect(mockSavePin).not.toHaveBeenCalled();
  expect(mockEnablePay).not.toHaveBeenCalled();
  expect(tree.root.findByProps({ testID: 'pin-sheet' }).props.error).toBe('Incorrect PIN');
  act(() => tree.unmount());
});

it('enables biometric payments only after the server verifies the PIN', async () => {
  const tree = await openPay();
  await act(async () => { await tree.root.findByProps({ testID: 'pin-sheet' }).props.onComplete('135790'); });
  expect(mockSavePin).toHaveBeenCalledWith('135790');
  expect(mockPost.mock.invocationCallOrder[0]).toBeLessThan(mockSavePin.mock.invocationCallOrder[0]);
  expect(mockEnablePay).toHaveBeenCalledWith(true);
  act(() => tree.unmount());
});
