import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import Setup from '@/app/(auth)/setup';
import Completed from '@/app/(auth)/completed';
import ApprovalAppLink from '@/app/(auth)/wa/approve/[token]';

const mockGetToken = jest.fn();
const mockLocked = jest.fn();
const mockRead = jest.fn();
const mockPushRegistration = jest.fn();
const mockRedirect = jest.fn();
let mockParams: Record<string, unknown> = {};
jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn() },
  useLocalSearchParams: () => mockParams,
  Redirect: (props: unknown) => { mockRedirect(props); return null; },
}));
jest.mock('@/lib/secureStore', () => ({ getToken: () => mockGetToken(), getSessionGeneration: () => 0 }));
jest.mock('@/lib/session', () => ({
  enforceHardExpiry: jest.fn(), enforceIdleTimeout: jest.fn(), lockIfAwayTooLong: jest.fn(),
  isSessionLocked: () => mockLocked(), isExternalActivityActive: () => false,
}));
jest.mock('@/lib/notifications', () => ({ registerForPushNotifications: (...args: unknown[]) => mockPushRegistration(...args) }));
jest.mock('@react-native-async-storage/async-storage', () => ({ __esModule: true, default: { getItem: (...args: unknown[]) => mockRead(...args) } }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Brand', () => ({ ZMark: () => null }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: {} }), font: {} }));
jest.mock('@/components/design/ui', () => ({
  Screen: ({ children }: { children: ReactNode }) => children,
  Btn: () => null,
}));
beforeEach(() => {
  jest.clearAllMocks();
  mockGetToken.mockResolvedValue('access');
  mockLocked.mockResolvedValue(false);
  mockRead.mockResolvedValue('Ada');
  mockPushRegistration.mockResolvedValue(null);
  mockParams = {};
});

it.each([
  { name: 'setup', Screen: Setup, token: null, locked: false },
  { name: 'setup', Screen: Setup, token: 'access', locked: true },
  { name: 'completed', Screen: Completed, token: null, locked: false },
  { name: 'completed', Screen: Completed, token: 'access', locked: true },
])('requires an unlocked account for $name (token=$token, locked=$locked)', async ({ Screen, token, locked }) => {
  mockGetToken.mockResolvedValue(token);
  mockLocked.mockResolvedValue(locked);
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Screen />); });
  expect(mockRedirect).toHaveBeenCalledWith({ href: '/signin' });
  expect(mockRead).not.toHaveBeenCalled();
  expect(mockPushRegistration).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('requests notifications only after the completed screen passes the session guard', async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Completed />); });
  expect(mockRead).toHaveBeenCalledWith('UserFirstName');
  expect(mockPushRegistration).toHaveBeenCalledWith(true);
  expect(JSON.stringify(tree.toJSON())).toContain('Welcome to Zitch, Ada!');
  act(() => tree.unmount());
});

it.each([
  { token: 'ap1.signed', expected: 'ap1.signed' },
  { token: ['ap1.signed', 'ap2.signed'], expected: '' },
  { token: undefined, expected: '' },
])('preserves only a single unambiguous approval path token: $token', async ({ token, expected }) => {
  mockParams = { token };
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<ApprovalAppLink />); });
  expect(mockRedirect).toHaveBeenCalledWith({ href: { pathname: '/waapprove', params: { token: expected } } });
  act(() => tree.unmount());
});
