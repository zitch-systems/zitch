import { money, moneyk, isTrivialPin, sanitizeAmount, formatAmountInput } from '@/lib/format';

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

describe('sanitizeAmount', () => {
  it('keeps digits and a single decimal point', () => {
    expect(sanitizeAmount('1234')).toBe('1234');
    expect(sanitizeAmount('1234.56')).toBe('1234.56');
    expect(sanitizeAmount('₦1,234.56')).toBe('1234.56');
  });

  it('keeps a trailing point so kobo can still be typed', () => {
    expect(sanitizeAmount('12.')).toBe('12.');
    expect(sanitizeAmount('12.5')).toBe('12.5');
  });

  it('caps kobo at two places and folds extra points in', () => {
    expect(sanitizeAmount('12.567')).toBe('12.56');
    expect(sanitizeAmount('1.2.3')).toBe('1.23');
  });

  it('drops leading zeros but keeps a leading zero before kobo', () => {
    expect(sanitizeAmount('007')).toBe('7');
    expect(sanitizeAmount('.5')).toBe('0.5');
    expect(sanitizeAmount('0.75')).toBe('0.75');
  });

  it('stays a number the API and Number() can read', () => {
    expect(Number(sanitizeAmount('12,500.75'))).toBe(12500.75);
    expect(sanitizeAmount('')).toBe('');
  });
});

describe('formatAmountInput', () => {
  it('groups thousands and leaves kobo as typed', () => {
    expect(formatAmountInput('1234')).toBe('1,234');
    expect(formatAmountInput('1234567')).toBe('1,234,567');
    expect(formatAmountInput('12500.75')).toBe('12,500.75');
    expect(formatAmountInput('12500.7')).toBe('12,500.7');
    expect(formatAmountInput('12500.')).toBe('12,500.');
  });

  it('is stable when re-fed its own output', () => {
    expect(formatAmountInput(formatAmountInput('12500.75'))).toBe('12,500.75');
  });

  it('renders an empty field as empty', () => {
    expect(formatAmountInput('')).toBe('');
    expect(formatAmountInput('abc')).toBe('');
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
