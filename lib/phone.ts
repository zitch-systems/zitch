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
  else if (/^\d{10}$/.test(digits)) digits = `0${digits}`;
  return /^0\d{10}$/.test(digits) ? digits : '';
};
