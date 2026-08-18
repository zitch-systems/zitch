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
// The long-lived half of the session. Kept in the same keychain item class as the
// access token, and on web kept nowhere at all: a credential that survives a
// reload is exactly what a browser session must not persist.
const REFRESH_KEY = 'refresh_token';
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
let cachedRefresh: string | null | undefined;

export async function saveToken(token: string): Promise<void> {
  cachedToken = token;
  if (isWeb) {
    // Erase a token left by a pre-hardening build; never persist the replacement.
    await AsyncStorage.removeItem(TOKEN_KEY);
    return;
  }
  await SecureStore.setItemAsync(TOKEN_KEY, token, KEYCHAIN_OPTS);
}

/**
 * Persist a rotated refresh token.
 *
 * The server burns the old token on every refresh, and a second presentation of
 * a burnt one is treated as theft and kills the whole chain. So this write must
 * land before the replaced token is dropped — and the write happens BEFORE the
 * retried request goes out, never after it, or a crash in between would sign the
 * customer out and look like a break-in in the logs.
 *
 * An empty value clears the stored token rather than writing "" — the keychain
 * would happily store an empty string, which then reads back as a token and gets
 * sent as one.
 */
export async function saveRefreshToken(token: string): Promise<void> {
  if (!token) { await clearRefreshToken(); return; }
  cachedRefresh = token;
  if (isWeb) {
    await AsyncStorage.removeItem(REFRESH_KEY);
    return;
  }
  await SecureStore.setItemAsync(REFRESH_KEY, token, KEYCHAIN_OPTS);
}

export async function getRefreshToken(): Promise<string | null> {
  if (cachedRefresh !== undefined) return cachedRefresh;
  if (isWeb) {
    await AsyncStorage.removeItem(REFRESH_KEY);
    cachedRefresh = null;
    return null;
  }
  const token = await SecureStore.getItemAsync(REFRESH_KEY);
  cachedRefresh = token;
  return token;
}

/**
 * Store a session from an authenticating response (sign-in, signup OTP, password
 * reset). One helper for all three so a new surface cannot ship storing the
 * access token and silently forgetting the refresh token — which would look
 * perfectly fine for a day and then sign the customer out.
 *
 * The refresh token is written FIRST: if only one of the two lands, the session
 * that survives should be the recoverable one.
 */
export async function storeSession(result: { access_token?: string; refresh_token?: string }): Promise<void> {
  if (result?.refresh_token) await saveRefreshToken(result.refresh_token);
  if (result?.access_token) await saveToken(result.access_token);
}

export async function clearRefreshToken(): Promise<void> {
  cachedRefresh = null;
  if (isWeb) {
    await AsyncStorage.removeItem(REFRESH_KEY);
    return;
  }
  await SecureStore.deleteItemAsync(REFRESH_KEY);
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

// The identifier the customer last signed in WITH — their email or phone, so the
// sign-in field is filled for them instead of blank every time.
//
// Not a credential: it is the half of the pair that is printed on their bank
// statements and typed into every other app they own, and it is worthless
// without the password. Deliberately survives sign-out, exactly like the
// biometric-offer flag: "which account is this" is not a secret, and a returning
// customer retyping their own email is the app failing to recognise someone it
// has known for months. It is dropped only when the customer signs in as
// somebody else, which overwrites it.
const LAST_IDENTIFIER_KEY = 'z-last-identifier';

export async function rememberIdentifier(identifier: string): Promise<void> {
  const clean = (identifier || '').trim();
  if (clean) await AsyncStorage.setItem(LAST_IDENTIFIER_KEY, clean);
}

export async function getRememberedIdentifier(): Promise<string> {
  return (await AsyncStorage.getItem(LAST_IDENTIFIER_KEY)) || '';
}

export async function clearSession(): Promise<void> {
  await clearToken();
  await clearRefreshToken();
  await clearTransactionPin();
  // BIOPAY_OFFERED_KEY is deliberately NOT cleared. It records that we have
  // already asked this person once whether they want to approve payments with a
  // fingerprint — a UI preference, not a credential, and nothing about signing
  // out makes that question unasked.
  //
  // Clearing it is why the offer came back after "every transaction". Nobody was
  // signing out: enforceHardExpiry() calls clearSession() after HARD_EXPIRE_MS
  // of inactivity, and while that cap was twelve hours a customer who opened the
  // app about once a day crossed it every single time. So the flag was wiped
  // daily and the nag returned on the next transfer, exactly as if it had never
  // been set. (The cap is seven days now — see lib/session — but the flag still
  // does not belong to a session.)
  //
  // LAST_IDENTIFIER_KEY is not cleared either, and for the same reason: it says
  // which account this device belongs to, not how to get into it.
  await AsyncStorage.multiRemove(['userID', 'sessionExpiration', 'UserEmail', 'UserPhone', 'lastActiveAt', 'z-locked', 'z-has-pin', DISPLAY_NAME_KEY]);
}
