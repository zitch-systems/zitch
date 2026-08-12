import { FONT_WAIT_MS, splashReady } from '@/lib/boot';

describe('splashReady', () => {
  it('opens the app once the fonts have loaded', () => {
    expect(splashReady(true, null, false)).toBe(true);
  });

  it('opens the app when the fonts FAIL rather than holding the splash', () => {
    // The regression this exists for: the root layout used to `throw` the font
    // error inside an effect, before the splash was ever hidden. The app never
    // opened — a navy screen with no way past it — over a typeface.
    expect(splashReady(false, new Error('font asset missing'), false)).toBe(true);
  });

  it('opens the app when a font load never settles', () => {
    // No `loaded`, no `error`, forever. Without the deadline this is the state
    // the customer sits in until they delete the app.
    expect(splashReady(false, null, true)).toBe(true);
  });

  it('waits only while the fonts are genuinely still loading', () => {
    expect(splashReady(false, null, false)).toBe(false);
  });

  it('has a deadline short enough to be a startup, not a hang', () => {
    expect(FONT_WAIT_MS).toBeGreaterThan(0);
    expect(FONT_WAIT_MS).toBeLessThanOrEqual(5000);
  });
});
