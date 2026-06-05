import AsyncStorage from '@react-native-async-storage/async-storage';
import { getToken, clearSession } from '@/lib/secureStore';

/**
 * Client-side inactivity timeout.
 *
 * The server access-token TTL is the hard security bound; this is the softer
 * client limit that signs a user out after a stretch of no activity. "Activity"
 * is recorded on every authenticated API call (see lib/api), and the limit is
 * enforced when the app returns to the foreground (see app/_layout). This
 * replaces an older one-shot `setTimeout` that fired a fixed hour after login —
 * even mid-transaction — regardless of whether the user was active.
 */
export const IDLE_LIMIT_MS = 60 * 60 * 1000; // 1 hour
const LAST_ACTIVE_KEY = 'lastActiveAt';

/** Record that the user is active now. */
export async function touchActivity(): Promise<void> {
  await AsyncStorage.setItem(LAST_ACTIVE_KEY, Date.now().toString());
}

/** True if a session exists but has been idle beyond the limit. */
export async function isSessionIdleExpired(): Promise<boolean> {
  const token = await getToken();
  if (!token) return false;
  const raw = await AsyncStorage.getItem(LAST_ACTIVE_KEY);
  if (!raw) return false; // no stamp yet (e.g. just signed in) — don't force out
  const last = Number(raw);
  if (!Number.isFinite(last)) return false;
  return Date.now() - last > IDLE_LIMIT_MS;
}

/**
 * Clear the session if it's been idle too long; otherwise mark activity.
 * Returns true if it logged the user out.
 */
export async function enforceIdleTimeout(): Promise<boolean> {
  if (await isSessionIdleExpired()) {
    await clearSession();
    return true;
  }
  await touchActivity();
  return false;
}
