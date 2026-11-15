import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import NewDevice from '@/app/(auth)/newdevice';

const mockPublicPost = jest.fn();
const mockStoreSession = jest.fn();
const mockReplace = jest.fn();
const mockOpen = jest.fn();
const mockNotify = jest.fn();
const json = (status: number, body: unknown) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

jest.mock('expo-router', () => ({
  router: { replace: (...args: unknown[]) => mockReplace(...args) },
  useLocalSearchParams: () => ({ challenge: 'one-time-challenge', id: '08011112222' }),
}));
jest.mock('expo-web-browser', () => ({ openBrowserAsync: (...args: unknown[]) => mockOpen(...args) }));
jest.mock('expo-device', () => ({ modelName: 'Pixel 9' }));
jest.mock('@/lib/api', () => ({ publicPost: (...args: unknown[]) => mockPublicPost(...args) }));
jest.mock('@/lib/secureStore', () => ({
  storeSession: (...args: unknown[]) => mockStoreSession(...args),
  rememberIdentifier: async () => {},
  getSessionGeneration: () => 0,
}));
jest.mock('@/lib/session', () => ({ unlockSession: async () => {}, beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));
jest.mock('@/lib/notifications', () => ({ registerForPushNotifications: async () => 'ok' }));
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

beforeEach(() => { jest.clearAllMocks(); mockOpen.mockResolvedValue({ type: 'dismiss' }); });

const press = async (tree: renderer.ReactTestRenderer, label: string) => {
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: label }).props.onPress(); });
};

it('opens only the Prembly page, then signs in once the server confirms the face check', async () => {
  mockPublicPost.mockImplementation(async (path: string) => path.endsWith('/start/')
    ? json(200, { success: true, session: 'b6f1c5f0-0000-4000-8000-000000000001', url: 'https://sdk-live.prembly.com/?session=sdk' })
    : json(200, { access_token: 'access', refresh_token: 'refresh' }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<NewDevice />); });
  await press(tree, 'Start face check');
  expect(mockOpen).toHaveBeenCalledWith('https://sdk-live.prembly.com/?session=sdk');
  expect(mockPublicPost).toHaveBeenCalledWith('/api/signin/face/status/', { challenge: 'one-time-challenge', session: 'b6f1c5f0-0000-4000-8000-000000000001' });
  expect(mockStoreSession).toHaveBeenCalledWith({ access_token: 'access', refresh_token: 'refresh' }, 0);
  expect(mockReplace).toHaveBeenCalledWith('/home');
  act(() => tree.unmount());
});

it('refuses a face-check link that is not Prembly', async () => {
  mockPublicPost.mockResolvedValue(json(200, { success: true, session: 's', url: 'https://example.com/fake' }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<NewDevice />); });
  await press(tree, 'Start face check');
  expect(mockOpen).not.toHaveBeenCalled();
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
