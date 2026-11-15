import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import Kyc from '@/app/(auth)/kyc';
import { EP } from '@/lib/endpoints';

// Sign-up's "BVN and address" page: the identity screen in onboarding mode.
const mockApiJson = jest.fn();
const mockReplace = jest.fn();
const mockOpenBrowser = jest.fn();
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('@/lib/secureStore', () => ({ getToken: async () => 'test-token', getSessionGeneration: () => 0 }));
jest.mock('expo-router', () => ({
  router: { back: jest.fn(), push: jest.fn(), replace: (...args: unknown[]) => mockReplace(...args) },
  useLocalSearchParams: () => ({ onboarding: '1' }),
  Redirect: () => null,
  useFocusEffect: (callback: () => (() => void) | void) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    ReactActual.useEffect(() => callback(), [callback]);
  },
}));
jest.mock('expo-image-picker', () => ({}));
jest.mock('expo-web-browser', () => ({ openBrowserAsync: (...args: unknown[]) => mockOpenBrowser(...args) }));
jest.mock('@/lib/session', () => ({
  beginExternalActivity: jest.fn(), endExternalActivity: jest.fn(),
  isSessionLocked: async () => false, isExternalActivityActive: () => false,
  enforceHardExpiry: jest.fn(), enforceIdleTimeout: jest.fn(), lockIfAwayTooLong: jest.fn(),
}));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/FaceLivenessModal', () => () => null);
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#066E66', ink1: '#111', ink2: '#222', ink3: '#333', line: '#ddd', surface: '#fff' } }),
  font: { bold: 'b', extrabold: 'x', regular: 'r', semibold: 's', medium: 'm' },
}));
jest.mock('@/components/design/ui', () => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text, TextInput, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => R.createElement(View, null, children),
    Header: () => null, money: String,
    Tap: ({ children, ...props }: { children: ReactNode }) => R.createElement(Pressable, props, children),
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) =>
      R.createElement(Pressable, { accessibilityLabel: label, onPress, disabled }, R.createElement(Text, null, label)),
    Field: ({ label, value, onChangeText, onFocus }: { label: string; value: string; onChangeText: (v: string) => void; onFocus?: () => void }) =>
      R.createElement(TextInput, { accessibilityLabel: label, value, onChangeText, onFocus }),
  };
});

const base = { success: true, tier: 0, transaction_limit: '0', bvn_verified: false, nin_verified: false, face_verified: false,
  email_verified: false, account_provider: 'partnership', identity_face_available: true };

const byLabel = (tree: renderer.ReactTestRenderer, label: string) =>
  tree.root.findAll((n) => n.props.accessibilityLabel === label && (n.props.onPress || n.props.onChangeText))[0];

beforeEach(() => {
  mockApiJson.mockReset();
  mockReplace.mockReset();
  mockOpenBrowser.mockReset();
});

it('starts on the BVN form, even before the email is verified, and sends the SMS code', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? base
    : { success: true, otp_required: true, identity_verification_provider: 'prembly', delivery: 'phone •••8888' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  expect(JSON.stringify(tree.toJSON())).toContain('BVN and address');
  expect(JSON.stringify(tree.toJSON())).not.toContain('Verify email');
  expect(byLabel(tree, 'Send code').props.disabled).toBe(true);
  await act(async () => { byLabel(tree, 'Bank Verification Number (BVN)').props.onChangeText('12345678901'); });
  await act(async () => { await byLabel(tree, 'Send code').props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.bvnStart, { bvn: '12345678901' });
  expect(JSON.stringify(tree.toJSON())).toContain('phone •••8888');
  await act(async () => tree.unmount());
});

it('uses the face check when the customer picks it', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? base
    : { success: false, message: 'Face check unavailable' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { byLabel(tree, 'Bank Verification Number (BVN)').props.onChangeText('12345678901'); });
  await act(async () => { byLabel(tree, 'Face check, A quick selfie to match your BVN').props.onPress(); });
  await act(async () => { await byLabel(tree, 'Start face check').props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.identityFaceStart, { bvn: '12345678901', prefer_face: true });
  expect(mockApiJson.mock.calls.map(([p]) => p)).not.toContain(EP.kyc.bvnStart);
  await act(async () => tree.unmount());
});

it('can be left for later, straight to the PIN set-up', async () => {
  mockApiJson.mockResolvedValue(base);
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { byLabel(tree, "I'll do this later").props.onPress(); });
  expect(mockReplace).toHaveBeenCalledWith('/setup');
  await act(async () => tree.unmount());
});

it('asks for the address once the BVN is verified, saves it and moves on', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? { ...base, bvn_verified: true, tier: 1 }
    : { success: true, address: '12 Allen Avenue, Ikeja, Lagos' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  expect(JSON.stringify(tree.toJSON())).toContain('Your BVN is verified. Now add your address.');
  const cont = () => byLabel(tree, 'Continue');
  expect(cont().props.disabled).toBe(true);
  await act(async () => { byLabel(tree, 'House number').props.onChangeText('12'); });
  await act(async () => { byLabel(tree, 'Street address').props.onChangeText('Allen Avenue'); });
  await act(async () => { byLabel(tree, 'State').props.onChangeText('lag'); });
  await act(async () => { byLabel(tree, 'Local government area').props.onChangeText('Ikeja'); });
  // a state has to come from the list
  expect(cont().props.disabled).toBe(true);
  await act(async () => { byLabel(tree, 'Lagos').props.onPress(); });
  expect(cont().props.disabled).toBe(false);
  await act(async () => { await cont().props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.addressSave, { house_number: '12', street: 'Allen Avenue', state: 'Lagos', lga: 'Ikeja' });
  // saving the address verifies nothing, so the Tier 3 endpoint is never called
  expect(mockApiJson.mock.calls.map(([p]) => p)).not.toContain(EP.kyc.address);
  expect(mockReplace).toHaveBeenCalledWith('/setup');
  await act(async () => tree.unmount());
});
