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
export async function clearSession(): Promise<void> {
  await clearToken();
  await AsyncStorage.multiRemove(['userID', 'sessionExpiration', 'UserEmail', 'UserPhone', 'lastActiveAt']);
}
