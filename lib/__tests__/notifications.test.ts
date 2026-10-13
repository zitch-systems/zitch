jest.mock('react-native', () => ({ Platform: { OS: 'android' } }));
jest.mock('expo-device', () => ({ isDevice: true }));
jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { extra: { eas: { projectId: 'test-project' }, push: { androidConfigured: true } } } } }));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true, default: { setItem: jest.fn().mockResolvedValue(undefined), getItem: jest.fn(), removeItem: jest.fn().mockResolvedValue(undefined) },
}));
jest.mock('@/lib/api', () => ({ apiJson: jest.fn() }));
let mockGeneration = 0;
let mockToken: string | null = 'session';
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockGeneration, getToken: async () => mockToken }));
jest.mock('expo-notifications', () => ({
  AndroidImportance: { HIGH: 4 }, setNotificationHandler: jest.fn(),
  setNotificationChannelAsync: jest.fn().mockResolvedValue(undefined),
  getPermissionsAsync: jest.fn().mockResolvedValue({ status: 'granted' }),
  requestPermissionsAsync: jest.fn(),
  getExpoPushTokenAsync: jest.fn().mockResolvedValue({ data: 'ExpoPushToken[test]' }),
  addNotificationResponseReceivedListener: jest.fn().mockReturnValue({ remove: jest.fn() }),
  getLastNotificationResponseAsync: jest.fn().mockResolvedValue(null),
  clearLastNotificationResponseAsync: jest.fn().mockResolvedValue(undefined),
}));

import AsyncStorage from '@react-native-async-storage/async-storage';
import Constants from 'expo-constants';
import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import { apiJson } from '../api';
import { registerForPushNotifications, subscribeToNotificationOpens, takePendingNotificationOpen } from '../notifications';

beforeEach(() => {
  jest.clearAllMocks(); mockGeneration = 0; mockToken = 'session';
  (Platform as { OS: string }).OS = 'android';
  Constants.expoConfig!.extra = { eas: { projectId: 'test-project' }, push: { androidConfigured: true } };
});

it.each([false, undefined])('does not ask permission or register an Android build without native push configuration: %s', async (configured) => {
  Constants.expoConfig!.extra!.push.androidConfigured = configured;
  expect(await registerForPushNotifications(true)).toBe('not-configured');
  expect(Notifications.setNotificationChannelAsync).not.toHaveBeenCalled();
  expect(Notifications.getPermissionsAsync).not.toHaveBeenCalled();
  expect(Notifications.requestPermissionsAsync).not.toHaveBeenCalled();
  expect(Notifications.getExpoPushTokenAsync).not.toHaveBeenCalled();
  expect(apiJson).not.toHaveBeenCalled();
});

it('does not require Android Firebase configuration for an iOS registration', async () => {
  (Platform as { OS: string }).OS = 'ios';
  Constants.expoConfig!.extra!.push.androidConfigured = false;
  (apiJson as jest.Mock).mockResolvedValue({ _httpOk: true });
  expect(await registerForPushNotifications()).toBe('registered');
});

it('does not prompt for push permission before the Expo project is configured', async () => {
  Constants.expoConfig!.extra!.eas = {};
  expect(await registerForPushNotifications(true)).toBe('not-configured');
  expect(Notifications.requestPermissionsAsync).not.toHaveBeenCalled();
  expect(Notifications.getExpoPushTokenAsync).not.toHaveBeenCalled();
});

it('recognizes the backend message-only success and stores the registered token', async () => {
  (apiJson as jest.Mock).mockResolvedValue({ message: 'Notifications enabled', _httpOk: true, _httpStatus: 200 });
  expect(await registerForPushNotifications()).toBe('registered');
  expect(AsyncStorage.setItem).toHaveBeenCalledWith('z-expo-push-token', 'ExpoPushToken[test]');
});

it.each([{ success: false, _httpOk: true }, { _httpOk: false }, { offline: true }])('does not mark failed registration as enabled: %j', async (response) => {
  (apiJson as jest.Mock).mockResolvedValue(response);
  expect(await registerForPushNotifications()).toBe('failed');
  expect(AsyncStorage.setItem).not.toHaveBeenCalled();
});

it('does not reopen notifications after the subscription has unmounted', async () => {
  let resolve!: (value: any) => void;
  (Notifications.getLastNotificationResponseAsync as jest.Mock).mockReturnValueOnce(new Promise(r => { resolve = r; }));
  const onOpen = jest.fn();
  const unsubscribe = subscribeToNotificationOpens(onOpen);
  unsubscribe();
  resolve({ notification: {} });
  await Promise.resolve();
  await Promise.resolve();
  expect(onOpen).not.toHaveBeenCalled();
  expect(AsyncStorage.setItem).not.toHaveBeenCalled();
});

it('handles native cold-start notification errors without an unhandled rejection', async () => {
  (Notifications.getLastNotificationResponseAsync as jest.Mock).mockRejectedValueOnce(new Error('native unavailable'));
  const onOpen = jest.fn();
  const unsubscribe = subscribeToNotificationOpens(onOpen);
  await Promise.resolve();
  await Promise.resolve();
  expect(onOpen).not.toHaveBeenCalled();
  unsubscribe();
});

it.each([null, 'not-a-date', String(Date.now() + 3600000), String(Date.now() - 3600000)])('rejects absent, malformed or stale pending notification markers: %s', async (value) => {
  (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(value);
  expect(await takePendingNotificationOpen()).toBe(false);
});

it('consumes a recent pending notification marker', async () => {
  (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(String(Date.now()));
  expect(await takePendingNotificationOpen()).toBe(true);
  expect(AsyncStorage.removeItem).toHaveBeenCalledWith('z-pending-notification-open');
});


it('does not bind a delayed native token after the account has changed', async () => {
  (Notifications.getExpoPushTokenAsync as jest.Mock).mockImplementationOnce(async () => {
    mockGeneration += 1;
    return { data: 'ExpoPushToken[old-account]' };
  });
  expect(await registerForPushNotifications()).toBe('failed');
  expect(apiJson).not.toHaveBeenCalled();
  expect(AsyncStorage.setItem).not.toHaveBeenCalled();
});

it('requires a signed-in account before requesting push credentials', async () => {
  mockToken = null;
  expect(await registerForPushNotifications(true)).toBe('failed');
  expect(Notifications.getExpoPushTokenAsync).not.toHaveBeenCalled();
  expect(Notifications.requestPermissionsAsync).not.toHaveBeenCalled();
  expect(apiJson).not.toHaveBeenCalled();
});

it('does not persist a registration response belonging to the previous session', async () => {
  (apiJson as jest.Mock).mockImplementationOnce(async () => {
    mockGeneration += 1;
    return { _httpOk: true };
  });
  expect(await registerForPushNotifications()).toBe('failed');
  expect(AsyncStorage.setItem).not.toHaveBeenCalled();
});


it('marks native cold-start taps so navigation requires a fresh unlock', async () => {
  (Notifications.getLastNotificationResponseAsync as jest.Mock).mockResolvedValueOnce({ notification: {} });
  const onOpen = jest.fn();
  const unsubscribe = subscribeToNotificationOpens(onOpen);
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  expect(onOpen).toHaveBeenCalledWith(true);
  unsubscribe();
});
