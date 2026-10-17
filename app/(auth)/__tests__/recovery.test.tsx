import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import ForgotPassword from '@/app/(auth)/forgotpassword';
import ResetPassword from '@/app/(auth)/resetpassword';

const mockPost = jest.fn();
const mockPush = jest.fn();
const mockReplace = jest.fn();
const mockStoreSession = jest.fn();
let mockGeneration = 0;
let mockParams: Record<string, unknown> = {};
jest.mock('expo-router', () => ({
  router: { push: (...args: unknown[]) => mockPush(...args), replace: (...args: unknown[]) => mockReplace(...args), back: jest.fn() },
  useLocalSearchParams: () => mockParams,
}));
jest.mock('@/lib/api', () => ({ publicPost: (...args: unknown[]) => mockPost(...args) }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockGeneration, storeSession: (...args: unknown[]) => mockStoreSession(...args) }));
jest.mock('@/lib/session', () => ({ unlockSession: jest.fn() }));
jest.mock('@/lib/notifications', () => ({ registerForPushNotifications: async () => {} }));
jest.mock('@/lib/screenCapture', () => ({ usePinScreenProtection: jest.fn() }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Brand', () => ({ ZMark: () => null }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: {} }), font: {} }));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View, TextInput, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => R.createElement(View, null, children),
    Header: () => null,
    Field: (props: any) => R.createElement(TextInput, { ...props, accessibilityLabel: props.label }),
    Btn: (props: any) => R.createElement(Pressable, { ...props, accessibilityLabel: props.label }),
  };
});
const field = (tree: renderer.ReactTestRenderer, label: string) => tree.root.findByProps({ accessibilityLabel: label });
beforeEach(() => {
  jest.clearAllMocks();
  mockGeneration = 0;
  mockParams = {};
  mockPost.mockResolvedValue({ ok: true, json: async () => ({}) });
});

it.each(['words08012345678', '0801234567890123456', 'ada@example.com extra'])('rejects malformed recovery identifier %s without dispatch', async (ident) => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<ForgotPassword />); });
  await act(async () => { field(tree, 'Email or phone').props.onChangeText(ident); });
  expect(field(tree, 'Send reset code').props.disabled).toBe(true);
  await act(async () => { await field(tree, 'Email or phone').props.onSubmitEditing(); });
  expect(mockPost).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('requests a reset once and preserves the trimmed account identifier', async () => {
  let finish!: (value: unknown) => void;
  mockPost.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<ForgotPassword />); });
  await act(async () => { field(tree, 'Email or phone').props.onChangeText(' ada@example.com '); });
  let sending!: Promise<void>;
  act(() => {
    const button = field(tree, 'Send reset code');
    sending = button.props.onPress();
    button.props.onPress();
  });
  expect(mockPost).toHaveBeenCalledTimes(1);
  await act(async () => { finish({ ok: true }); await sending; });
  expect(mockPush).toHaveBeenCalledWith({ pathname: '/resetpassword', params: { ident: 'ada@example.com' } });
  act(() => tree.unmount());
});

it('does not open recovery over a newer signed-in session', async () => {
  let finish!: (value: unknown) => void;
  mockPost.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<ForgotPassword />); });
  await act(async () => { field(tree, 'Email or phone').props.onChangeText('08012345678'); });
  let sending!: Promise<void>;
  act(() => { sending = field(tree, 'Send reset code').props.onPress(); });
  mockGeneration += 1;
  await act(async () => { finish({ ok: true }); await sending; });
  expect(mockPush).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it.each([undefined, ['a@example.com', 'b@example.com']])('provides a recovery path for a missing or ambiguous target', async (ident) => {
  mockParams = { ident };
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<ResetPassword />); });
  expect(tree.root.findAllByProps({ accessibilityLabel: 'Reset code' })).toHaveLength(0);
  act(() => { field(tree, 'Request reset code').props.onPress(); });
  expect(mockReplace).toHaveBeenCalledWith('/forgotpassword');
  expect(mockPost).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('discards a delayed reset result when the recovery link changes account', async () => {
  mockParams = { ident: 'first@example.com' };
  let finish!: (value: unknown) => void;
  mockPost.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<ResetPassword />); });
  await act(async () => { field(tree, 'Reset code').props.onChangeText('654321'); });
  await act(async () => { field(tree, 'New password').props.onChangeText('Password123!'); });
  await act(async () => { field(tree, 'Confirm password').props.onChangeText('Password123!'); });
  let resetting!: Promise<void>;
  act(() => { resetting = field(tree, 'Reset password').props.onPress(); });
  mockParams = { ident: 'second@example.com' };
  await act(async () => { tree.update(<ResetPassword />); });
  expect(field(tree, 'Reset code').props.value).toBe('');
  await act(async () => { finish({ ok: true, json: async () => ({ access_token: 'old-account' }) }); await resetting; });
  expect(mockStoreSession).not.toHaveBeenCalled();
  expect(mockReplace).not.toHaveBeenCalled();
  act(() => tree.unmount());
});
