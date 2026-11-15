import { isCompleteNamePart, isCompleteRegistrationName, normalizeNamePart, splitRegistrationName } from '@/lib/registration';

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

describe('first name and surname fields', () => {
  it('trims each field and collapses inner spaces', () => {
    expect(normalizeNamePart('  Nneka   Okafor ')).toBe('Nneka Okafor');
  });

  it('needs at least two letters in each field', () => {
    expect(isCompleteNamePart('Ada')).toBe(true);
    expect(isCompleteNamePart(' A ')).toBe(false);
    expect(isCompleteNamePart('   ')).toBe(false);
  });
});
