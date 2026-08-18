import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  clearSession,
  getRememberedIdentifier,
  getTransactionPin,
  hasTransactionPin,
  rememberIdentifier,
  saveTransactionPin,
} from '../secureStore';

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn(),
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'whenUnlockedThisDeviceOnly',
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn(),
  multiRemove: jest.fn(),
}));

const secure = SecureStore as jest.Mocked<typeof SecureStore>;
const asyncStore = AsyncStorage as jest.Mocked<typeof AsyncStorage>;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('transaction PIN storage', () => {
  const protectedOptions = expect.objectContaining({
    keychainAccessible: 'whenUnlockedThisDeviceOnly',
    requireAuthentication: true,
  });

  it('writes and reads the PIN through an OS-authenticated keychain item', async () => {
    secure.getItemAsync.mockResolvedValue('438921');
    await saveTransactionPin('438921');
    expect(secure.setItemAsync).toHaveBeenCalledWith('txn_pin', '438921', protectedOptions);
    expect(asyncStore.setItem).toHaveBeenCalledWith('z-has-pin', '1');

    expect(await getTransactionPin()).toBe('438921');
    expect(secure.getItemAsync).toHaveBeenCalledWith('txn_pin', protectedOptions);
  });

  it('refuses to cache a legacy or malformed PIN', async () => {
    await expect(saveTransactionPin('1234')).rejects.toThrow('six-digit');
    await expect(saveTransactionPin('abcdef')).rejects.toThrow('six-digit');
    expect(secure.setItemAsync).not.toHaveBeenCalled();
  });

  it('deletes an unmarked legacy item instead of reading an unprotected secret', async () => {
    asyncStore.getItem.mockResolvedValue(null);
    expect(await hasTransactionPin()).toBe(false);
    expect(secure.getItemAsync).not.toHaveBeenCalled();
    expect(secure.deleteItemAsync).toHaveBeenCalledWith('txn_pin');
  });
});

describe('what survives a sign-out', () => {
  it('remembers which account this device belongs to', async () => {
    await rememberIdentifier('  ada@example.com  ');
    expect(asyncStore.setItem).toHaveBeenCalledWith('z-last-identifier', 'ada@example.com');
    asyncStore.getItem.mockResolvedValue('ada@example.com');
    expect(await getRememberedIdentifier()).toBe('ada@example.com');
  });

  it('never stores a blank identifier over a good one', async () => {
    await rememberIdentifier('   ');
    expect(asyncStore.setItem).not.toHaveBeenCalled();
  });

  it('keeps the identifier and the biometric-offer flag through a sign-out', async () => {
    // Both answer "which account is this / have we already asked", neither is a
    // credential, and clearing them is what made a returning customer retype
    // their own email and re-answer a question they had already answered.
    await clearSession();
    const removed = asyncStore.multiRemove.mock.calls.flat().flat();
    expect(removed).not.toContain('z-last-identifier');
    expect(removed).not.toContain('z-biopay-offered');
    // ...while everything that IS a credential goes.
    expect(secure.deleteItemAsync).toHaveBeenCalledWith('access_token');
    expect(secure.deleteItemAsync).toHaveBeenCalledWith('refresh_token');
    expect(secure.deleteItemAsync).toHaveBeenCalledWith('txn_pin');
  });
});
