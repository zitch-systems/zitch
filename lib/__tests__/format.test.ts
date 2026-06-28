import { money, moneyk } from '@/lib/format';

describe('money', () => {
  // Design `fmtN`: whole naira, with kobo shown only when actually present.
  it('formats whole naira without trailing .00', () => {
    expect(money(5000)).toBe('₦5,000');
    expect(money(150000)).toBe('₦150,000');
  });

  it('shows kobo only when the amount is fractional', () => {
    expect(money(1234.5)).toBe('₦1,234.5');
    expect(money(1234.56)).toBe('₦1,234.56');
  });

  it('treats null/undefined/0 as ₦0', () => {
    expect(money(0)).toBe('₦0');
    expect(money(null)).toBe('₦0');
    expect(money(undefined)).toBe('₦0');
  });
});

describe('moneyk', () => {
  it('formats without forced decimals', () => {
    expect(moneyk(5000)).toBe('₦5,000');
    expect(moneyk(null)).toBe('₦0');
  });
});
