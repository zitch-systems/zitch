import { setBiometricTxnEnabled } from '@/lib/biometrics';
import { saveTransactionPin } from '@/lib/secureStore';
import { apiPost } from '@/lib/api';

/**
 * Turns on approving payments by scan, as Security Center's switch does: the
 * server checks the PIN first (a cached typo would be replayed at every payment
 * and could lock the account), then the PIN is kept behind the device's
 * biometric lock.
 */
export async function enableBiometricPay(pin: string): Promise<{ ok: true } | { ok: false; message: string }> {
  const response = await apiPost('/api/verify-transaction-pin/', { pin });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result?.success) {
    return { ok: false, message: result?.message || 'Could not verify your transaction PIN.' };
  }
  await saveTransactionPin(pin);
  await setBiometricTxnEnabled(true);
  return { ok: true };
}
