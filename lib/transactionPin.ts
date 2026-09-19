/** One transaction-PIN policy shared by setup, payment entry and biometric storage. */
export const TRANSACTION_PIN_LENGTH = 6;

export function isValidTransactionPin(pin: string): boolean {
  return new RegExp(`^\\d{${TRANSACTION_PIN_LENGTH}}$`).test(pin);
}
