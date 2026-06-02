import { money, moneyk } from '@/lib/format';

describe('money', () => {
  it('formats with the naira sign and two decimals', () => {
    expect(money(5000)).toBe('₦5,000.00');
    expect(money(1234.5)).toBe('₦1,234.50');
  });

  it('treats null/undefined/0 as ₦0.00', () => {
    expect(money(0)).toBe('₦0.00');
    expect(money(null)).toBe('₦0.00');
    expect(money(undefined)).toBe('₦0.00');
  });
});

describe('moneyk', () => {
  it('formats without forced decimals', () => {
    expect(moneyk(5000)).toBe('₦5,000');
    expect(moneyk(null)).toBe('₦0');
  });
});
