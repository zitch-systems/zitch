// What each KYC tier allows. The server's tables (accounts/models.py) are the
// source of truth and arrive as `tier_ladder` on /api/kyc/status/; this copy is
// used only until a server without that field is updated.
export type LadderTier = { tier: number; name: string; per_transfer: string; daily_transfer: string; daily_bill: string };

export const LADDER_FALLBACK: LadderTier[] = [
  { tier: 1, name: 'Verified', per_transfer: '50000', daily_transfer: '50000', daily_bill: '20000' },
  { tier: 2, name: 'Enhanced', per_transfer: '200000', daily_transfer: '1000000', daily_bill: '100000' },
  { tier: 3, name: 'Premium', per_transfer: '5000000', daily_transfer: '5000000', daily_bill: '500000' },
];

/** The server's ladder when it sent one, else the fallback; tiers 1–3 only. */
export const tierLadder = (fromServer?: LadderTier[] | null): LadderTier[] =>
  (fromServer?.length ? fromServer : LADDER_FALLBACK).filter((t) => t.tier >= 1 && t.tier <= 3);

/** What each tier needs, as both the account-limits ladder and the Tier 2 page say it. */
export const TIER_NEEDS: Record<number, string> = {
  1: 'BVN, verified when you signed up',
  2: 'Your NIN and a face check',
  3: 'The address you gave at sign-up',
};
