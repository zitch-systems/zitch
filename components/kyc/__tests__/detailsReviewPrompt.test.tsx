import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import { router } from 'expo-router';
import { DetailsReviewPrompt } from '@/components/kyc/DetailsReviewPrompt';

// "Your details need review" on its own (17 Nov 2026): only a refusal shows, once.
const mockApiJson = jest.fn();
const mockStore: Record<string, string> = {};
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: async (key: string) => mockStore[key] ?? null,
  setItem: async (key: string, value: string) => { mockStore[key] = value; },
}));
jest.mock('expo-router', () => ({
  router: { push: jest.fn() },
  useFocusEffect: (callback: () => void) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    ReactActual.useEffect(callback, [callback]);
  },
}));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#0FA295', ink1: '#111', ink2: '#222', ink3: '#333', line: '#ddd', surface: '#fff', surface2: '#eee' }, theme: 'light' }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text, View, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Sheet: ({ open, children }: { open: boolean; children: ReactNode }) => (open ? ReactActual.createElement(View, null, children) : null),
    NText: Text, money: (value: number) => `₦${value}`,
    Tap: ({ children, onPress, accessibilityLabel }: { children: ReactNode; onPress: () => void; accessibilityLabel?: string }) =>
      ReactActual.createElement(Pressable, { onPress, accessibilityLabel }, children),
    Btn: ({ label, onPress }: { label: string; onPress: () => void }) =>
      ReactActual.createElement(Pressable, { accessibilityLabel: label, onPress }, ReactActual.createElement(Text, null, label)),
  };
});

const status = (details_review: unknown[]) => ({ success: true, tier: 1, address: '12 Allen Avenue, Ikeja, Lagos', details_review });
const render = async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<DetailsReviewPrompt />); });
  return tree;
};

beforeEach(() => {
  mockApiJson.mockReset();
  (router.push as jest.Mock).mockClear();
  Object.keys(mockStore).forEach((k) => delete mockStore[k]);
});

it('shows nothing when the bank refused nothing', async () => {
  mockApiJson.mockResolvedValue(status([]));
  const tree = await render();
  expect(tree.toJSON()).toBeNull();
  act(() => tree.unmount());
});

it('pops up on its own when the bank refused the Tier 2 details, and opens the Tier 2 page', async () => {
  mockApiJson.mockResolvedValue(status([{ tier: 2, at: '2026-11-17T12:00:00Z' }]));
  const tree = await render();
  const out = JSON.stringify(tree.toJSON());
  expect(out).toContain('Your details need review');
  expect(out).toContain('NIN and face check');
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Review details' }).props.onPress(); });
  expect(router.push).toHaveBeenCalledWith('/tier2?from=home&recheck=1');
  act(() => tree.unmount());
});

it('shows each refusal once: not again after Not now, but again for a new refusal', async () => {
  mockApiJson.mockResolvedValue(status([{ tier: 3, at: '2026-11-17T12:00:00Z' }]));
  let tree = await render();
  expect(JSON.stringify(tree.toJSON())).toContain('Home address');
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Not now' }).props.onPress(); });
  expect(tree.toJSON()).toBeNull();
  act(() => tree.unmount());
  tree = await render();
  expect(tree.toJSON()).toBeNull();
  act(() => tree.unmount());
  mockApiJson.mockResolvedValue(status([{ tier: 3, at: '2026-11-18T09:00:00Z' }]));
  tree = await render();
  expect(JSON.stringify(tree.toJSON())).toContain('Your details need review');
  act(() => tree.unmount());
});
