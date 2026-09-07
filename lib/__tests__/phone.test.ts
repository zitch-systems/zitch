import { localPhoneNumber } from '@/lib/phone';

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
