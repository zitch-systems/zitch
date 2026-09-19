import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Crypto from 'expo-crypto';

import { newIdempotencyKey } from '@/lib/api';
import { getSpendAccountNamespace } from '@/lib/secureStore';

type StoredAttempt = {
  key: string;
  createdAt: number;
};

const PREFIX = 'zitch:pending-spend:v1:';
const acquiring = new Map<string, Promise<string>>();

async function storageKey(
  accountNamespace: string,
  scope: string,
  fingerprint: string,
): Promise<string> {
  // Transaction details can include an account, phone, meter or RRR. Hash them
  // before using them as an AsyncStorage key so the durable retry marker does
  // not become a plaintext index of a customer's payment activity. The stable
  // authenticated-account namespace is inside the same digest: two customers
  // sharing a device can never see or reuse each other's attempt key.
  const digest = await Crypto.digestStringAsync(
    Crypto.CryptoDigestAlgorithm.SHA256,
    `${accountNamespace}\u0000${scope}\u0000${fingerprint}`,
  );
  return `${PREFIX}${scope.replace(/[^a-z0-9_-]/gi, '_')}:${digest}`;
}

async function requireAccountNamespace(): Promise<string> {
  const namespace = await getSpendAccountNamespace();
  if (!namespace) {
    // Never fall back to a device-global bucket. A missing namespace can make a
    // payment temporarily unavailable; sharing another customer's retry key can
    // make the wrong account appear to have paid or trigger a binding conflict.
    throw new Error('Authenticated account namespace is unavailable');
  }
  return namespace;
}

/**
 * Return the durable idempotency key for one exact spend attempt.
 *
 * The record is deliberately not expired: age cannot prove that an ambiguous
 * provider charge failed. It is removed only after the API reports a definite
 * success or failure. A module-level promise also closes the double-tap race
 * where two handlers could otherwise both create a key before storage settles.
 */
export async function acquireSpendAttempt(scope: string, fingerprint: string): Promise<string> {
  const accountNamespace = await requireAccountNamespace();
  const lock = `${accountNamespace}\u0000${scope}\u0000${fingerprint}`;
  const current = acquiring.get(lock);
  if (current) return current;

  const pending = (async () => {
    const itemKey = await storageKey(accountNamespace, scope, fingerprint);
    // A new key is safe only when storage positively says no marker exists.
    // Read errors and corrupt existing records are ambiguous: replacing either
    // can bypass the server key for a payment that was already delivered.
    const raw = await AsyncStorage.getItem(itemKey);
    if (raw !== null) {
      try {
        const stored = JSON.parse(raw) as Partial<StoredAttempt>;
        if (typeof stored.key === 'string' && stored.key.length > 0) {
          return stored.key;
        }
      } catch {
        throw new Error('Durable spend attempt is unreadable');
      }
      throw new Error('Durable spend attempt is invalid');
    }
    const key = newIdempotencyKey();
    await AsyncStorage.setItem(itemKey, JSON.stringify({ key, createdAt: Date.now() } satisfies StoredAttempt));
    return key;
  })();
  acquiring.set(lock, pending);
  try {
    return await pending;
  } finally {
    if (acquiring.get(lock) === pending) acquiring.delete(lock);
  }
}

/** Remove a resolved attempt, but only if it still contains the caller's key. */
export async function clearSpendAttempt(
  scope: string,
  fingerprint: string,
  expectedKey: string,
): Promise<void> {
  try {
    const accountNamespace = await requireAccountNamespace();
    const itemKey = await storageKey(accountNamespace, scope, fingerprint);
    const raw = await AsyncStorage.getItem(itemKey);
    if (!raw) return;
    const stored = JSON.parse(raw) as Partial<StoredAttempt>;
    if (stored.key === expectedKey) await AsyncStorage.removeItem(itemKey);
  } catch {
    // Fail closed. A delayed response clearing an old key must never delete a
    // newer unresolved attempt merely because its marker could not be read.
  }
}
