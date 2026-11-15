import { LGAS_BY_STATE, lgasFor, exactLga } from '@/lib/data/lgas';
import { NIGERIAN_STATES } from '@/components/kyc/Onboarding';

jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ui', () => ({ Field: () => null, Btn: () => null }));
jest.mock('@/lib/services/kyc', () => ({ kycService: {} }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: {} }), font: {} }));

it('lists LGAs for every state the address form offers, and for nothing else', () => {
  expect(Object.keys(LGAS_BY_STATE).sort()).toEqual([...NIGERIAN_STATES].sort());
  for (const state of NIGERIAN_STATES) expect(lgasFor(state).length).toBeGreaterThan(0);
});

it("covers Nigeria's 774 local government areas, FCT area councils included, without repeats in a state", () => {
  const all = Object.values(LGAS_BY_STATE);
  expect(all.reduce((n, list) => n + list.length, 0)).toBe(774);
  expect(lgasFor('FCT')).toHaveLength(6);
  for (const list of all) expect(new Set(list).size).toBe(list.length);
});

it('matches a typed LGA in any case, and nothing for an unknown state', () => {
  expect(exactLga('Lagos', ' ikeja ')).toBe('Ikeja');
  expect(exactLga('Lagos', 'Ikeja GRA')).toBe('');
  expect(lgasFor('Atlantis')).toEqual([]);
});
