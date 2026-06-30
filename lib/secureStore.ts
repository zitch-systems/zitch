import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';

/**
 * Centralised access-token storage.
 *
 * The access token is a credential, so on native platforms it is kept in the
 * OS keychain / keystore via expo-secure-store instead of the unencrypted
 * AsyncStorage. expo-secure-store has no web implementation, so we fall back to
 * AsyncStorage on web (where the app is non-sensitive preview only).
 */
const TOKEN_KEY = 'access_token';
const isWeb = Platform.OS === 'web';

// In-memory cache of the access token. getToken() is on the hot path of every
// authenticated API call (plus the auth guard and wallet load), and a native
// keychain read costs real milliseconds on each call — enough to make taps feel
// laggy on Android. We read the keychain once, then serve from memory; the cache
// is updated on save and cleared on sign-out, and it never outlives the process.
// `undefined` = not loaded yet; `null` = loaded and known-absent.
let cachedToken: string | null | undefined;

export async function saveToken(token: string): Promise<void> {
  cachedToken = token;
  if (isWeb) {
    await AsyncStorage.setItem(TOKEN_KEY, token);
    return;
  }
  await SecureStore.setItemAsync(TOKEN_KEY, token);
}

export async function getToken(): Promise<string | null> {
  if (cachedToken !== undefined) return cachedToken;
  const token = isWeb
    ? await AsyncStorage.getItem(TOKEN_KEY)
    : await SecureStore.getItemAsync(TOKEN_KEY);
  cachedToken = token;
  return token;
}

export async function clearToken(): Promise<void> {
  cachedToken = null;
  if (isWeb) {
    await AsyncStorage.removeItem(TOKEN_KEY);
    return;
  }
  await SecureStore.deleteItemAsync(TOKEN_KEY);
}

/** Clears the token plus the non-sensitive profile keys kept in AsyncStorage. */
// ---------------------------------------------------------------------------
// Transaction PIN (for biometric "pay with Face ID / fingerprint")
//
// The money-authorising PIN is kept in the OS keychain/keystore (same place as
// the session token), so a successful biometric scan can retrieve and submit it
// instead of the user retyping. Retrieval is always gated by the OS biometric
// prompt; the value is cleared on sign-out. Not stored on web (preview only).
// ---------------------------------------------------------------------------
const TXN_PIN_KEY = 'txn_pin';
// Non-secret marker (plain AsyncStorage) recording *whether* a money PIN is
// cached in the keychain. The PIN pad uses this to decide whether to offer the
// biometric-pay shortcut, so the UI never has to read the actual secret just to
// toggle a button — the PIN itself is only ever pulled inside the biometric flow.
const HAS_TXN_PIN_KEY = 'z-has-pin';

export async function saveTransactionPin(pin: string): Promise<void> {
  if (isWeb) return; // don't persist the money PIN in unencrypted web storage
  await SecureStore.setItemAsync(TXN_PIN_KEY, pin);
  await AsyncStorage.setItem(HAS_TXN_PIN_KEY, '1');
}

export async function getTransactionPin(): Promise<string | null> {
  if (isWeb) return null;
  return SecureStore.getItemAsync(TXN_PIN_KEY);
}

/** Whether a money PIN is cached for biometric pay — a non-secret boolean, so
 *  callers can gate UI without reading the PIN into memory. */
export async function hasTransactionPin(): Promise<boolean> {
  if (isWeb) return false;
  if ((await AsyncStorage.getItem(HAS_TXN_PIN_KEY)) === '1') return true;
  // One-time migration for sessions whose PIN was cached before this flag
  // existed: if a PIN is already in the keychain, record the marker so future
  // checks never touch the secret again (and the biometric shortcut keeps
  // working for those users).
  const existing = await SecureStore.getItemAsync(TXN_PIN_KEY);
  if (existing) {
    await AsyncStorage.setItem(HAS_TXN_PIN_KEY, '1');
    return true;
  }
  return false;
}

export async function clearTransactionPin(): Promise<void> {
  if (isWeb) return;
  await SecureStore.deleteItemAsync(TXN_PIN_KEY);
  await AsyncStorage.removeItem(HAS_TXN_PIN_KEY);
}

// Non-secret marker: whether we've already nudged the user (once) to turn on
// biometric pay after a successful transfer. Keeps the in-context offer one-time
// so it never nags — they can always enable it later from Me / Settings.
const BIOPAY_OFFERED_KEY = 'z-biopay-offered';

export async function hasOfferedBiometricPay(): Promise<boolean> {
  return (await AsyncStorage.getItem(BIOPAY_OFFERED_KEY)) === '1';
}

export async function markBiometricPayOffered(): Promise<void> {
  await AsyncStorage.setItem(BIOPAY_OFFERED_KEY, '1');
}

export async function clearSession(): Promise<void> {
  await clearToken();
  await clearTransactionPin();
  await AsyncStorage.multiRemove(['userID', 'sessionExpiration', 'UserEmail', 'UserPhone', 'lastActiveAt', 'z-locked', 'z-has-pin', BIOPAY_OFFERED_KEY]);
}
