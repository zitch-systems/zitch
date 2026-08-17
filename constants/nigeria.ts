/**
 * Nigeria's states, for the places a customer has to tell us where they live.
 *
 * A free-text state field looked harmless and was not: "Lagos", "lagos", "Lagos
 * State", "LAG" and "Lasgidi" all arrived as different values for one place, and
 * every consumer downstream — address verification, the compliance export, any
 * grouping by region — had to guess which of them meant the same thing. A closed
 * list is the only way that question has one answer.
 *
 * All 36 states plus the Federal Capital Territory, which is not a state and is
 * still where a great many customers live, so leaving it out would have made the
 * list unusable for Abuja addresses.
 *
 * Stored and displayed as the same string. There is no separate code: the names
 * are already unique and stable, and a numeric id would only add a mapping for
 * every reader to get wrong.
 */
export const NIGERIAN_STATES: readonly string[] = [
  'Abia',
  'Adamawa',
  'Akwa Ibom',
  'Anambra',
  'Bauchi',
  'Bayelsa',
  'Benue',
  'Borno',
  'Cross River',
  'Delta',
  'Ebonyi',
  'Edo',
  'Ekiti',
  'Enugu',
  'Federal Capital Territory',
  'Gombe',
  'Imo',
  'Jigawa',
  'Kaduna',
  'Kano',
  'Katsina',
  'Kebbi',
  'Kogi',
  'Kwara',
  'Lagos',
  'Nasarawa',
  'Niger',
  'Ogun',
  'Ondo',
  'Osun',
  'Oyo',
  'Plateau',
  'Rivers',
  'Sokoto',
  'Taraba',
  'Yobe',
  'Zamfara',
] as const;

/**
 * What people actually type, mapped to the canonical name. Only for repairing a
 * value that already exists — a legacy record, or a KYC profile pre-filled from
 * the bank — never for guessing at something the customer is still choosing.
 *
 * "Abuja" earns its place: it is what residents call the FCT, and a customer who
 * types it should not be told their own city is not a state.
 */
const ALIASES: Record<string, string> = {
  abuja: 'Federal Capital Territory',
  fct: 'Federal Capital Territory',
  'fct abuja': 'Federal Capital Territory',
  'abuja fct': 'Federal Capital Territory',
  'cross rivers': 'Cross River',
  'akwa-ibom': 'Akwa Ibom',
  nassarawa: 'Nasarawa',
  nassawara: 'Nasarawa',
};

/**
 * Resolve loose input to a canonical state name, or '' when nothing matches.
 *
 * Deliberately conservative about prefixes: it accepts one only when exactly a
 * single state starts with it, so "Ka" stays unresolved (Kaduna / Kano / Katsina)
 * rather than silently picking the alphabetically-first. Guessing a customer's
 * state wrong on a compliance record is worse than leaving it blank.
 */
export function canonicalState(input: string): string {
  const raw = String(input ?? '').trim();
  if (!raw) return '';
  const key = raw.toLowerCase().replace(/\s+/g, ' ').replace(/\s*state$/, '').trim();
  const exact = NIGERIAN_STATES.find((s) => s.toLowerCase() === key);
  if (exact) return exact;
  if (ALIASES[key]) return ALIASES[key];
  const starts = NIGERIAN_STATES.filter((s) => s.toLowerCase().startsWith(key));
  return starts.length === 1 ? starts[0] : '';
}
