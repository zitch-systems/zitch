/** One transaction-PIN policy shared by setup, payment entry and biometric storage. */
export const TRANSACTION_PIN_LENGTH = 6;

export function isValidTransactionPin(pin: string): boolean {
  return new RegExp(`^\\d{${TRANSACTION_PIN_LENGTH}}$`).test(pin);
}

/** The server's refusal for an account that never set a transaction PIN. It is
 *  an HTTP 403 with no `code` (common.http.verify_transaction_pin), so the
 *  message is the only thing that identifies it. */
export function isMissingTransactionPinMessage(message: unknown): boolean {
  return typeof message === 'string' && /no transaction pin set/i.test(message);
}
