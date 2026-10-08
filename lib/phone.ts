/** Convert a Nigerian phone number from API/WhatsApp form to the 11-digit form
 * shown in the mobile app. Unknown international numbers are left as digits so
 * callers can still display them without inventing a local prefix. */
export const localPhoneNumber = (value: unknown): string => {
  const digits = String(value ?? '').replace(/\D/g, '');
  if (/^234\d{10}$/.test(digits)) return `0${digits.slice(3)}`;
  if (/^\d{10}$/.test(digits)) return `0${digits}`;
  return digits.slice(0, 15);
};

/** Convert a selected Nigerian contact number to the 11-digit local form the
 * utility providers accept. Handles +234, 234, and the common 234(0) spelling. */
export const purchasablePhoneNumber = (value: unknown): string => {
  let digits = String(value ?? '').replace(/\D/g, '');
  if (/^2340\d{10}$/.test(digits)) digits = digits.slice(3);
  else if (/^234\d{10}$/.test(digits)) digits = `0${digits.slice(3)}`;
  else if (/^[789]\d{9}$/.test(digits)) digits = `0${digits}`;
  return /^0[789]\d{9}$/.test(digits) ? digits : '';
};

/** A QR only prefills a destination; every transfer still requires name enquiry
 * and explicit confirmation. Never extract a digit fragment from arbitrary text
 * or truncate a phone into a different bank account. */
export function paymentDestination(raw: unknown): { identifier: string; mode: 'bank' | 'zitch' } | null {
  const value = String(raw ?? '').trim();
  const classify = (candidate: string) => {
    if (/^\d{10}$/.test(candidate)) return { identifier: candidate, mode: 'bank' as const };
    const phone = purchasablePhoneNumber(candidate);
    return phone ? { identifier: phone, mode: 'zitch' as const } : null;
  };
  if (/^[+\d ()-]+$/.test(value)) return classify(value.replace(/[ ()-]/g, ''));
  try {
    const url = new URL(value);
    if (!['https:', 'zitch:'].includes(url.protocol)) return null;
    const values = ['account', 'acct', 'phone', 'identifier'].flatMap((key) => url.searchParams.getAll(key));
    // Multiple destinations are ambiguous, even if one of them looks valid.
    if (values.length !== 1 || !/^[+\d ()-]+$/.test(values[0])) return null;
    return classify(values[0].replace(/[ ()-]/g, ''));
  } catch { return null; }
}
