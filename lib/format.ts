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

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-08" — sortable, and the identity a month group is keyed by. */
export const monthKey = (ts: number): string => {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
};

/** "Aug 2026" — the heading over a month's transactions. */
export const monthLabel = (key: string): string => {
  const [y, m] = key.split('-');
  return `${MONTHS[Number(m) - 1] ?? m} ${y}`;
};

const ordinal = (n: number): string => {
  // 11th/12th/13th are the exception the naive last-digit rule gets wrong.
  if (n % 100 >= 11 && n % 100 <= 13) return `${n}th`;
  return `${n}${['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`;
};

/** "Aug 15th, 02:24" — a transaction's own timestamp in a list row. */
export const txnDate = (ts: number): string => {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${MONTHS[d.getMonth()]} ${ordinal(d.getDate())}, ${hh}:${mm}`;
};

/** "15 Jul, 2026" — the date shown in a statement's start/end fields. */
export const dayLabel = (ts: number): string => {
  const d = new Date(ts);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${d.getFullYear()}`;
};

/** "2026-07-15" — the wire form the statement API takes. */
export const isoDay = (ts: number): string => {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

/**
 * Rejects trivially-guessable transaction PINs at setup: repeated blocks
 * (000000, 121212, 123123) and cyclic ascending/descending runs
 * (123456, 654321, 789012…).
 * These dominate real-world PIN choices and hand a thief with the unlocked
 * phone a strong head start within the server's 5-try lockout window.
 */
export const isTrivialPin = (pin: string): boolean => {
  if (!/^\d{4,}$/.test(pin)) return false; // not a complete numeric PIN — let other checks handle it
  for (let width = 1; width < pin.length; width += 1) {
    if (pin.length % width === 0 && pin.slice(0, width).repeat(pin.length / width) === pin) {
      return true;
    }
  }
  const ascending = pin
    .split('')
    .every((d, i) => i === 0 || (+d - +pin[i - 1] + 10) % 10 === 1);
  const descending = pin
    .split('')
    .every((d, i) => i === 0 || (+pin[i - 1] - +d + 10) % 10 === 1);
  return ascending || descending;
};
