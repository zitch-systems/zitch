import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { apiJson } from '@/lib/api';

const CHANNEL_ID = 'transactions';
const TOKEN_KEY = 'z-expo-push-token';
const PENDING_OPEN_KEY = 'z-pending-notification-open';
let handlerConfigured = false;

export type PushRegistration = 'registered' | 'denied' | 'unavailable' | 'failed';

/** Show notifications even while the app is in the foreground. */
export function configureNotificationPresentation(): void {
  if (Platform.OS === 'web' || handlerConfigured) return;
  handlerConfigured = true;
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
}

/** Register an already-authorised install, or (during completed signup) ask for
 * permission first. Android requires a channel before its permission prompt. */
export async function registerForPushNotifications(
  requestPermission = false,
): Promise<PushRegistration> {
  if (Platform.OS === 'web' || !Device.isDevice) return 'unavailable';
  configureNotificationPresentation();
  try {
    if (Platform.OS === 'android') {
      await Notifications.setNotificationChannelAsync(CHANNEL_ID, {
        name: 'Transaction alerts',
        description: 'Money in, transfers, and bill-payment updates',
        importance: Notifications.AndroidImportance.HIGH,
        sound: 'default',
        vibrationPattern: [0, 250, 180, 250],
      });
    }

    let permission = await Notifications.getPermissionsAsync();
    if (permission.status !== 'granted' && requestPermission) {
      permission = await Notifications.requestPermissionsAsync();
    }
    if (permission.status !== 'granted') return 'denied';

    const projectId = Constants.expoConfig?.extra?.eas?.projectId
      ?? Constants.easConfig?.projectId;
    if (!projectId) return 'failed';
    const token = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
    const result = await apiJson('/api/push/register/', {
      token,
      platform: Platform.OS,
    });
    if (!result?.success) return 'failed';
    await AsyncStorage.setItem(TOKEN_KEY, token);
    return 'registered';
  } catch {
    return 'failed';
  }
}

/** Route both warm-app and cold-start notification taps through one callback. */
export function subscribeToNotificationOpens(onOpen: () => void): () => void {
  if (Platform.OS === 'web') return () => {};
  const open = () => {
    AsyncStorage.setItem(PENDING_OPEN_KEY, Date.now().toString()).catch(() => {});
    onOpen();
  };
  const sub = Notifications.addNotificationResponseReceivedListener(open);
  const last = Notifications.getLastNotificationResponse();
  if (last) {
    Notifications.clearLastNotificationResponse();
    setTimeout(open, 0);
  }
  return () => sub.remove();
}

export async function takePendingNotificationOpen(): Promise<boolean> {
  const raw = await AsyncStorage.getItem(PENDING_OPEN_KEY);
  await AsyncStorage.removeItem(PENDING_OPEN_KEY);
  const at = Number(raw);
  return Number.isFinite(at) && Date.now() - at < 10 * 60 * 1000;
}

export async function clearPendingNotificationOpen(): Promise<void> {
  await AsyncStorage.removeItem(PENDING_OPEN_KEY);
}
