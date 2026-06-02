import { daysUntil, ratePct, lockProgress } from '@/lib/savings';

describe('daysUntil', () => {
  const now = Date.parse('2026-06-02T12:00:00Z');

  it('counts whole days to a future date', () => {
    expect(daysUntil('2026-06-12', now)).toBe(10);
  });

  it('is non-positive once the date has passed', () => {
    expect(daysUntil('2026-06-01', now)).toBeLessThanOrEqual(0);
  });

  it('returns 0 for an unparseable date', () => {
    expect(daysUntil('not-a-date', now)).toBe(0);
  });
});

describe('ratePct', () => {
  it('renders a decimal rate string as a percentage', () => {
    expect(ratePct('0.1500')).toBe('15% p.a');
    expect(ratePct('0.22')).toBe('22% p.a');
  });
});

describe('lockProgress', () => {
  it('is 1 when matured', () => {
    expect(lockProgress(true, 90, 90)).toBe(1);
  });

  it('reflects elapsed fraction while active', () => {
    expect(lockProgress(false, 90, 90)).toBe(0); // just locked
    expect(lockProgress(false, 90, 45)).toBeCloseTo(0.5); // halfway
    expect(lockProgress(false, 90, 0)).toBe(1); // due
  });

  it('clamps to [0,1] and guards a zero duration', () => {
    expect(lockProgress(false, 90, 999)).toBe(0);
    expect(lockProgress(false, 0, 0)).toBe(0);
  });
});
