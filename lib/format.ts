// Money formatting — matches the prototype's money()/moneyk() helpers.
// Whole naira show no kobo ("₦15,000"); any kobo shows as two digits
// ("₦120.50", never "₦120.5").
export const money = (n: number | null | undefined): string => {
  const v = Number(n || 0);
  const kobo = Math.round(Math.abs(v) * 100) % 100 !== 0;
  return '₦' + v.toLocaleString('en-NG', {
    minimumFractionDigits: kobo ? 2 : 0,
    maximumFractionDigits: 2,
  });
};

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

/** "Today · 9:14am", "Yesterday · 6:40pm", "14 Nov · 6:40pm" — when, in a short activity row. */
export const whenLabel = (ts: number, now: number = Date.now()): string => {
  const d = new Date(ts);
  const h = d.getHours();
  const time = `${h % 12 || 12}:${String(d.getMinutes()).padStart(2, '0')}${h < 12 ? 'am' : 'pm'}`;
  const startOf = (t: number) => { const x = new Date(t); x.setHours(0, 0, 0, 0); return x.getTime(); };
  const days = Math.round((startOf(now) - startOf(ts)) / 86400000);
  const day = days === 0 ? 'Today' : days === 1 ? 'Yesterday' : `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return `${day} · ${time}`;
};

/**
 * "012 345 6789": a 10-digit account number grouped 3-3-4 for reading. One
 * grouping everywhere the number is shown (Home, Wallet, Add money, Bank
 * transfer, Account limits), so the same number never looks like two. Display
 * only: copy and share always use the raw digits. Anything that is not exactly
 * 10 digits is returned as given.
 */
export const groupAccountNumber = (value: string): string => {
  const d = String(value ?? '').replace(/\D/g, '');
  return d.length === 10 ? `${d.slice(0, 3)} ${d.slice(3, 6)} ${d.slice(6)}` : String(value ?? '');
};

/**
 * Two letters for a provider's tile: the start of its short code when the name
 * carries one ("Ikeja (IKEDC)" -> "IK", "P/H (PHED)" -> "PH"), else the first
 * letters of its first two words, else the start of its one word ("Kaduna" ->
 * "KA"). Never the same letter twice just because the name repeats it.
 */
export const providerInitials = (name: string): string => {
  const value = String(name ?? '');
  const code = /\(([A-Za-z]{2,})\)/.exec(value)?.[1];
  if (code) return code.slice(0, 2).toUpperCase();
  const words = value.replace(/\(.*?\)/g, ' ').replace(/[^A-Za-z ]/g, '').split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return (words[0] || '').slice(0, 2).toUpperCase();
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
