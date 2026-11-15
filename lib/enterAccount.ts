import { router } from 'expo-router';
import { unlockSession } from '@/lib/session';
import { pendingWhatsAppApproval } from '@/lib/pendingApproval';
import { registerForPushNotifications, takePendingNotificationOpen } from '@/lib/notifications';

/**
 * The last step of every sign-in, once a session is stored: unlock it, bind this
 * phone's push token, then land where the customer was heading — a WhatsApp
 * approval link that opened the app, a notification they tapped, or Home.
 *
 * Shared by the password / biometric sign-in and the new-device face check, so
 * a sign-in that needed the face check does not drop a pending approval.
 * `isCurrent` is checked after every await; a stale screen navigates nowhere.
 */
export async function enterAccount(isCurrent: () => boolean): Promise<void> {
  if (!isCurrent()) return;
  await unlockSession();
  if (!isCurrent()) return;
  // Bind this phone's push token to whoever is signing in now. Signup was the
  // only registration, so a phone that changed hands kept delivering the
  // previous customer's alerts, and a new phone never received any. No
  // permission prompt here: it binds only where alerts are already allowed.
  registerForPushNotifications(false).catch(() => {});
  const token = await pendingWhatsAppApproval();
  if (!isCurrent()) return;
  const openNotifications = !token && await takePendingNotificationOpen().catch(() => false);
  if (!isCurrent()) return;
  router.replace(token ? { pathname: '/waapprove', params: { token } } : openNotifications ? '/notifications' : '/home');
}
