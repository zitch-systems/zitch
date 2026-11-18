import React from 'react';
import renderer, { act } from 'react-test-renderer';
import BioPayOffer from '@/components/design/BioPayOffer';

const mockOffer = jest.fn();
const mockEnable = jest.fn();
jest.mock('@/lib/payApproval', () => ({ takeBiometricPayOffer: (...a: unknown[]) => mockOffer(...a) }));
jest.mock('@/lib/bioPayEnable', () => ({ enableBiometricPay: (...a: unknown[]) => mockEnable(...a) }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: { brand: '#0FA295', ink1: '#111', ink3: '#333' } }), font: { regular: 'r', extrabold: 'x' } }));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { Text, View, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Sheet: ({ open, title, children }: { open: boolean; title: string; children: React.ReactNode }) =>
      open ? R.createElement(View, { testID: 'sheet' }, R.createElement(Text, null, title), children) : null,
    PinSheet: ({ open, title, subtitle, error, onComplete }: { open: boolean; title: string; subtitle: string; error?: string; onComplete: (p: string) => void }) =>
      open ? R.createElement(View, { testID: 'pin' }, R.createElement(Text, null, title), R.createElement(Text, null, subtitle),
        error ? R.createElement(Text, null, error) : null,
        R.createElement(Pressable, { accessibilityLabel: 'type pin', onPress: () => onComplete('246810') })) : null,
    Btn: ({ label, onPress }: { label: string; onPress: () => void }) => R.createElement(Pressable, { accessibilityLabel: label, onPress }, R.createElement(Text, null, label)),
  };
});

const notify = () => require('@/components/design/Notify').notify as jest.Mock;
const text = (t: renderer.ReactTestRenderer) => JSON.stringify(t.toJSON());

beforeEach(() => {
  jest.useFakeTimers();
  mockOffer.mockReset();
  mockEnable.mockReset();
  notify().mockClear();
});
afterEach(() => jest.useRealTimers());

const mount = async (show = true) => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<BioPayOffer show={show} />); });
  await act(async () => { jest.advanceTimersByTime(700); await Promise.resolve(); });
  return tree;
};

it('offers the fingerprint a beat after a PIN-approved receipt, and turns it on with the PIN once', async () => {
  mockOffer.mockResolvedValue('fingerprint');
  mockEnable.mockResolvedValue({ ok: true });
  const tree = await mount();
  expect(mockOffer).toHaveBeenCalledTimes(1);
  expect(text(tree)).toContain('Approve payments with your fingerprint');
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Turn on fingerprint' }).props.onPress(); });
  expect(tree.root.findAllByProps({ testID: 'sheet' })).toHaveLength(0);
  await act(async () => { jest.advanceTimersByTime(320); });
  expect(text(tree)).toContain('Enter your 6-digit PIN once to approve payments with your fingerprint.');
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'type pin' }).props.onPress(); });
  expect(mockEnable).toHaveBeenCalledWith('246810');
  expect(tree.root.findAllByProps({ testID: 'pin' })).toHaveLength(0);
  expect(notify()).toHaveBeenCalledWith('All set', 'Fingerprint payments are on.');
  act(() => tree.unmount());
});

it('keeps the PIN sheet open with the reason when the PIN is wrong', async () => {
  mockOffer.mockResolvedValue('fingerprint');
  mockEnable.mockResolvedValue({ ok: false, message: 'Incorrect PIN. 2 attempts left.' });
  const tree = await mount();
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Turn on fingerprint' }).props.onPress(); jest.advanceTimersByTime(320); });
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'type pin' }).props.onPress(); });
  expect(text(tree)).toContain('Incorrect PIN. 2 attempts left.');
  expect(notify()).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('closes on Not now without turning anything on', async () => {
  mockOffer.mockResolvedValue('face');
  const tree = await mount();
  expect(text(tree)).toContain('Approve payments with Face ID');
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Not now' }).props.onPress(); jest.advanceTimersByTime(1000); });
  expect(tree.root.findAllByProps({ testID: 'sheet' })).toHaveLength(0);
  expect(tree.root.findAllByProps({ testID: 'pin' })).toHaveLength(0);
  expect(mockEnable).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('shows nothing when no offer is due', async () => {
  mockOffer.mockResolvedValue(null);
  const tree = await mount();
  expect(tree.toJSON()).toBeNull();
  act(() => tree.unmount());
});

it('does not ask on a receipt that is not a success', async () => {
  mockOffer.mockResolvedValue('fingerprint');
  const tree = await mount(false);
  expect(mockOffer).not.toHaveBeenCalled();
  expect(tree.toJSON()).toBeNull();
  act(() => tree.unmount());
});
