// Pure helpers for the Fixed Save UI, extracted from the screen so they can be
// unit-tested without rendering React Native.

/** Whole days from `now` until an ISO (YYYY-MM-DD) date; negative once passed. */
export const daysUntil = (iso: string, now: number = Date.now()): number => {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return 0;
  return Math.ceil((d.getTime() - now) / 86_400_000);
};

/** "15% p.a" from a decimal rate string like "0.1500". */
export const ratePct = (rate: string): string => `${(Number(rate) * 100).toFixed(0)}% p.a`;

/** Lock progress 0..1: full once matured, else elapsed/total by days remaining. */
export const lockProgress = (matured: boolean, durationDays: number, daysLeft: number): number => {
  if (matured) return 1;
  if (durationDays <= 0) return 0;
  return Math.min(1, Math.max(0, (durationDays - Math.max(0, daysLeft)) / durationDays));
};
