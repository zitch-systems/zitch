// In-memory keychain so we can assert the token cache avoids redundant reads.
const mockStore: Record<string, string> = {};
const mockGetItemAsync = jest.fn((k: string) => Promise.resolve(mockStore[k] ?? null));
const mockSetItemAsync = jest.fn((k: string, v: string, _options?: unknown) => { mockStore[k] = v; return Promise.resolve(); });
const mockDeleteItemAsync = jest.fn((k: string) => { delete mockStore[k]; return Promise.resolve(); });

jest.mock('expo-secure-store', () => ({
  getItemAsync: (k: string) => mockGetItemAsync(k),
  setItemAsync: (...args: [string, string, unknown?]) => mockSetItemAsync(...args),
  deleteItemAsync: (k: string) => mockDeleteItemAsync(k),
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: () => Promise.resolve(null),
    setItem: () => Promise.resolve(),
    removeItem: () => Promise.resolve(),
    multiRemove: () => Promise.resolve(),
  },
}));

import {
  saveToken,
  getToken,
  clearToken,
  saveTransactionPin,
  getTransactionPin,
  clearTransactionPin,
  saveRefreshToken, getRefreshToken, storeSession, clearSession, getSessionGeneration,
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
    await saveTransactionPin('135790');
    expect(mockSetItemAsync).toHaveBeenCalledWith(
      'txn_pin',
      '135790',
      expect.objectContaining({ requireAuthentication: true }),
    );
    expect(await getTransactionPin()).toBe('135790');
    await clearTransactionPin();
    expect(await getTransactionPin()).toBeNull();
  });

  it('refuses legacy, malformed and non-numeric PINs', async () => {
    await expect(saveTransactionPin('1234')).rejects.toThrow(
      'A 6-digit transaction PIN is required',
    );
    await expect(saveTransactionPin('1234567')).rejects.toThrow(
      'A 6-digit transaction PIN is required',
    );
    await expect(saveTransactionPin('12a456')).rejects.toThrow(
      'A 6-digit transaction PIN is required',
    );
  });
});


describe('authenticated session replacement', () => {
  beforeEach(() => {
    mockSetItemAsync.mockClear();
    mockDeleteItemAsync.mockClear();
  });

  it('does not use a token whose native persistence failed', async () => {
    await clearToken();
    mockSetItemAsync.mockRejectedValueOnce(new Error('keystore unavailable'));
    await expect(saveToken('not-persisted')).rejects.toThrow('keystore unavailable');
    expect(await getToken()).toBeNull();
  });

  it('replaces the previous account refresh token and biometric PIN', async () => {
    await saveToken('old-access');
    await saveRefreshToken('old-refresh');
    await saveTransactionPin('135790');
    await storeSession({ access_token: 'new-access' });
    expect(await getToken()).toBe('new-access');
    expect(await getRefreshToken()).toBeNull();
    expect(await getTransactionPin()).toBeNull();
  });

  it('invalidates in-flight account work before awaiting logout storage', async () => {
    const generation = getSessionGeneration();
    const logout = clearSession();
    expect(getSessionGeneration()).toBe(generation + 1);
    await logout;
    const rotationGeneration = getSessionGeneration();
    await saveToken('same-session-rotation');
    await saveRefreshToken('same-session-refresh');
    expect(getSessionGeneration()).toBe(rotationGeneration);
  });

  it('still deletes refresh tokens and payment PIN when token deletion fails', async () => {
    await saveRefreshToken('old-refresh');
    await saveTransactionPin('135790');
    mockDeleteItemAsync.mockRejectedValueOnce(new Error('cannot delete access token'));
    await expect(clearSession()).rejects.toThrow('cannot delete access token');
    expect(mockDeleteItemAsync).toHaveBeenCalledWith('refresh_token');
    expect(mockDeleteItemAsync).toHaveBeenCalledWith('txn_pin');
    expect(await getRefreshToken()).toBeNull();
    expect(await getTransactionPin()).toBeNull();
  });
});

it('does not repopulate the cache from a keychain read that completes after logout', async () => {
  let isolated!: typeof import('../secureStore');
  jest.isolateModules(() => { isolated = jest.requireActual('../secureStore'); });
  let finishRead!: (value: string) => void;
  mockGetItemAsync.mockReturnValueOnce(new Promise((resolve) => { finishRead = resolve; }));
  const oldRead = isolated.getToken();
  await isolated.clearSession();
  finishRead('logged-out-access');
  await expect(oldRead).resolves.toBeNull();
  await expect(isolated.getToken()).resolves.toBeNull();
});

it('serializes an in-flight native credential write before the newer logout deletion', async () => {
  let finishWrite!: () => void;
  let started!: () => void;
  const writing = new Promise<void>((resolve) => { started = resolve; });
  const release = new Promise<void>((resolve) => { finishWrite = resolve; });
  mockSetItemAsync.mockImplementationOnce(async (key, value) => {
    started();
    await release;
    mockStore[key] = value;
  });
  const oldSave = saveToken('old-access');
  await writing;
  const logout = clearSession();
  finishWrite();
  await Promise.all([oldSave, logout]);
  expect(mockStore.access_token).toBeUndefined();
  expect(await getToken()).toBeNull();
});

it('stops an old sign-in continuation without deleting the newer account session', async () => {
  let finishWrite!: () => void;
  let started!: () => void;
  const writing = new Promise<void>((resolve) => { started = resolve; });
  const release = new Promise<void>((resolve) => { finishWrite = resolve; });
  mockSetItemAsync.mockImplementationOnce(async (key, value) => {
    started();
    await release;
    mockStore[key] = value;
  });
  const oldSignIn = storeSession({ access_token: 'old-access', refresh_token: 'old-refresh' });
  const rejectedOld = expect(oldSignIn).rejects.toThrow('Your session changed');
  await writing;
  const newSignIn = storeSession({ access_token: 'new-access', refresh_token: 'new-refresh' });
  finishWrite();
  await Promise.all([rejectedOld, newSignIn]);
  expect(await getToken()).toBe('new-access');
  expect(await getRefreshToken()).toBe('new-refresh');
  expect(mockStore.access_token).toBe('new-access');
  expect(mockStore.refresh_token).toBe('new-refresh');
});
