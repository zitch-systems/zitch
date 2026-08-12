import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  getTransactionPin,
  hasTransactionPin,
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
