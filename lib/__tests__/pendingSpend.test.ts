import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Crypto from 'expo-crypto';

import { acquireSpendAttempt, clearSpendAttempt } from '../pendingSpend';
import { isRecoveredSpendResponse } from '../spendOutcome';

const mockGetSpendAccountNamespace = jest.fn<Promise<string>, []>();

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn(),
}));
jest.mock('expo-crypto', () => ({
  CryptoDigestAlgorithm: { SHA256: 'SHA-256' },
  digestStringAsync: jest.fn(async (_algorithm: string, value: string) => `hash:${value}`),
  randomUUID: jest.fn(),
}));
jest.mock('@/lib/secureStore', () => ({
  getSpendAccountNamespace: () => mockGetSpendAccountNamespace(),
}));

const asyncStorage = AsyncStorage as jest.Mocked<typeof AsyncStorage>;
const crypto = Crypto as jest.Mocked<typeof Crypto>;
const values = new Map<string, string>();

beforeEach(() => {
  values.clear();
  jest.clearAllMocks();
  let n = 0;
  let digestNumber = 0;
  const digests = new Map<string, string>();
  crypto.randomUUID.mockImplementation(() => `attempt-${++n}`);
  crypto.digestStringAsync.mockImplementation(async (_algorithm: string, value: string) => {
    if (!digests.has(value)) digests.set(value, `digest-${++digestNumber}`);
    return digests.get(value)!;
  });
  mockGetSpendAccountNamespace.mockResolvedValue('account-a-hash');
  asyncStorage.getItem.mockImplementation(async (key) => values.get(key) ?? null);
  asyncStorage.setItem.mockImplementation(async (key, value) => { values.set(key, value); });
  asyncStorage.removeItem.mockImplementation(async (key) => { values.delete(key); });
});

describe('durable spend attempts', () => {
  it('reuses the same key for the same attempt after component state is gone', async () => {
    const first = await acquireSpendAttempt('electricity', 'ikeja|meter|1000');
    const reopened = await acquireSpendAttempt('electricity', 'ikeja|meter|1000');

    expect(reopened).toBe(first);
    expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  });

  it('uses a different key for different transaction details', async () => {
    const first = await acquireSpendAttempt('airtime', 'mtn|0801|100');
    const second = await acquireSpendAttempt('airtime', 'mtn|0801|200');

    expect(second).not.toBe(first);
  });

  it('keeps the edited attempt durable after an earlier PIN rejection', async () => {
    const rejectedA = await acquireSpendAttempt('airtime', 'mtn|0801|100');
    // No clear: a PIN error lets the customer retry the same authorization.
    const ambiguousB = await acquireSpendAttempt('airtime', 'mtn|0801|200');

    expect(ambiguousB).not.toBe(rejectedA);
    // Simulate component state being destroyed after B's response is unknown.
    const reopenedB = await acquireSpendAttempt('airtime', 'mtn|0801|200');
    expect(reopenedB).toBe(ambiguousB);
  });

  it('isolates identical attempts made by two accounts on one device', async () => {
    mockGetSpendAccountNamespace.mockResolvedValue('account-a-hash');
    const firstUser = await acquireSpendAttempt('airtime', 'mtn|0801|100');

    mockGetSpendAccountNamespace.mockResolvedValue('account-b-hash');
    const secondUser = await acquireSpendAttempt('airtime', 'mtn|0801|100');

    expect(secondUser).not.toBe(firstUser);

    // Even knowing A's random key cannot make B clear A's namespaced record.
    await clearSpendAttempt('airtime', 'mtn|0801|100', firstUser);

    mockGetSpendAccountNamespace.mockResolvedValue('account-a-hash');
    expect(await acquireSpendAttempt('airtime', 'mtn|0801|100')).toBe(firstUser);
    expect([...values.keys()].join('|')).not.toContain('account-a-hash');
    expect([...values.keys()].join('|')).not.toContain('account-b-hash');
  });

  it('fails closed instead of using a device-global bucket without an account', async () => {
    mockGetSpendAccountNamespace.mockResolvedValue('');
    await expect(acquireSpendAttempt('airtime', 'mtn|0801|100'))
      .rejects.toThrow('account namespace');
    expect(asyncStorage.setItem).not.toHaveBeenCalled();
  });

  it('does not mint or overwrite a key when the durable read fails', async () => {
    asyncStorage.getItem.mockRejectedValueOnce(new Error('storage unavailable'));

    await expect(acquireSpendAttempt('airtime', 'mtn|0801|100'))
      .rejects.toThrow('storage unavailable');
    expect(crypto.randomUUID).not.toHaveBeenCalled();
    expect(asyncStorage.setItem).not.toHaveBeenCalled();
  });

  it.each(['{not-json', JSON.stringify({ createdAt: 123 })])(
    'does not replace a malformed existing marker: %s',
    async (raw) => {
      asyncStorage.getItem.mockResolvedValueOnce(raw);

      await expect(acquireSpendAttempt('airtime', 'mtn|0801|100')).rejects.toThrow(
        /Durable spend attempt/,
      );
      expect(crypto.randomUUID).not.toHaveBeenCalled();
      expect(asyncStorage.setItem).not.toHaveBeenCalled();
    },
  );

  it('never clears a marker when it cannot verify the stored key', async () => {
    asyncStorage.getItem.mockRejectedValueOnce(new Error('storage unavailable'));
    await clearSpendAttempt('airtime', 'mtn|0801|100', 'old-response-key');
    expect(asyncStorage.removeItem).not.toHaveBeenCalled();

    asyncStorage.getItem.mockResolvedValueOnce('{not-json');
    await clearSpendAttempt('airtime', 'mtn|0801|100', 'old-response-key');
    expect(asyncStorage.removeItem).not.toHaveBeenCalled();
  });

  it('clears only the matching resolved attempt', async () => {
    const first = await acquireSpendAttempt('exam', 'waec|0801|1');
    await clearSpendAttempt('exam', 'waec|0801|1', 'some-other-key');
    expect(await acquireSpendAttempt('exam', 'waec|0801|1')).toBe(first);

    await clearSpendAttempt('exam', 'waec|0801|1', first);
    expect(await acquireSpendAttempt('exam', 'waec|0801|1')).not.toBe(first);
  });

  it('turns an unknown-then-settled replay into a recovered attempt before a fresh key', async () => {
    const original = await acquireSpendAttempt('airtime', 'mtn|0801|100');
    // Unknown delivery keeps the marker. A later retry gets the same server key.
    expect(await acquireSpendAttempt('airtime', 'mtn|0801|100')).toBe(original);
    const replay = { success: true, duplicate: true, reference: 'OLD-REF' };
    expect(isRecoveredSpendResponse(replay, true)).toBe(true);

    await clearSpendAttempt('airtime', 'mtn|0801|100', original);
    // A later intended purchase of the same value must require a new key.
    expect(await acquireSpendAttempt('airtime', 'mtn|0801|100')).not.toBe(original);
  });
});
