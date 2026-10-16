import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import OTPVerification from '@/app/(auth)/otp';

const mockPost = jest.fn();
const mockStoreSession = jest.fn();
const mockUnlock = jest.fn();
const mockReplace = jest.fn();
let mockGeneration = 0;

jest.mock('expo-router', () => ({ router: { replace: (...args: unknown[]) => mockReplace(...args) } }));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true, default: {
    getItem: async (key: string) => ({ UserPhone: '08012345678', UserFirstName: 'Ada', UserLastName: 'Test' }[key] || null),
    removeItem: jest.fn(async () => {}),
  },
}));
jest.mock('@/lib/api', () => ({ publicPost: (...args: unknown[]) => mockPost(...args) }));
jest.mock('@/lib/secureStore', () => ({
  getSessionGeneration: () => mockGeneration,
  storeSession: (...args: unknown[]) => mockStoreSession(...args),
}));
jest.mock('@/lib/session', () => ({ unlockSession: () => mockUnlock() }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/Stepper', () => ({ Stepper: () => null }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: {} }), font: {} }));
jest.mock('@/components/design/ui', () => ({
  Screen: ({ children }: { children: ReactNode }) => children,
  Header: () => null,
}));

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  mockGeneration = 0;
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
