import React, { type ReactNode } from 'react';
import { TextInput } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import Tier2 from '@/app/(auth)/tier2';
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
jest.mock('@/components/design/FaceVerifyModal', () => (props: { visible: boolean }) => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return props.visible ? R.createElement(View, { ...props, testID: 'face-page' }) : null;
});
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#066E66', ink1: '#111', ink2: '#222', ink3: '#333', line: '#ddd', surface: '#fff' }, theme: 'light' }),
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

const tier1 = {
  success: true, tier: 1, transaction_limit: '50000', bvn_verified: true, nin_verified: false, face_verified: false,
  email_verified: true, phone_verified: true, account_provider: 'partnership',
  tier2_face_flow: 'prembly_sdk', tier2_face_available: true,
};
const FACE_URL = 'https://sdk-live.prembly.com/?session=opaque&has_callback=false';

const render = async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Tier2 />); });
  return tree;
};
const text = (tree: renderer.ReactTestRenderer) => JSON.stringify(tree.toJSON());
const facePages = (tree: renderer.ReactTestRenderer) => tree.root.findAll((n) => n.props.testID === 'face-page' && typeof n.type === 'string').length;
const submitBtn = (tree: renderer.ReactTestRenderer) => tree.root.findByProps({ accessibilityLabel: 'Submit for Tier 2' });

beforeEach(() => {
  mockApiJson.mockReset();
  mockNotify.mockReset();
  Object.values(mockRouter).forEach((fn) => fn.mockClear());
  delete mockParams.from;
});

it('shows the design copy and keeps submit off until the NIN and face check are done', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? tier1 : { success: false });
  const tree = await render();
  const out = text(tree);
  for (const copy of ['Upgrade to Tier 2 Your NIN and a quick face check', 'Enhanced', 'Your NIN and a face check', 'Tier 1 now', '₦200000', '₦1000000',
    'Your NIN', 'We check your NIN to confirm it’s yours.', 'Face check',
    'A short selfie video. We match it to your NIN photo. It takes about a minute.',
    'Your sign-up address is sent with this, so the Tier 3 check starts straight away.',
    'Finish the face check to submit.']) expect(out).toContain(copy);
  expect(out).not.toMatch(/Prembly|Wema/);
  expect(submitBtn(tree).props.disabled).toBe(true);
  act(() => tree.unmount());
});

it('verifies the NIN with the code the server asks for', async () => {
  let ninVerified = false;
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return { ...tier1, nin_verified: ninVerified };
    if (path === EP.kyc.nin) return { success: true, otp_required: true, identity_verification_provider: 'prembly', delivery: 'phone •••8888' };
    if (path === EP.kyc.ninConfirm) { ninVerified = true; return { ...tier1, nin_verified: true }; }
    throw new Error(`Unexpected API call: ${path}`);
  });
  const tree = await render();
  await act(async () => { tree.root.findByType(TextInput).props.onChangeText('3456 7890 123'); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Verify NIN' }).props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.nin, { nin: '34567890123' });
  expect(text(tree)).toContain('phone •••8888');
  await act(async () => { tree.root.findByType(TextInput).props.onChangeText('123456'); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Confirm NIN' }).props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.ninConfirm, { otp: '123456' });
  expect(text(tree)).toContain('Verified');
  // NIN alone does not unlock submit: the face check is still outstanding.
  expect(submitBtn(tree).props.disabled).toBe(true);
  expect(mockApiJson.mock.calls.map(([p]) => p)).not.toContain(EP.wallet.wemaUpgradeTier2);
  act(() => tree.unmount());
});

it('holds the face page in-app, polls until verified, then submits and returns to account limits', async () => {
  jest.useFakeTimers();
  mockParams.from = 'limits';
  let polls = 0;
  let faceDone = false;
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return { ...tier1, nin_verified: true, face_verified: faceDone, tier: faceDone ? 2 : 1 };
    if (path === EP.kyc.livenessStart) return { success: true, session: 'server-bound', url: FACE_URL };
    if (path === EP.kyc.livenessStatus) {
      polls += 1;
      if (polls < 2) return { success: false, pending: true, status: 'pending' };
      faceDone = true;
      return { ...tier1, nin_verified: true, face_verified: true, tier: 2, success: true, status: 'verified' };
    }
    if (path === EP.kyc.address) return { success: false };
    throw new Error(`Unexpected API call: ${path}`);
  });
  let tree!: renderer.ReactTestRenderer;
  try {
    tree = await render();
    expect(text(tree)).toContain('Verified'); // NIN already done
    await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Start face check' }).props.onPress(); });
    const page = tree.root.findByProps({ testID: 'face-page' });
    expect(page.props.url).toBe(FACE_URL);
    expect(page.props.title).toBe('Face check');
    expect(String(page.props.subtitle) + String(page.props.footer)).not.toMatch(/Prembly|Wema|bank/);
    await act(async () => { jest.advanceTimersByTime(3000); });
    expect(facePages(tree)).toBe(1);
    await act(async () => { jest.advanceTimersByTime(3000); });
    expect(facePages(tree)).toBe(0);
    expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.livenessStatus, { session: 'server-bound' });
    expect(text(tree)).not.toContain('Finish the face check to submit.');
    expect(submitBtn(tree).props.disabled).toBe(false);
    await act(async () => { await submitBtn(tree).props.onPress(); });
    expect(mockNotify).toHaveBeenCalledWith('You’re now Tier 2', expect.any(String), 'success');
    expect(mockRouter.back).toHaveBeenCalled();
    expect(mockApiJson.mock.calls.map(([p]) => p)).not.toContain(EP.wallet.wemaUpgradeTier2);
  } finally {
    act(() => tree?.unmount());
    jest.useRealTimers();
  }
});

it('checks once when the face page is closed and goes to account limits when not opened from there', async () => {
  let faceDone = false;
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return { ...tier1, nin_verified: true, face_verified: faceDone, tier: faceDone ? 2 : 1 };
    if (path === EP.kyc.livenessStart) return { success: true, session: 'server-bound', url: FACE_URL };
    if (path === EP.kyc.livenessStatus) { faceDone = true; return { ...tier1, nin_verified: true, face_verified: true, tier: 2, success: true, status: 'verified' }; }
    return { success: false };
  });
  const tree = await render();
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Start face check' }).props.onPress(); });
  await act(async () => { tree.root.findByProps({ testID: 'face-page' }).props.onClose(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.livenessStatus, { session: 'server-bound' });
  await act(async () => { await submitBtn(tree).props.onPress(); });
  expect(mockRouter.replace).toHaveBeenCalledWith('/accountlimits');
  act(() => tree.unmount());
});

it('never opens a face page from an unexpected origin', async () => {
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return tier1;
    if (path === EP.kyc.livenessStart) return { success: true, session: 's', url: 'https://evil.example/?session=x' };
    throw new Error(`Unexpected API call: ${path}`);
  });
  const tree = await render();
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Start face check' }).props.onPress(); });
  expect(facePages(tree)).toBe(0);
  expect(mockNotify).toHaveBeenCalledWith('Face check unavailable', expect.any(String));
  act(() => tree.unmount());
});
