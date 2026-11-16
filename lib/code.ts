/**
 * One-time codes: what to keep from typed or pasted text.
 *
 * People paste the whole SMS or email ("Your Zitch code is 482 193. Don't share
 * it."), not just the digits. Prefer a run of exactly `length` digits, so other
 * numbers in the message (a phone number, "10 minutes") are ignored; otherwise
 * keep the digits in order, up to `length` (typing, or "482 193").
 */
export function extractCode(text: string, length = 6): string {
  const value = String(text ?? '');
  const exact = (value.match(/\d+/g) || []).find((run) => run.length === length);
  if (exact) return exact;
  return value.replace(/\D/g, '').slice(0, length);
}
