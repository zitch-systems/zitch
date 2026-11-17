import React, { type ReactNode } from 'react';
import { TextInput } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import Tier3, { addressParts } from '@/app/(auth)/tier3';
import { EP } from '@/lib/endpoints';

const mockApiJson = jest.fn();
const mockNotify = jest.fn();
const mockRouter = { back: jest.fn(), push: jest.fn(), replace: jest.fn(), canGoBack: jest.fn(() => true) };
const mockParams: { from?: string } = {};
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => 0, getToken: async () => 'token' }));
jest.mock('@/components/AuthGuard', () => ({ children }: { children: ReactNode }) => children);
jest.mock('expo-router', () => ({
  get router() { return mockRouter; },
  useLocalSearchParams: () => mockParams,
  useFocusEffect: (callback: () => (() => void) | void) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    ReactActual.useEffect(callback, [callback]);
  },
}));
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#066E66', ink1: '#111', ink2: '#222', ink3: '#333', line: '#ddd', surface: '#fff', red: '#c00' }, theme: 'light' }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold', medium: 'medium' },
}));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text, TextInput: Input, View } = jest.requireActual<typeof import('react-native')>('react-native');
  const Box = ({ children }: { children: ReactNode }) => R.createElement(View, null, children);
  return {
    Screen: Box, Card: Box, NText: Text, money: (value: number) => `₦${value}`,
    Header: ({ title, sub }: { title: string; sub?: string }) => R.createElement(Text, null, `${title} ${sub || ''}`),
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) =>
      R.createElement(Pressable, { accessibilityLabel: label, onPress, disabled }, R.createElement(Text, null, label)),
    Field: ({ value, onChangeText, label }: { value: string; onChangeText: (v: string) => void; label?: string }) =>
      R.createElement(Input, { value, onChangeText, accessibilityLabel: label }),
  };
});

const ladder = [
  { tier: 1, name: 'Verified', per_transfer: '50000', daily_transfer: '50000', daily_bill: '20000' },
  { tier: 2, name: 'Enhanced', per_transfer: '200000', daily_transfer: '1000000', daily_bill: '100000' },
  { tier: 3, name: 'Premium', per_transfer: '5000000', daily_transfer: '5000000', daily_bill: '500000' },
];
const tier2 = {
  success: true, tier: 2, bvn_verified: true, nin_verified: true, face_verified: true, tier_ladder: ladder,
  address: '12 Allen Avenue, Ikeja, Lagos', tier3_state: 'not_confirmed',
};

const render = async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Tier3 />); });
  return tree;
};
const text = (tree: renderer.ReactTestRenderer) => JSON.stringify(tree.toJSON());
const field = (tree: renderer.ReactTestRenderer, label: string) => tree.root.findByProps({ accessibilityLabel: label });
const submit = (tree: renderer.ReactTestRenderer) => field(tree, 'Submit for Tier 3');

beforeEach(() => {
  mockApiJson.mockReset();
  mockNotify.mockReset();
  Object.values(mockRouter).forEach((fn) => fn.mockClear());
  delete mockParams.from;
});

describe('addressParts', () => {
  it('splits the saved sign-up address back into its parts', () => {
    expect(addressParts('12 Allen Avenue, Ikeja, Lagos')).toEqual({ house: '12', street: 'Allen Avenue', state: 'Lagos', lga: 'Ikeja' });
    expect(addressParts('Plot 4B Adeola Odeku Street, Victoria Island, Eti-Osa, lagos'))
      .toEqual({ house: 'Plot 4B', street: 'Adeola Odeku Street, Victoria Island', state: 'Lagos', lga: 'Eti-Osa' });
    // no house number in it: the street, LGA and state still come back
    expect(addressParts('Allen Avenue, Ikeja, Lagos')).toEqual({ house: '', street: 'Allen Avenue', state: 'Lagos', lga: 'Ikeja' });
  });

  it('starts empty for anything that is not in that shape', () => {
    for (const saved of ['', 'Lagos', 'Ikeja, Lagos', '12 Allen Avenue, Ikeja, Atlantis']) {
      expect(addressParts(saved)).toEqual({ house: '', street: '', state: '', lga: '' });
    }
  });
});

it('shows a refused address prefilled as needing review, and sends it again as its own Tier 3 request', async () => {
  mockParams.from = 'limits';
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return { ...tier2, tier3_review: 'rejected' };
    if (path === EP.kyc.tier3Send) return { success: true, sent: true, tier3_state: 'processing' };
    throw new Error(`Unexpected API call: ${path}`);
  });
  const tree = await render();
  const out = text(tree);
  expect(out).toContain('Your details need review. Your bank couldn’t verify this address.');
  expect(out).not.toMatch(/Prembly|Wema/);
  expect(field(tree, 'House number').props.value).toBe('12');
  expect(field(tree, 'Street address').props.value).toBe('Allen Avenue');
  act(() => { field(tree, 'Street address').props.onChangeText('Allen Avenue, Opebi'); });
  await act(async () => { await submit(tree).props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.tier3Send, { house_number: '12', street: 'Allen Avenue, Opebi', state: 'Lagos', lga: 'Ikeja' });
  // Tier 3 is never raised from this answer: the customer's tap applies it once the bank verified it
  expect(mockNotify).toHaveBeenCalledWith('Sent for Tier 3', 'Your bank is checking your address. Tap Verify Tier 3 on Account limits to check on it.', 'success');
  expect(mockRouter.back).toHaveBeenCalled();
  act(() => tree.unmount());
});

it('keeps the customer on the page when the bank refuses the address again', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? tier2
    : path === EP.kyc.tier3Send ? { success: true, sent: true, tier3_state: 'not_confirmed' } : { success: false });
  const tree = await render();
  await act(async () => { await submit(tree).props.onPress(); });
  expect(mockNotify).toHaveBeenCalledWith('Not confirmed', 'Your bank could not confirm this address. Check it and send it again.', 'info');
  expect(mockRouter.back).not.toHaveBeenCalled();
  expect(mockRouter.replace).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('shows Processing, Tier 2 first, or done instead of the form', async () => {
  for (const [state, copy] of [
    [{ tier3_state: 'processing' }, 'Still checking your address'],
    [{ tier: 1, face_verified: false, tier3_state: 'not_sent' }, 'Verify Tier 2 first'],
    [{ tier: 3, tier3_state: 'done' }, 'You’re on Tier 3'],
  ] as const) {
    mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? { ...tier2, ...state } : { success: false });
    const tree = await render();
    expect(text(tree)).toContain(copy);
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Submit for Tier 3' })).toHaveLength(0);
    act(() => tree.unmount());
  }
});
