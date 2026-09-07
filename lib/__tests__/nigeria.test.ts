import { NIGERIAN_STATES, canonicalState } from '@/constants/nigeria';

describe('Nigerian states', () => {
  it('has all 36 states plus the FCT', () => {
    expect(NIGERIAN_STATES).toHaveLength(37);
    expect(NIGERIAN_STATES).toContain('Federal Capital Territory');
  });

  it('is sorted and free of duplicates, so the picker reads predictably', () => {
    expect([...NIGERIAN_STATES]).toEqual([...NIGERIAN_STATES].slice().sort());
    expect(new Set(NIGERIAN_STATES).size).toBe(NIGERIAN_STATES.length);
  });

  describe('canonicalState', () => {
    it('accepts the canonical name in any casing', () => {
      expect(canonicalState('lagos')).toBe('Lagos');
      expect(canonicalState('  AKWA IBOM ')).toBe('Akwa Ibom');
    });

    it('drops a trailing "State", which is how people write it', () => {
      expect(canonicalState('Lagos State')).toBe('Lagos');
      expect(canonicalState('kano state')).toBe('Kano');
    });

    it('treats Abuja as the FCT, because residents do', () => {
      expect(canonicalState('Abuja')).toBe('Federal Capital Territory');
      expect(canonicalState('FCT')).toBe('Federal Capital Territory');
    });

    it('resolves an unambiguous prefix', () => {
      expect(canonicalState('Bayel')).toBe('Bayelsa');
    });

    it('refuses an AMBIGUOUS prefix rather than guessing', () => {
      // Kaduna / Kano / Katsina / Kebbi / Kogi / Kwara all start with "K".
      expect(canonicalState('Ka')).toBe('');
      expect(canonicalState('K')).toBe('');
    });

    it('returns empty for nonsense and for blanks', () => {
      expect(canonicalState('Atlantis')).toBe('');
      expect(canonicalState('')).toBe('');
      expect(canonicalState('   ')).toBe('');
      expect(canonicalState(undefined as unknown as string)).toBe('');
    });

    it('round-trips every state it lists', () => {
      for (const s of NIGERIAN_STATES) expect(canonicalState(s)).toBe(s);
    });
  });
});
