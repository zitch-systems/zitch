// In-memory keychain so we can assert the token cache avoids redundant reads.
const mockStore: Record<string, string> = {};
const mockGetItemAsync = jest.fn((k: string) => Promise.resolve(mockStore[k] ?? null));
const mockSetItemAsync = jest.fn((k: string, v: string) => { mockStore[k] = v; return Promise.resolve(); });
const mockDeleteItemAsync = jest.fn((k: string) => { delete mockStore[k]; return Promise.resolve(); });

jest.mock('expo-secure-store', () => ({
  getItemAsync: (k: string) => mockGetItemAsync(k),
  setItemAsync: (k: string, v: string) => mockSetItemAsync(k, v),
  deleteItemAsync: (k: string) => mockDeleteItemAsync(k),
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: { removeItem: () => Promise.resolve(), multiRemove: () => Promise.resolve() },
}));

import {
  saveToken,
  getToken,
  clearToken,
  saveTransactionPin,
  getTransactionPin,
  clearTransactionPin,
} from '@/lib/secureStore';

describe('access token storage', () => {
  it('persists to the keychain and serves subsequent reads from cache', async () => {
    await saveToken('tok-123');
    mockGetItemAsync.mockClear();
    expect(await getToken()).toBe('tok-123'); // from in-memory cache
    expect(await getToken()).toBe('tok-123');
    expect(mockGetItemAsync).not.toHaveBeenCalled(); // never re-read the keychain
  });

  it('clears the token and reports absence without hitting the keychain', async () => {
    await saveToken('tok-xyz');
    await clearToken();
    mockGetItemAsync.mockClear();
    expect(await getToken()).toBeNull();
    expect(mockGetItemAsync).not.toHaveBeenCalled();
    expect(mockDeleteItemAsync).toHaveBeenCalledWith('access_token');
  });
});

describe('transaction PIN storage', () => {
  it('round-trips the PIN through the keychain and clears it', async () => {
    await saveTransactionPin('1234');
    expect(mockSetItemAsync).toHaveBeenCalledWith('txn_pin', '1234');
    expect(await getTransactionPin()).toBe('1234');
    await clearTransactionPin();
    expect(await getTransactionPin()).toBeNull();
  });
});
