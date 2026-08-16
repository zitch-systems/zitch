import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';

/**
 * Centralised access-token storage.
 *
 * The access token is a credential, so on native platforms it is kept in the
 * OS keychain / keystore via expo-secure-store instead of the unencrypted
 * AsyncStorage. expo-secure-store has no web implementation, so browser sessions
 * are memory-only: a reload signs out rather than leaving a bearer credential in
 * local storage where any later XSS or browser-profile copy can recover it.
 */
const TOKEN_KEY = 'access_token';
const isWeb = Platform.OS === 'web';

// Bind secrets (session token + money PIN) to THIS device: `WHEN_UNLOCKED_THIS_
// DEVICE_ONLY` keeps them out of iCloud/device backups, so a backup extraction
// or restore onto another handset can't lift the credential. Reads/deletes don't
// take the option — only the write sets the item's accessibility class.
const KEYCHAIN_OPTS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

// Unlike the session token, the transaction PIN directly authorises money.
// Bind its keychain item to the OS authentication ACL as well as to this device.
// The payment UI reads this item directly: the keystore itself opens one fresh
// system authentication prompt. A second JavaScript biometric prompt would only
// make the customer scan twice; it cannot strengthen the keychain ACL.
const TXN_PIN_KEYCHAIN_OPTS: SecureStore.SecureStoreOptions = {
  ...KEYCHAIN_OPTS,
  requireAuthentication: true,
  authenticationPrompt: 'Authenticate to use your Zitch transaction PIN',
};

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
    // Erase a token left by a pre-hardening build; never persist the replacement.
    await AsyncStorage.removeItem(TOKEN_KEY);
    return;
  }
  await SecureStore.setItemAsync(TOKEN_KEY, token, KEYCHAIN_OPTS);
}

export async function getToken(): Promise<string | null> {
  if (cachedToken !== undefined) return cachedToken;
  if (isWeb) {
    await AsyncStorage.removeItem(TOKEN_KEY);
    cachedToken = null;
    return null;
  }
  const token = await SecureStore.getItemAsync(TOKEN_KEY);
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
  if (!/^\d{6}$/.test(pin)) {
    throw new Error('A six-digit transaction PIN is required');
  }
  await SecureStore.setItemAsync(TXN_PIN_KEY, pin, TXN_PIN_KEYCHAIN_OPTS);
  await AsyncStorage.setItem(HAS_TXN_PIN_KEY, '1');
}

export async function getTransactionPin(): Promise<string | null> {
  if (isWeb) return null;
  return SecureStore.getItemAsync(TXN_PIN_KEY, TXN_PIN_KEYCHAIN_OPTS);
}

/** Whether a money PIN is cached for biometric pay — a non-secret boolean, so
 *  callers can gate UI without reading the PIN into memory. */
export async function hasTransactionPin(): Promise<boolean> {
  if (isWeb) return false;
  if ((await AsyncStorage.getItem(HAS_TXN_PIN_KEY)) === '1') return true;
  // A pre-hardening item without this marker was written without an OS-auth ACL.
  // Never silently migrate or read that secret: remove it and ask the customer to
  // enable biometric payments again, which writes a freshly protected item.
  await SecureStore.deleteItemAsync(TXN_PIN_KEY);
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

// The signed-in user's display name, remembered for the sign-in screen so a
// returning user (especially one unlocking with a fingerprint, who never types
// an identifier) is greeted by name instead of a blank "Welcome back". Not a
// credential — a name, in plain AsyncStorage — and cleared on sign-out.
const DISPLAY_NAME_KEY = 'z-display-name';

export async function saveDisplayName(name: string): Promise<void> {
  const clean = (name || '').trim();
  if (clean) await AsyncStorage.setItem(DISPLAY_NAME_KEY, clean);
}

export async function getDisplayName(): Promise<string> {
  return (await AsyncStorage.getItem(DISPLAY_NAME_KEY)) || '';
}

export async function clearSession(): Promise<void> {
  await clearToken();
  await clearTransactionPin();
  await AsyncStorage.multiRemove(['userID', 'sessionExpiration', 'UserEmail', 'UserPhone', 'lastActiveAt', 'z-locked', 'z-has-pin', BIOPAY_OFFERED_KEY, DISPLAY_NAME_KEY]);
}
