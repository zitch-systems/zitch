import { isCompleteRegistrationName, splitRegistrationName } from '@/lib/registration';

describe('registration names', () => {
  it('normalizes whitespace and preserves compound surnames', () => {
    expect(splitRegistrationName('  Ada   Nneka  Okafor ')).toEqual({
      firstName: 'Ada',
      lastName: 'Nneka Okafor',
    });
  });

  it('requires both a first and last name', () => {
    expect(isCompleteRegistrationName('Ada Okafor')).toBe(true);
    expect(isCompleteRegistrationName('Ada')).toBe(false);
    expect(isCompleteRegistrationName('A Okafor')).toBe(false);
  });
});
