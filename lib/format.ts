// Money formatting — matches the prototype's money()/moneyk() helpers.
export const money = (n: number | null | undefined): string =>
  '₦' +
  Number(n || 0).toLocaleString('en-NG', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

export const moneyk = (n: number | null | undefined): string =>
  '₦' + Number(n || 0).toLocaleString('en-NG');

/**
 * Normalizes what the user typed into an amount field to a plain numeric string
 * the API can take verbatim ("1234.5"), keeping kobo: digits and at most one
 * decimal point, at most 2 decimal places, no thousands separators and no
 * leading zeros. The decimal point is kept while it is still trailing ("12.")
 * so the user can carry on typing the kobo.
 */
export const sanitizeAmount = (raw: string): string => {
  const cleaned = String(raw ?? '').replace(/[^\d.]/g, '');
  const [first, ...rest] = cleaned.split('.');
  // Everything past a second "." is folded into the kobo part rather than
  // dropping the keystroke, so "1.2.3" reads as 1.23.
  const naira = first.replace(/^0+(?=\d)/, '');
  if (!rest.length) return naira;
  return `${naira || '0'}.${rest.join('').slice(0, 2)}`;
};

/**
 * The display form of a raw amount string: thousands grouped with commas, kobo
 * left exactly as typed ("1234.5" -> "1,234.5"). Pairs with sanitizeAmount —
 * state keeps the raw value, the field shows this.
 */
export const formatAmountInput = (raw: string): string => {
  const v = sanitizeAmount(raw);
  if (!v) return '';
  const [naira, kobo] = v.split('.');
  const grouped = (naira || '0').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return v.includes('.') ? `${grouped}.${kobo ?? ''}` : grouped;
};

/**
 * Rejects trivially-guessable transaction PINs at setup: all-same digits
 * (0000, 1111…) and straight ascending/descending runs (1234, 4321, 0123…).
 * These dominate real-world PIN choices and hand a thief with the unlocked
 * phone a strong head start within the server's 5-try lockout window.
 */
export const isTrivialPin = (pin: string): boolean => {
  if (!/^\d{4,}$/.test(pin)) return false; // not a complete numeric PIN — let other checks handle it
  if (/^(\d)\1+$/.test(pin)) return true; // all identical digits
  const ascending = pin.split('').every((d, i) => i === 0 || +d === +pin[i - 1] + 1);
  const descending = pin.split('').every((d, i) => i === 0 || +d === +pin[i - 1] - 1);
  return ascending || descending;
};
