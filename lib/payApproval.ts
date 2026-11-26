import { Platform } from 'react-native';
import type { biometricLabel as BiometricLabel } from '@/lib/biometrics';

/**
 * How payments are approved on this device (18 Nov 2026).
 *
 * The default is the fingerprint (or Face ID) when the customer has turned it
 * on for payments, and the typed PIN when not. Choosing "Use Payment PIN" on the
 * confirm sheet opens that one payment straight on the keypad. After a payment
 * approved with the typed PIN, the receipt offers to turn the fingerprint on,
 * at most once every five days.
 */

/** The fewest days between two offers to turn on fingerprint payments. */
export const BIO_PAY_OFFER_DAYS = 5;
export const BIO_PAY_OFFER_EVERY_MS = BIO_PAY_OFFER_DAYS * 24 * 60 * 60 * 1000;
const OFFERED_AT_KEY = 'z-bio-pay-offered-at';

// In memory only: how the last PIN pad was answered, and a one-off "keypad
// first" choice. Neither is a secret and neither needs to outlive the app.
let lastApproval: 'pin' | 'biometric' | null = null;
let keypadFirstUntil = 0;

/** The PIN pad records how it was answered: typed digits or a scan. */
export function noteApproval(kind: 'pin' | 'biometric'): void {
  lastApproval = kind;
}

/** "Use Payment PIN" on the confirm sheet: the next PIN pad opens on the keypad
 *  instead of starting a scan. It lapses if no pad opens soon after. */
export function preferKeypadOnce(now = Date.now()): void {
  keypadFirstUntil = now + 15000;
}

/** Read once by the PIN pad as it opens. */
export function takeKeypadFirst(now = Date.now()): boolean {
  const on = now < keypadFirstUntil;
  keypadFirstUntil = 0;
  return on;
}

export type BioKind = 'face' | 'fingerprint' | 'biometrics';

/**
 * Called by a payment receipt. When that payment was approved with the typed
 * PIN, the device can scan, fingerprint payments are not on, and the last
 * offer is five or more days old, this records the offer and returns the kind
 * of scan to name. Otherwise null. Reading it clears the note, so one payment
 * makes at most one offer.
 */
export async function takeBiometricPayOffer(now = Date.now()): Promise<BioKind | null> {
  const typed = lastApproval === 'pin';
  lastApproval = null;
  // Payment approval by scan exists only in the mobile app.
  if (!typed || Platform.OS === 'web') return null;
  // Loaded here, not at the top: the PIN pad and every receipt import this
  // module, and neither should need the keychain or storage just to draw (nor a
  // mock of them in every test that renders one).
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { isBiometricTxnEnabled, isBiometricAvailable, biometricLabel } = require('@/lib/biometrics') as {
    isBiometricTxnEnabled: () => Promise<boolean>; isBiometricAvailable: () => Promise<boolean>; biometricLabel: typeof BiometricLabel;
  };
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { hasTransactionPin } = require('@/lib/secureStore') as { hasTransactionPin: () => Promise<boolean> };
  const [txnOn, cached] = await Promise.all([isBiometricTxnEnabled(), hasTransactionPin()]);
  if (txnOn && cached) return null;   // already on: the PIN was the customer's choice this time
  if (!(await isBiometricAvailable())) return null;
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const storage = require('@react-native-async-storage/async-storage');
  const AsyncStorage = storage.default ?? storage;
  const last = Number(await AsyncStorage.getItem(OFFERED_AT_KEY)) || 0;
  // A clock set back leaves `last` in the future; that never blocks the offer for good.
  if (last && now >= last && now - last < BIO_PAY_OFFER_EVERY_MS) return null;
  await AsyncStorage.setItem(OFFERED_AT_KEY, String(now));
  return biometricLabel();
}

/** Tests only. */
export function __resetPayApproval(): void {
  lastApproval = null;
  keypadFirstUntil = 0;
}
