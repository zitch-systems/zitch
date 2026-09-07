/**
 * When the app is allowed to leave the splash screen.
 *
 * The native splash (navy, Z mark) is held deliberately at startup and lifts
 * only when `SplashScreen.hideAsync()` is called. Everything that decides when
 * that happens lives here, because the failure it guards against is the app not
 * opening at all — a held splash looks identical to a frozen phone, and the
 * customer's only recourse is to delete the app.
 *
 * The rule: fonts may delay the first paint, never prevent it.
 */

/** How long the splash may wait for fonts before the app opens regardless. */
export const FONT_WAIT_MS = 4000;

/**
 * Whether the root layout may render and hide the splash.
 *
 * Three ways to be ready, and only the first is the happy path:
 *   * the fonts loaded,
 *   * the fonts FAILED — render in the system face; a missing typeface is a
 *     cosmetic problem and must not cost the customer the whole app,
 *   * neither, past the deadline — a load that never settles (nor rejects) is
 *     not a state anyone should be left sitting in.
 *
 * The one false case is the honest one: fonts still loading, no error, deadline
 * not reached. Every other combination opens the app.
 */
export const splashReady = (
  fontsLoaded: boolean,
  fontError: unknown,
  fontWaitOver: boolean,
): boolean => Boolean(fontsLoaded) || Boolean(fontError) || Boolean(fontWaitOver);
