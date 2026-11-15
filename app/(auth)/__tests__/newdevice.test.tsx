import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import NewDevice from '@/app/(auth)/newdevice';

const mockPublicPost = jest.fn();
const mockStoreSession = jest.fn();
const mockReplace = jest.fn();
const mockModal = jest.fn();
let mockApproval = '';
const mockNotify = jest.fn();
const json = (status: number, body: unknown) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

jest.mock('expo-router', () => ({
  router: { replace: (...args: unknown[]) => mockReplace(...args) },
  useLocalSearchParams: () => ({ challenge: 'one-time-challenge', id: '08011112222' }),
}));
jest.mock('@/components/design/FaceVerifyModal', () => (props: unknown) => { mockModal(props); return null; });
jest.mock('@/lib/pendingApproval', () => ({ pendingWhatsAppApproval: async () => mockApproval }));
jest.mock('expo-device', () => ({ modelName: 'Pixel 9' }));
jest.mock('@/lib/api', () => ({ publicPost: (...args: unknown[]) => mockPublicPost(...args) }));
jest.mock('@/lib/secureStore', () => ({
  storeSession: (...args: unknown[]) => mockStoreSession(...args),
  rememberIdentifier: async () => {},
  getSessionGeneration: () => 0,
}));
jest.mock('@/lib/session', () => ({ unlockSession: async () => {}, beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));
jest.mock('@/lib/notifications', () => ({ registerForPushNotifications: async () => 'ok', takePendingNotificationOpen: async () => false }));
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: {} }), font: {} }));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => R.createElement(View, null, children),
    Header: () => null,
    Btn: (props: any) => R.createElement(Pressable, { onPress: props.onPress, accessibilityLabel: props.label }),
  };
});

beforeEach(() => { jest.useFakeTimers(); jest.clearAllMocks(); mockApproval = ''; });
afterEach(() => jest.useRealTimers());

const lastModal = () => mockModal.mock.calls[mockModal.mock.calls.length - 1]?.[0] as
  { url: string; visible: boolean; onClose: () => void; title?: string; footer?: string } | undefined;
const tick = async (ms: number) => { await act(async () => { jest.advanceTimersByTime(ms); }); await act(async () => {}); };
const statusCalls = () => mockPublicPost.mock.calls.filter(([p]) => p === '/api/signin/face/status/').length;
const SESSION = 'b6f1c5f0-0000-4000-8000-000000000001';

const press = async (tree: renderer.ReactTestRenderer, label: string) => {
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: label }).props.onPress(); });
};

it('holds the Prembly page in the app and signs in once polling sees the server confirm', async () => {
  let confirmed = false;
  mockPublicPost.mockImplementation(async (path: string) => path.endsWith('/start/')
    ? json(200, { success: true, session: SESSION, url: 'https://sdk-live.prembly.com/?session=sdk' })
    : confirmed ? json(200, { access_token: 'access', refresh_token: 'refresh' }) : json(202, { status: 'pending' }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<NewDevice />); });
  await press(tree, 'Start face check');
  expect(lastModal()).toMatchObject({ url: 'https://sdk-live.prembly.com/?session=sdk', visible: true, title: 'Face check',
    footer: 'Secured by Prembly · matched to your BVN photo' });
  await tick(3000);
  expect(mockPublicPost).toHaveBeenCalledWith('/api/signin/face/status/', { challenge: 'one-time-challenge', session: SESSION });
  expect(mockStoreSession).not.toHaveBeenCalled();
  confirmed = true;
  await tick(3000);
  expect(mockStoreSession).toHaveBeenCalledWith({ access_token: 'access', refresh_token: 'refresh' }, 0);
  expect(mockReplace).toHaveBeenCalledWith('/home');
  expect(lastModal()?.visible).toBe(false);
  act(() => tree.unmount());
});

it('routes a confirmed face check to a pending WhatsApp approval, like a password sign-in', async () => {
  mockApproval = 'wa-token';
  mockPublicPost.mockImplementation(async (path: string) => path.endsWith('/start/')
    ? json(200, { success: true, session: SESSION, url: 'https://sdk-live.prembly.com/?session=sdk' })
    : json(200, { access_token: 'access' }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<NewDevice />); });
  await press(tree, 'Start face check');
  await tick(3000);
  expect(mockReplace).toHaveBeenCalledWith({ pathname: '/waapprove', params: { token: 'wa-token' } });
  act(() => tree.unmount());
});

it('checks once when the customer closes the page', async () => {
  mockPublicPost.mockImplementation(async (path: string) => path.endsWith('/start/')
    ? json(200, { success: true, session: SESSION, url: 'https://sdk-live.prembly.com/?session=sdk' })
    : json(202, { status: 'pending' }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<NewDevice />); });
  await press(tree, 'Start face check');
  expect(statusCalls()).toBe(0);
  await act(async () => { lastModal()!.onClose(); });
  expect(statusCalls()).toBe(1);
  expect(mockNotify).toHaveBeenCalledWith('Still checking', expect.any(String), 'info');
  expect(lastModal()?.visible).toBe(false);
  await tick(9000);
  expect(statusCalls()).toBe(1);
  act(() => tree.unmount());
});

it('refuses a face-check link that is not Prembly', async () => {
  mockPublicPost.mockResolvedValue(json(200, { success: true, session: 's', url: 'https://example.com/fake' }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<NewDevice />); });
  await press(tree, 'Start face check');
  expect(mockModal.mock.calls.some(([p]) => (p as { visible: boolean }).visible)).toBe(false);
  expect(mockStoreSession).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('sends an expired sign-in back to the password screen', async () => {
  mockPublicPost.mockResolvedValue(json(401, { message: 'This sign-in has expired. Sign in again.' }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<NewDevice />); });
  await press(tree, 'Start face check');
  expect(mockReplace).toHaveBeenCalledWith('/signin');
  expect(mockStoreSession).not.toHaveBeenCalled();
  act(() => tree.unmount());
});
