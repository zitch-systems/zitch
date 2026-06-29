import AsyncStorage from '@react-native-async-storage/async-storage';
import { getToken, clearSession } from '@/lib/secureStore';

/**
 * Client-side inactivity timeout.
 *
 * The server access-token TTL is the hard security bound; this is the softer
 * client limit that *locks* the app after a stretch of no activity. "Activity"
 * is recorded on every authenticated API call (see lib/api) and on sign-in, and
 * the limit is enforced on launch, when the app returns to the foreground, and
 * on a short repeating timer (see app/_layout).
 *
 * On timeout the session is LOCKED rather than cleared: the access token stays
 * on the device so the user can unlock instantly with biometrics (or a password
 * sign-in). A full sign-out (Me → Log out) still clears the token outright.
 */
export const IDLE_LIMIT_MS = 5 * 60 * 1000; // 5 minutes
const LAST_ACTIVE_KEY = 'lastActiveAt';
export const LOCK_KEY = 'z-locked';

/** Record that the user is active now. */
export async function touchActivity(): Promise<void> {
  await AsyncStorage.setItem(LAST_ACTIVE_KEY, Date.now().toString());
}

/** Lock the session (keep the token; require biometric/password to re-enter). */
export async function lockSession(): Promise<void> {
  await AsyncStorage.setItem(LOCK_KEY, '1');
}

/** Clear the lock after a successful re-authentication, and mark activity. */
export async function unlockSession(): Promise<void> {
  await AsyncStorage.removeItem(LOCK_KEY);
  await touchActivity();
}

/** True while the session is locked by the idle timeout. */
export async function isSessionLocked(): Promise<boolean> {
  return (await AsyncStorage.getItem(LOCK_KEY)) === '1';
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
 * Lock the session if it's been idle too long. Returns true if it just locked
 * (so the caller can bounce to the sign-in / unlock screen). A no-op —
 * returning false — when there's no session, the session is fresh, or it's
 * already locked. Note: this does NOT refresh activity; real activity is
 * stamped by authenticated API calls and on sign-in.
 */
export async function enforceIdleTimeout(): Promise<boolean> {
  if (await isSessionLocked()) return false; // already locked — nothing to do
  if (await isSessionIdleExpired()) {
    await lockSession();
    return true;
  }
  return false;
}

/**
 * Absolute idle cap. A LOCKED session deliberately keeps the access token on the
 * device for instant biometric re-entry, but a device left untouched this long
 * is treated as lost/stolen: the token (and money PIN) are dropped outright so a
 * thief can't biometric-unlock into a still-valid session, and the next entry
 * needs a full password sign-in. This is a client-side reduction of the
 * lost-device window below the server token TTL; it does not shorten the server
 * bound or affect actively-used sessions (real activity keeps the stamp fresh).
 */
export const HARD_EXPIRE_MS = 12 * 60 * 60 * 1000; // 12 hours

/** True if a session exists but has been idle past the absolute hard cap. */
export async function isSessionHardExpired(): Promise<boolean> {
  const token = await getToken();
  if (!token) return false;
  const raw = await AsyncStorage.getItem(LAST_ACTIVE_KEY);
  if (!raw) return false; // no stamp yet (e.g. just signed in) — don't force out
  const last = Number(raw);
  if (!Number.isFinite(last)) return false;
  return Date.now() - last > HARD_EXPIRE_MS;
}

/**
 * Fully clear a session that's been idle past the hard cap. Returns true if it
 * just cleared (so the caller can bounce to /signin, where — with no token — a
 * password sign-in is required rather than a biometric unlock). Check this
 * BEFORE the idle lock so a long-idle session is cleared, not merely locked.
 */
export async function enforceHardExpiry(): Promise<boolean> {
  if (await isSessionHardExpired()) {
    await clearSession();
    return true;
  }
  return false;
}

/**
 * Re-lock grace period for *leaving* the app. Opening the image picker, camera,
 * or the biometric prompt briefly backgrounds the app; locking the instant we
 * background meant "upload a photo" bounced the user to the unlock screen. So
 * instead of locking on background, we stamp the time and only re-lock on
 * return if the app was actually away at least this long.
 */
export const LOCK_AFTER_BACKGROUND_MS = 60 * 1000; // 1 minute
const BACKGROUND_AT_KEY = 'z-bg-at';

// In-app excursions that legitimately background the app (image picker, camera)
// must never trigger the lock, however long they take. Call begin() before
// launching and end() in a finally afterwards. A counter handles overlap.
let externalActivityCount = 0;
export function beginExternalActivity(): void { externalActivityCount += 1; }
export function endExternalActivity(): void {
  externalActivityCount = Math.max(0, externalActivityCount - 1);
}
export function isExternalActivityActive(): boolean { return externalActivityCount > 0; }

/** Stamp the moment the app was backgrounded (drives the re-lock decision). */
export async function markBackgrounded(): Promise<void> {
  await AsyncStorage.setItem(BACKGROUND_AT_KEY, Date.now().toString());
}

/**
 * On returning to the foreground, lock the session only if the app was away at
 * least LOCK_AFTER_BACKGROUND_MS and a session exists. Always clears the stamp.
 * Returns true if it just locked.
 */
export async function lockIfAwayTooLong(): Promise<boolean> {
  const raw = await AsyncStorage.getItem(BACKGROUND_AT_KEY);
  await AsyncStorage.removeItem(BACKGROUND_AT_KEY);
  if (!raw || isExternalActivityActive()) return false;
  const at = Number(raw);
  if (!Number.isFinite(at) || Date.now() - at < LOCK_AFTER_BACKGROUND_MS) return false;
  if (!(await getToken())) return false;
  await lockSession();
  return true;
}
