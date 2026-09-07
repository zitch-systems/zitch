import { purchasablePhoneNumber } from '@/lib/phone';

describe('purchasablePhoneNumber', () => {
  it.each([
    ['08166938327', '08166938327'],
    ['+234 816 693 8327', '08166938327'],
    ['234 (0) 816 693 8327', '08166938327'],
    ['8166938327', '08166938327'],
  ])('normalises %s', (input, expected) => {
    expect(purchasablePhoneNumber(input)).toBe(expected);
  });

  it('rejects non-Nigerian or incomplete numbers', () => {
    expect(purchasablePhoneNumber('+1 202 555 0123')).toBe('');
    expect(purchasablePhoneNumber('0816')).toBe('');
  });
});
