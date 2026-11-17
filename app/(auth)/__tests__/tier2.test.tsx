import React, { type ReactNode } from 'react';
import { TextInput } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import Tier2 from '@/app/(auth)/tier2';
import { EP } from '@/lib/endpoints';

const mockApiJson = jest.fn();
const mockNotify = jest.fn();
const mockRouter = { back: jest.fn(), push: jest.fn(), replace: jest.fn(), canGoBack: jest.fn(() => true) };
const mockParams: { from?: string; recheck?: string } = {};
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
jest.mock('@/components/design/FaceLivenessModal', () => (props: { visible: boolean }) => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return props.visible ? R.createElement(View, { ...props, testID: 'face-check' }) : null;
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
  email_verified: true, phone_verified: true, account_provider: 'partnership', has_wema_account: true,
  tier2_face_rail: 'wema', tier2_face_flow: 'wema_selfie', tier2_face_available: true,
};
const CAPTURE = { image: 'CHOSEN-STRAIGHT-PHOTO', token: 'proof-for-that-photo' };

const render = async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Tier2 />); });
  return tree;
};
const text = (tree: renderer.ReactTestRenderer) => JSON.stringify(tree.toJSON());
const submitBtn = (tree: renderer.ReactTestRenderer) => tree.root.findByProps({ accessibilityLabel: 'Submit for Tier 2' });
const fill = (tree: renderer.ReactTestRenderer, bvn = '2222 2222 222', nin = '3333 3333 333') => {
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Bank Verification Number (BVN)' }).props.onChangeText(bvn); });
  act(() => { tree.root.findByProps({ accessibilityLabel: 'National Identification Number (NIN)' }).props.onChangeText(nin); });
};
const passFaceCheck = async (tree: renderer.ReactTestRenderer, capture = CAPTURE) => {
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Start face check' }).props.onPress(); });
  await act(async () => { tree.root.findByProps({ testID: 'face-check' }).props.onCapture(capture); });
};

beforeEach(() => {
  mockApiJson.mockReset();
  mockNotify.mockReset();
  Object.values(mockRouter).forEach((fn) => fn.mockClear());
  delete mockParams.from;
  delete mockParams.recheck;
});

it('asks for the BVN, NIN and the live face check, and keeps submit off until all three are done', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? tier1 : { success: false });
  const tree = await render();
  const out = text(tree);
  for (const copy of ['Upgrade to Tier 2 Your BVN, NIN and a live face check', 'Tier 1 now', 'Your BVN and NIN',
    'Live face check', 'A quick face check', 'Enter your BVN and NIN to submit.']) expect(out).toContain(copy);
  expect(out).not.toMatch(/Prembly|Wema/);
  // Tier 2 sends no address: Tier 3 is its own request (review, Nov 2026)
  expect(out).not.toContain('address');
  expect(tree.root.findAllByType(TextInput)).toHaveLength(2);
  expect(submitBtn(tree).props.disabled).toBe(true);
  fill(tree, '2222 2222 222', '');
  expect(submitBtn(tree).props.disabled).toBe(true);
  // A face check passed before both numbers are in is held, not sent.
  await passFaceCheck(tree);
  expect(text(tree)).toContain('Face check done');
  expect(submitBtn(tree).props.disabled).toBe(true);
  fill(tree);
  expect(submitBtn(tree).props.disabled).toBe(false);
  expect(mockApiJson.mock.calls.map(([p]) => p)).not.toContain(EP.wallet.wemaUpgradeTier2);
  act(() => tree.unmount());
});

it('asks for the face check once both numbers are in', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? tier1 : { success: false });
  const tree = await render();
  fill(tree);
  expect(text(tree)).toContain('Take the live face check to submit.');
  expect(submitBtn(tree).props.disabled).toBe(true);
  act(() => tree.unmount());
});

it('sends both numbers with the chosen photo and its proof once, then returns to account limits', async () => {
  mockParams.from = 'limits';
  let upgraded = false;
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return upgraded ? { ...tier1, nin_verified: true, face_verified: true, tier: 2 } : tier1;
    if (path === EP.wallet.wemaUpgradeTier2) { upgraded = true; return { success: true, tier: 2, face_verified: true }; }
    if (path === EP.kyc.address) return { success: false };
    throw new Error(`Unexpected API call: ${path}`);
  });
  const tree = await render();
  fill(tree);
  // Passing the face check with both numbers typed sends it straight away.
  await passFaceCheck(tree);
  expect(mockApiJson).toHaveBeenCalledWith(EP.wallet.wemaUpgradeTier2, {
    bvn: '22222222222', nin: '33333333333', live_image: CAPTURE.image, liveness_token: CAPTURE.token });
  expect(mockApiJson.mock.calls.filter(([p]) => p === EP.wallet.wemaUpgradeTier2)).toHaveLength(1);
  expect(mockNotify).toHaveBeenCalledWith('You’re now Tier 2', expect.any(String), 'success');
  expect(mockRouter.back).toHaveBeenCalled();
  act(() => tree.unmount());
});

it('asks for a new face check when the bank or our check refuses it, but keeps it for a wrong NIN', async () => {
  const answers = [
    { success: false, code: 'that-nin', message: 'That NIN does not match the NIN already verified on this account.', _httpStatus: 409 },
    { success: false, message: 'Your bank could not confirm your face.', _httpStatus: 502 },
  ];
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return tier1;
    if (path === EP.wallet.wemaUpgradeTier2) return answers.shift();
    throw new Error(`Unexpected API call: ${path}`);
  });
  const tree = await render();
  fill(tree);
  await passFaceCheck(tree);
  expect(mockNotify).toHaveBeenLastCalledWith('Not upgraded yet', 'That NIN does not match the NIN already verified on this account.');
  expect(text(tree)).toContain('Face check done');
  await act(async () => { await submitBtn(tree).props.onPress(); });
  expect(mockNotify).toHaveBeenLastCalledWith('Not upgraded yet', 'Your bank could not confirm your face.');
  expect(text(tree)).not.toContain('Face check done');
  expect(submitBtn(tree).props.disabled).toBe(true);
  act(() => tree.unmount());
});

it('shows a bank still checking the face, and finishes from Check again', async () => {
  let done = false;
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return done ? { ...tier1, face_verified: true, tier: 2 } : { ...tier1, tier2_face_pending: true };
    if (path === EP.kyc.address) return { success: false };
    // Check again is a tap on Tier 2: it reads the bank back and applies a confirmed check.
    if (path === EP.kyc.claim) return done ? { success: true, state: 'upgraded', tier: 2 } : { success: true, state: 'processing', tier: 1 };
    throw new Error(`Unexpected API call: ${path}`);
  });
  const tree = await render();
  expect(text(tree)).toContain('Your bank is still checking your details');
  expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
  done = true;
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Check again' }).props.onPress(); });
  expect(mockNotify).toHaveBeenCalledWith('You’re now Tier 2', expect.any(String), 'success');
  expect(mockRouter.replace).toHaveBeenCalledWith('/accountlimits');
  act(() => tree.unmount());
});

it('says what comes first when Tier 2 cannot start', async () => {
  for (const [state, copy] of [
    [{ tier2_face_available: false, tier2_unavailable_reason: 'Back soon.' }, 'Back soon.'],
    [{ has_wema_account: false }, 'Set up your account first'],
  ] as const) {
    mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? { ...tier1, ...state } : { success: false });
    const tree = await render();
    expect(text(tree)).toContain(copy);
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Submit for Tier 2' })).toHaveLength(0);
    act(() => tree.unmount());
  }
});

it('moves up at once when the bank already confirmed what sign-up sent', async () => {
  let claimed = false;
  mockApiJson.mockImplementation(async (path: string, body?: { tier?: number }) => {
    if (path === EP.kyc.status) return claimed ? { ...tier1, face_verified: true, nin_verified: true, tier: 2, tier2_state: 'done' } : { ...tier1, tier2_state: 'ready' };
    if (path === EP.kyc.claim) { expect(body).toEqual({ tier: 2 }); claimed = true; return { success: true, state: 'upgraded', tier: 2 }; }
    throw new Error(`Unexpected API call: ${path}`);
  });
  const tree = await render();
  expect(text(tree)).toContain('Your bank confirmed your details');
  expect(tree.root.findAllByType(TextInput)).toHaveLength(0);   // nothing to enter again
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Move to Tier 2' }).props.onPress(); });
  expect(mockNotify).toHaveBeenCalledWith('You’re now Tier 2', expect.any(String), 'success');
  expect(mockRouter.replace).toHaveBeenCalledWith('/accountlimits');
  act(() => tree.unmount());
});

it('says the sign-up check was not confirmed when Verify Tier 2 sends the customer back to it', async () => {
  mockParams.recheck = '1';
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status
    ? { ...tier1, tier2_state: 'not_confirmed', tier2_review: 'rejected' } : { success: false });
  const tree = await render();
  const out = text(tree);
  expect(out).toContain('Your details need review. Your bank couldn’t confirm your NIN or face check.');
  expect(out).toContain('Enter your NIN and retake the face check');
  expect(tree.root.findAllByType(TextInput)).toHaveLength(2);
  act(() => tree.unmount());
  // nothing was sent at sign-up: no red banner, just the form
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status
    ? { ...tier1, tier2_state: 'not_confirmed', tier2_review: '' } : { success: false });
  const fresh = await render();
  expect(text(fresh)).not.toContain('need review');
  act(() => fresh.unmount());
});
