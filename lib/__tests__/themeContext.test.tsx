/**
 * The theme context must keep a stable identity across re-renders.
 *
 * ThemeProvider wraps the whole app and practically every component reads it
 * (Screen, ServiceTile, Hero, Badge, TxnRow, every screen body). It used to
 * build its value inline — `value={{ theme, c, setTheme, toggle }}` — so the
 * object was new on every render and React re-rendered every consumer in the
 * mounted tree. RootLayout calls usePathname() and sits directly above the
 * provider, so that fired on every navigation: each tap that opened a screen
 * re-rendered the entire app underneath it. This pins the fix.
 */
import * as React from 'react';
import renderer from 'react-test-renderer';

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
}));
import { ThemeProvider, useTheme } from '../theme';

/** Records the context value handed to it on each render. */
const makeProbe = (seen: any[]) => {
  const Probe = () => {
    seen.push(useTheme());
    return null;
  };
  return Probe;
};

const renderProvider = async (Probe: React.ComponentType) => {
  let tree: renderer.ReactTestRenderer;
  await renderer.act(async () => {
    tree = renderer.create(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
  });
  return tree!;
};

it('hands consumers the same context value when the parent re-renders', async () => {
  const seen: any[] = [];
  const Probe = makeProbe(seen);
  const tree = await renderProvider(Probe);

  // Re-render the provider without changing the theme, the way a navigation
  // re-renders RootLayout above it.
  await renderer.act(async () => {
    tree.update(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
  });

  expect(seen.length).toBeGreaterThan(1);
  const [first, ...rest] = seen;
  for (const value of rest) {
    expect(value).toBe(first);
  }

  await renderer.act(async () => { tree.unmount(); });
});

it('keeps setTheme stable so the memoized value is not defeated by it', async () => {
  const seen: any[] = [];
  const Probe = makeProbe(seen);
  const tree = await renderProvider(Probe);

  await renderer.act(async () => {
    tree.update(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
  });

  expect(seen[seen.length - 1].setTheme).toBe(seen[0].setTheme);

  await renderer.act(async () => { tree.unmount(); });
});

it('does publish a new value when the theme actually changes', async () => {
  const seen: any[] = [];
  const Probe = makeProbe(seen);
  const tree = await renderProvider(Probe);

  const before = seen[seen.length - 1];
  await renderer.act(async () => { before.toggle(); });
  const after = seen[seen.length - 1];

  expect(after).not.toBe(before);
  expect(after.theme).not.toBe(before.theme);
  expect(after.c).not.toBe(before.c);

  await renderer.act(async () => { tree.unmount(); });
});
