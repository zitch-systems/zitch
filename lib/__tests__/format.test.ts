import { groupAccountNumber, money, moneyk, providerInitials } from '@/lib/format';

describe('money', () => {
  // Design `fmtN`: whole naira, with kobo shown only when actually present.
  it('formats whole naira without trailing .00', () => {
    expect(money(5000)).toBe('₦5,000');
    expect(money(150000)).toBe('₦150,000');
  });

  it('shows kobo only when the amount is fractional', () => {
    expect(money(1234.5)).toBe('₦1,234.50');
    expect(money(120.5)).toBe('₦120.50');
    expect(money(15000)).toBe('₦15,000');
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

describe('groupAccountNumber', () => {
  it('groups a 10-digit account number 3-3-4, the same on every screen', () => {
    expect(groupAccountNumber('0123456789')).toBe('012 345 6789');
    expect(groupAccountNumber('0123 456 789')).toBe('012 345 6789');
  });

  it('leaves anything that is not a 10-digit number as given', () => {
    expect(groupAccountNumber('12345')).toBe('12345');
    expect(groupAccountNumber('')).toBe('');
  });
});

describe('providerInitials', () => {
  it('uses the short code when the name carries one, never a doubled letter', () => {
    expect(providerInitials('Ikeja (IKEDC)')).toBe('IK');
    expect(providerInitials('Eko (EKEDC)')).toBe('EK');
    expect(providerInitials('P/H (PHED)')).toBe('PH');
    expect(providerInitials('Ibadan (IBEDC)')).toBe('IB');
  });

  it('falls back to the first letters of the name', () => {
    expect(providerInitials('Kaduna')).toBe('KA');
    expect(providerInitials('Ikeja Electric')).toBe('IE');
  });
});
