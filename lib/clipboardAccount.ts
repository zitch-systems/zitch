/**
 * What a copied number means for a transfer.
 *
 * A copied 10-digit number is an account number. A copied Nigerian mobile
 * number is turned into the account number most banks and wallets give that
 * phone (OPay, PalmPay, Moniepoint and others): the phone without its leading
 * 0, so 0808 110 3848 becomes 8081103848; +234 808 110 3848 does the same.
 *
 * Only a clipboard that holds the number alone counts (spaces, dashes, dots and
 * brackets between the digits are fine). A number inside a longer message is
 * never picked out, and nothing is sent anywhere here: the Send money page runs
 * the name enquiry, and the customer confirms the name and bank before paying.
 */
export type CopiedAccount = {
  /** The 10-digit account number to send to. */
  account: string;
  /** The 11-digit phone number it came from, when it came from one. */
  phone?: string;
};

const MOBILE = /^0[789][01]\d{8}$/;

export const copiedAccount = (text: unknown): CopiedAccount | null => {
  const raw = String(text ?? '').trim();
  if (!raw || raw.length > 24 || !/^[+\d\s().-]+$/.test(raw)) return null;
  const digits = raw.replace(/\D/g, '');
  const international = raw.startsWith('+');
  if (!international && /^\d{10}$/.test(digits)) return { account: digits };
  if (!international && MOBILE.test(digits)) return { account: digits.slice(1), phone: digits };
  if (/^234\d{10}$/.test(digits) && MOBILE.test(`0${digits.slice(3)}`)) {
    return { account: digits.slice(3), phone: `0${digits.slice(3)}` };
  }
  return null;
};

/** "0808 110 3848" — a local mobile number grouped for reading. */
export const groupPhone = (phone: string): string =>
  /^\d{11}$/.test(phone) ? `${phone.slice(0, 4)} ${phone.slice(4, 7)} ${phone.slice(7)}` : phone;
