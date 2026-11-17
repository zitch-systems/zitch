import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import OTPVerification from '@/app/(auth)/otp';

const mockPost = jest.fn();
const mockStoreSession = jest.fn();
const mockUnlock = jest.fn();
const mockReplace = jest.fn();
let mockGeneration = 0;
let mockPhone: string | null = '08012345678';
const mockNotify = jest.fn();

jest.mock('expo-router', () => ({ router: { replace: (...args: unknown[]) => mockReplace(...args) } }));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true, default: {
    getItem: async (key: string) => ({ UserPhone: mockPhone, UserFirstName: 'Ada', UserLastName: 'Test' }[key] || null),
    removeItem: jest.fn(async () => {}),
  },
}));
const mockApiPost = jest.fn();
const mockSaveRefresh = jest.fn();
jest.mock('@/lib/api', () => ({
  publicPost: (...args: unknown[]) => mockPost(...args),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
}));
jest.mock('@/lib/secureStore', () => ({
  getSessionGeneration: () => mockGeneration,
  storeSession: (...args: unknown[]) => mockStoreSession(...args),
  saveRefreshToken: (...args: unknown[]) => mockSaveRefresh(...args),
}));
jest.mock('@/lib/screenCapture', () => ({ usePinScreenProtection: () => {} }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/session', () => ({ unlockSession: () => mockUnlock() }));
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/Stepper', () => ({ Stepper: () => null }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: {} }), font: {} }));
jest.mock('@/components/design/ui', () => ({
  Screen: ({ children }: { children: ReactNode }) => children,
  Header: () => null,
  Field: (props: any) => {
    const R = jest.requireActual<typeof import('react')>('react');
    const { TextInput } = jest.requireActual<typeof import('react-native')>('react-native');
    return R.createElement(TextInput, { ...props, accessibilityLabel: props.label });
  },
  Btn: (props: any) => {
    const R = jest.requireActual<typeof import('react')>('react');
    const { Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
    return R.createElement(Pressable, { ...props, accessibilityLabel: props.label });
  },
}));

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  mockGeneration = 0;
  mockPhone = '08012345678';
});
afterEach(() => jest.useRealTimers());

async function startVerification() {
  let finish!: (value: unknown) => void;
  mockPost.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<OTPVerification />); });
  await act(async () => {
    tree.root.findByProps({ autoComplete: 'sms-otp' }).props.onChangeText('654321');
  });
  await act(async () => { jest.advanceTimersByTime(0); });
  // The code is only CHECKED first: nothing is created until the password is set.
  expect(mockPost).toHaveBeenCalledWith('/api/verify_otp/', {
    otp: '654321', phone: '08012345678', check_only: true,
  });
  return { tree, finish };
}

const CHECKED = { ok: true, json: async () => ({ success: true, verified: true }) };

async function fillPassword(tree: renderer.ReactTestRenderer, value = 'Zitch-pass1') {
  const field = (label: string) => tree.root.findByProps({ accessibilityLabel: label });
  await act(async () => { field('Password').props.onChangeText(value); });
  await act(async () => { field('Confirm password').props.onChangeText(value); });
  return tree.root.findAll((n) => n.props.accessibilityLabel === 'Continue' && typeof n.props.onPress === 'function')[0];
}

it('a confirmed code opens the password fields but creates no account yet', async () => {
  const { tree, finish } = await startVerification();
  await act(async () => { finish(CHECKED); });
  expect(mockStoreSession).not.toHaveBeenCalled();
  expect(mockUnlock).not.toHaveBeenCalled();
  expect(mockReplace).not.toHaveBeenCalled();
  expect(tree.root.findByProps({ accessibilityLabel: 'Password' }).props.editable).toBe(true);
  act(() => tree.unmount());
});

it('creates the account when the password is set, then goes to BVN and address', async () => {
  const { tree, finish } = await startVerification();
  await act(async () => { finish(CHECKED); });
  const field = (label: string) => tree.root.findByProps({ accessibilityLabel: label });
  const cont = () => tree.root.findAll((n) => n.props.accessibilityLabel === 'Continue' && typeof n.props.onPress === 'function')[0];
  await act(async () => { field('Password').props.onChangeText('Zitch-pass1'); });
  await act(async () => { field('Confirm password').props.onChangeText('Zitch-pass'); });
  expect(cont().props.disabled).toBe(true);
  await act(async () => { field('Confirm password').props.onChangeText('Zitch-pass1'); });
  expect(cont().props.disabled).toBe(false);
  mockPost.mockResolvedValue({ ok: true, json: async () => ({ access_token: 'signup-access' }) });
  await act(async () => { await cont().props.onPress(); });
  expect(mockPost).toHaveBeenLastCalledWith('/api/verify_otp/', {
    otp: '654321', phone: '08012345678', first_name: 'Ada', last_name: 'Test', password: 'Zitch-pass1',
  });
  expect(mockApiPost).not.toHaveBeenCalled();
  expect(mockStoreSession).toHaveBeenCalledWith({ access_token: 'signup-access' }, 0);
  expect(mockUnlock).toHaveBeenCalledTimes(1);
  expect(mockReplace).toHaveBeenCalledWith({ pathname: '/kyc', params: { onboarding: '1' } });
  act(() => tree.unmount());
});

it('a weak password is reported and the code stays usable', async () => {
  const { tree, finish } = await startVerification();
  await act(async () => { finish(CHECKED); });
  const cont = await fillPassword(tree);
  mockPost.mockResolvedValue({ ok: false, json: async () => ({ code: 'weak_password', message: 'Too common' }) });
  await act(async () => { await cont.props.onPress(); });
  expect(mockNotify).toHaveBeenCalledWith('Password', 'Too common');
  expect(mockStoreSession).not.toHaveBeenCalled();
  expect(tree.root.findByProps({ accessibilityLabel: 'Password' }).props.editable).toBe(true);
  act(() => tree.unmount());
});

it('keeps the server rules: a password without a symbol cannot be sent', async () => {
  const { tree, finish } = await startVerification();
  await act(async () => { finish(CHECKED); });
  const cont = await fillPassword(tree, 'Zitchpass1');
  expect(cont.props.disabled).toBe(true);
  act(() => tree.unmount());
});

it('ignores a late account result after another account signs in', async () => {
  const { tree, finish } = await startVerification();
  await act(async () => { finish(CHECKED); });
  const cont = await fillPassword(tree);
  let created!: (value: unknown) => void;
  mockPost.mockReturnValue(new Promise(resolve => { created = resolve; }));
  let saving!: Promise<void>;
  act(() => { saving = cont.props.onPress(); });
  mockGeneration += 1;
  await act(async () => { created({ ok: true, json: async () => ({ access_token: 'old-access' }) }); await saving; });
  expect(mockStoreSession).not.toHaveBeenCalled();
  expect(mockUnlock).not.toHaveBeenCalled();
  expect(mockReplace).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('ignores a late code result after the verification screen closes', async () => {
  const { tree, finish } = await startVerification();
  act(() => tree.unmount());
  await act(async () => { finish(CHECKED); });
  expect(mockStoreSession).not.toHaveBeenCalled();
  expect(mockUnlock).not.toHaveBeenCalled();
  expect(mockReplace).not.toHaveBeenCalled();
});


it('provides a fresh signup path when the pending phone cannot be restored', async () => {
  mockPhone = null;
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<OTPVerification />); });
  expect(tree.root.findAllByProps({ autoComplete: 'sms-otp' })).toHaveLength(0);
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Enter my details' }).props.onPress(); });
  expect(mockReplace).toHaveBeenCalledWith('/register');
  expect(mockPost).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('sends only one resend request and suppresses a late SMS notice after account change', async () => {
  let finish!: (value: unknown) => void;
  mockPost.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<OTPVerification />); });
  for (let i = 0; i < 30; i++) await act(async () => { jest.advanceTimersByTime(1000); });
  const resend = tree.root.findByProps({ accessibilityLabel: 'Resend verification code' });
  expect(resend.props.disabled).toBe(false);
  let sending!: Promise<void>;
  act(() => { sending = resend.props.onPress(); resend.props.onPress(); });
  expect(mockPost).toHaveBeenCalledTimes(1);
  expect(tree.root.findByProps({ autoComplete: 'sms-otp' }).props.editable).toBe(false);
  mockGeneration += 1;
  await act(async () => { finish({ ok: true, json: async () => ({}) }); await sending; });
  expect(mockNotify).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('waits 30 seconds before a new code can be asked for', async () => {
  mockPost.mockReturnValue(new Promise(() => {}));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<OTPVerification />); });
  const resend = () => tree.root.findByProps({ accessibilityLabel: 'Resend verification code' });
  for (let i = 0; i < 29; i++) await act(async () => { jest.advanceTimersByTime(1000); });
  expect(resend().props.disabled).toBe(true);
  await act(async () => { jest.advanceTimersByTime(1000); });
  expect(resend().props.disabled).toBe(false);
  act(() => tree.unmount());
});
