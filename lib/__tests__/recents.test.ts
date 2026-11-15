import { shortDate, groupPhone, networkForPhone } from '@/lib/recents';

jest.mock('@/lib/services/bills', () => ({ recentsService: { get: jest.fn() } }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => 0 }));

describe('bill recents helpers', () => {
  it('dates a recent payment as "12 Nov", with the year only when it is not this year', () => {
    const now = new Date('2026-11-15T12:00:00Z');
    expect(shortDate('2026-11-12T09:14:00+01:00', now)).toBe('12 Nov');
    expect(shortDate('2025-10-28T09:14:00+01:00', now)).toBe('28 Oct 2025');
    expect(shortDate('not a date', now)).toBe('');
  });

  it('groups an 11-digit number for display and leaves anything else alone', () => {
    expect(groupPhone('08012345678')).toBe('0801 234 5678');
    expect(groupPhone('0801234')).toBe('0801234');
  });

  it('suggests the network from the prefix, or nothing when it cannot tell', () => {
    expect(networkForPhone('08031234567')).toBe('1');
    expect(networkForPhone('+2348051234567')).toBe('2');
    expect(networkForPhone('08021234567')).toBe('3');
    expect(networkForPhone('08091234567')).toBe('4');
    expect(networkForPhone('0801')).toBe('');
    expect(networkForPhone('08')).toBe('');
  });
});
