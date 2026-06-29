import { money, moneyk, isTrivialPin } from '@/lib/format';

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

describe('isTrivialPin', () => {
  it('rejects all-same-digit PINs', () => {
    expect(isTrivialPin('0000')).toBe(true);
    expect(isTrivialPin('1111')).toBe(true);
    expect(isTrivialPin('9999')).toBe(true);
  });

  it('rejects straight ascending/descending runs', () => {
    expect(isTrivialPin('1234')).toBe(true);
    expect(isTrivialPin('0123')).toBe(true);
    expect(isTrivialPin('4321')).toBe(true);
    expect(isTrivialPin('3210')).toBe(true);
  });

  it('accepts non-trivial PINs', () => {
    expect(isTrivialPin('1357')).toBe(false);
    expect(isTrivialPin('2580')).toBe(false);
    expect(isTrivialPin('1984')).toBe(false);
    expect(isTrivialPin('1123')).toBe(false);
  });

  it('does not flag incomplete input', () => {
    expect(isTrivialPin('')).toBe(false);
    expect(isTrivialPin('12')).toBe(false);
  });
});
