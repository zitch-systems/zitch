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

export async function saveToken(token: string): Promise<void> {
  if (isWeb) {
    await AsyncStorage.setItem(TOKEN_KEY, token);
    return;
  }
  await SecureStore.setItemAsync(TOKEN_KEY, token);
}

export async function getToken(): Promise<string | null> {
  if (isWeb) {
    return AsyncStorage.getItem(TOKEN_KEY);
  }
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export async function clearToken(): Promise<void> {
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

export async function saveTransactionPin(pin: string): Promise<void> {
  if (isWeb) return; // don't persist the money PIN in unencrypted web storage
  await SecureStore.setItemAsync(TXN_PIN_KEY, pin);
}

export async function getTransactionPin(): Promise<string | null> {
  if (isWeb) return null;
  return SecureStore.getItemAsync(TXN_PIN_KEY);
}

export async function clearTransactionPin(): Promise<void> {
  if (isWeb) return;
  await SecureStore.deleteItemAsync(TXN_PIN_KEY);
}

export async function clearSession(): Promise<void> {
  await clearToken();
  await clearTransactionPin();
  await AsyncStorage.multiRemove(['userID', 'sessionExpiration', 'UserEmail', 'UserPhone', 'lastActiveAt', 'z-locked']);
}
