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
