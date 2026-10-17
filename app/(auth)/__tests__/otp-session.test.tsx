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
jest.mock('@/lib/api', () => ({ publicPost: (...args: unknown[]) => mockPost(...args) }));
jest.mock('@/lib/secureStore', () => ({
  getSessionGeneration: () => mockGeneration,
  storeSession: (...args: unknown[]) => mockStoreSession(...args),
}));
jest.mock('@/lib/session', () => ({ unlockSession: () => mockUnlock() }));
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/Stepper', () => ({ Stepper: () => null }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: {} }), font: {} }));
jest.mock('@/components/design/ui', () => ({
  Screen: ({ children }: { children: ReactNode }) => children,
  Header: () => null,
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
  expect(mockPost).toHaveBeenCalledWith('/api/verify_otp/', {
    otp: '654321', phone: '08012345678', first_name: 'Ada', last_name: 'Test',
  });
  return { tree, finish };
}

it('keeps verified signup bound to the session in which the SMS code was submitted', async () => {
  const { tree, finish } = await startVerification();
  await act(async () => { finish({ ok: true, json: async () => ({ access_token: 'signup-access' }) }); });
  expect(mockStoreSession).toHaveBeenCalledWith({ access_token: 'signup-access' }, 0);
  expect(mockUnlock).toHaveBeenCalledTimes(1);
  expect(mockReplace).toHaveBeenCalledWith('/setpassword');
  act(() => tree.unmount());
});

it('ignores a late signup OTP result after another account signs in', async () => {
  const { tree, finish } = await startVerification();
  mockGeneration += 1;
  await act(async () => { finish({ ok: true, json: async () => ({ access_token: 'old-access' }) }); });
  expect(mockStoreSession).not.toHaveBeenCalled();
  expect(mockUnlock).not.toHaveBeenCalled();
  expect(mockReplace).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('ignores a late signup OTP result after the verification screen closes', async () => {
  const { tree, finish } = await startVerification();
  act(() => tree.unmount());
  await act(async () => { finish({ ok: true, json: async () => ({ access_token: 'old-access' }) }); });
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
  for (let i = 0; i < 24; i++) await act(async () => { jest.advanceTimersByTime(1000); });
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
