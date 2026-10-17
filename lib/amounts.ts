// Mirrors backend common.http MIN_* so a screen never lets the customer reach
// the PIN step with an amount the server will refuse, nor refuses one the
// server (and the WhatsApp channel) accepts.
export const MIN_TRANSFER = 50;
export const MIN_AIRTIME = 50;
export const MIN_ELECTRICITY = 500;

/** Keep the entered monetary value intact. Reject ambiguous input rather than
 * deleting signs, decimal points or exponent markers and changing its value.
 * null means the edit is invalid; callers keep the last accepted input. */
export const normalizeAmountInput = (raw: string): string | null => {
  const value = raw.trim();
  if (!value) return '';
  if (!/^(?:\d+(?:\.\d{0,2})?|\.\d{0,2}|\d{1,3}(?:,\d{3})+(?:\.\d{0,2})?)$/.test(value)) return null;
  const [whole, fraction] = value.replace(/,/g, '').split('.');
  const integer = whole.replace(/^0+(?=\d)/, '') || '0';
  return fraction === undefined ? integer : `${integer}.${fraction}`;
};
