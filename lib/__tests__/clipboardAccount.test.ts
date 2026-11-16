import { copiedAccount, groupPhone } from '@/lib/clipboardAccount';

describe('copiedAccount', () => {
  it('offers a copied 10-digit account number as it is', () => {
    expect(copiedAccount('8081103848')).toEqual({ account: '8081103848' });
    expect(copiedAccount(' 012 345 6789 ')).toEqual({ account: '0123456789' });
    expect(copiedAccount('0123-456-789')).toEqual({ account: '0123456789' });
  });

  it('turns a copied phone number into its account number by dropping the leading 0', () => {
    expect(copiedAccount('08081103848')).toEqual({ account: '8081103848', phone: '08081103848' });
    expect(copiedAccount('0808 110 3848')).toEqual({ account: '8081103848', phone: '08081103848' });
    expect(copiedAccount('+234 808 110 3848')).toEqual({ account: '8081103848', phone: '08081103848' });
    expect(copiedAccount('2348081103848')).toEqual({ account: '8081103848', phone: '08081103848' });
  });

  it('ignores anything that is not a number on its own', () => {
    expect(copiedAccount('Send 5000 to 8081103848 please')).toBeNull();
    expect(copiedAccount('22123456789')).toBeNull(); // 11 digits, not a mobile number (a BVN)
    expect(copiedAccount('12345')).toBeNull();
    expect(copiedAccount('+8081103848')).toBeNull();
    expect(copiedAccount('')).toBeNull();
    expect(copiedAccount(undefined)).toBeNull();
    expect(copiedAccount('0808 110 3848 0808 110 3848')).toBeNull();
  });
});

it('groups a local mobile number for reading', () => {
  expect(groupPhone('08081103848')).toBe('0808 110 3848');
  expect(groupPhone('123')).toBe('123');
});
