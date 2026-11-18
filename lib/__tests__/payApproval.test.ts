import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  BIO_PAY_OFFER_EVERY_MS, __resetPayApproval, noteApproval, preferKeypadOnce, takeBiometricPayOffer, takeKeypadFirst,
} from '@/lib/payApproval';

jest.mock('@react-native-async-storage/async-storage', () => require('@react-native-async-storage/async-storage/jest/async-storage-mock'));
jest.mock('@/lib/biometrics', () => ({
  isBiometricTxnEnabled: jest.fn(async () => false),
  isBiometricAvailable: jest.fn(async () => true),
  biometricLabel: jest.fn(async () => 'fingerprint'),
}));
jest.mock('@/lib/secureStore', () => ({ hasTransactionPin: jest.fn(async () => false) }));

const bio = () => require('@/lib/biometrics');
const store = () => require('@/lib/secureStore');
const DAY = 24 * 60 * 60 * 1000;
const T0 = Date.UTC(2026, 10, 18, 9);

beforeEach(async () => {
  __resetPayApproval();
  await AsyncStorage.clear();
  bio().isBiometricTxnEnabled.mockResolvedValue(false);
  bio().isBiometricAvailable.mockResolvedValue(true);
  bio().biometricLabel.mockResolvedValue('fingerprint');
  store().hasTransactionPin.mockResolvedValue(false);
});

describe('the offer to turn on fingerprint payments', () => {
  it('comes after a payment approved with the typed PIN, once', async () => {
    noteApproval('pin');
    await expect(takeBiometricPayOffer(T0)).resolves.toBe('fingerprint');
    // the same payment never offers twice
    await expect(takeBiometricPayOffer(T0)).resolves.toBeNull();
  });

  it('comes at most once in five days', async () => {
    noteApproval('pin');
    await expect(takeBiometricPayOffer(T0)).resolves.toBe('fingerprint');
    noteApproval('pin');
    await expect(takeBiometricPayOffer(T0 + 4 * DAY)).resolves.toBeNull();
    noteApproval('pin');
    await expect(takeBiometricPayOffer(T0 + BIO_PAY_OFFER_EVERY_MS - 1)).resolves.toBeNull();
    noteApproval('pin');
    await expect(takeBiometricPayOffer(T0 + BIO_PAY_OFFER_EVERY_MS)).resolves.toBe('fingerprint');
  });

  it('is not made after a payment approved by scan', async () => {
    noteApproval('biometric');
    await expect(takeBiometricPayOffer(T0)).resolves.toBeNull();
    await expect(takeBiometricPayOffer(T0)).resolves.toBeNull();
  });

  it('is not made when fingerprint payments are already on', async () => {
    bio().isBiometricTxnEnabled.mockResolvedValue(true);
    store().hasTransactionPin.mockResolvedValue(true);
    noteApproval('pin');
    await expect(takeBiometricPayOffer(T0)).resolves.toBeNull();
    expect(await AsyncStorage.getItem('z-bio-pay-offered-at')).toBeNull();
  });

  it('is made when the switch is on but the PIN behind it is gone', async () => {
    bio().isBiometricTxnEnabled.mockResolvedValue(true);
    store().hasTransactionPin.mockResolvedValue(false);
    noteApproval('pin');
    await expect(takeBiometricPayOffer(T0)).resolves.toBe('fingerprint');
  });

  it('is not made on a phone that cannot scan, and does not use up the five days', async () => {
    bio().isBiometricAvailable.mockResolvedValue(false);
    noteApproval('pin');
    await expect(takeBiometricPayOffer(T0)).resolves.toBeNull();
    expect(await AsyncStorage.getItem('z-bio-pay-offered-at')).toBeNull();
  });

  it('names Face ID on a phone that has it', async () => {
    bio().biometricLabel.mockResolvedValue('face');
    noteApproval('pin');
    await expect(takeBiometricPayOffer(T0)).resolves.toBe('face');
  });

  it('is never blocked for good by a clock set back', async () => {
    await AsyncStorage.setItem('z-bio-pay-offered-at', String(T0 + 30 * DAY));
    noteApproval('pin');
    await expect(takeBiometricPayOffer(T0)).resolves.toBe('fingerprint');
  });
});

describe('Use Payment PIN', () => {
  it('opens the next PIN pad on the keypad, once', () => {
    preferKeypadOnce(T0);
    expect(takeKeypadFirst(T0 + 400)).toBe(true);
    expect(takeKeypadFirst(T0 + 500)).toBe(false);
  });

  it('lapses when no PIN pad opens soon after', () => {
    preferKeypadOnce(T0);
    expect(takeKeypadFirst(T0 + 16000)).toBe(false);
  });
});
