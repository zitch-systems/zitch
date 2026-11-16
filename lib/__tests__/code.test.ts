import { extractCode } from '@/lib/code';

describe('extractCode', () => {
  it('keeps typed digits, up to the length', () => {
    expect(extractCode('12')).toBe('12');
    expect(extractCode('1234567')).toBe('123456');
  });

  it('takes the code out of a pasted message, ignoring other numbers', () => {
    expect(extractCode('Zitch: 482193 is your code. It expires in 10 minutes. 08012345678')).toBe('482193');
    expect(extractCode('Your code is 482 193')).toBe('482193');
  });

  it('copes with nothing useful', () => {
    expect(extractCode('')).toBe('');
    expect(extractCode('no digits here')).toBe('');
  });
});
