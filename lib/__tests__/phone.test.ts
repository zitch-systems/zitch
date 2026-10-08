import { localPhoneNumber, purchasablePhoneNumber, paymentDestination } from '@/lib/phone';

describe('localPhoneNumber', () => {
  it.each([
    ['+234 906 283 1750', '09062831750'],
    ['2349062831750', '09062831750'],
    ['9062831750', '09062831750'],
    ['09062831750', '09062831750'],
  ])('normalises %s for mobile entry', (raw, expected) => {
    expect(localPhoneNumber(raw)).toBe(expected);
  });

  it('does not invent digits for an empty value', () => {
    expect(localPhoneNumber('')).toBe('');
  });
});


describe('payment phone and QR destinations', () => {
  it.each(['08012345678', '+2348012345678', '234(0)8012345678', '8012345678'])(
    'normalises a payable mobile number %s without truncating it', (value) => {
      expect(purchasablePhoneNumber(value)).toBe('08012345678');
    },
  );
  it.each(['0801234567', '00012345678', '+441234567890', '', '080123456789'])(
    'does not invent a Nigerian payment phone from %s', (value) => expect(purchasablePhoneNumber(value)).toBe(''),
  );
  it('keeps scanned phones distinct from 10-digit bank accounts', () => {
    expect(paymentDestination('08012345678')).toEqual({ identifier: '08012345678', mode: 'zitch' });
    expect(paymentDestination('0123456789')).toEqual({ identifier: '0123456789', mode: 'bank' });
    expect(paymentDestination('https://zitch.ng/pay?phone=08012345678')).toEqual({ identifier: '08012345678', mode: 'zitch' });
  });
  it.each(['https://zitch.ng/pay?account=012345678999', 'invoice number 0123456789',
    'https://zitch.ng/pay?account=0123456789&phone=08012345678', 'javascript:0123456789'])(
    'rejects ambiguous or malformed QR material %s', (value) => expect(paymentDestination(value)).toBeNull(),
  );
});
