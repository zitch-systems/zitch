import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import Kyc from '@/app/(auth)/kyc';
import { EP } from '@/lib/endpoints';

// Sign-up's "Identity and address" page: the identity screen in onboarding mode.
const mockApiJson = jest.fn();
const mockReplace = jest.fn();
const mockFaceSheet = jest.fn();
const mockLiveness = jest.fn();
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
jest.mock('@/components/design/FaceVerifyModal', () => (props: unknown) => { mockFaceSheet(props); return null; });
jest.mock('@/lib/session', () => ({
  beginExternalActivity: jest.fn(), endExternalActivity: jest.fn(),
  isSessionLocked: async () => false, isExternalActivityActive: () => false,
  enforceHardExpiry: jest.fn(), enforceIdleTimeout: jest.fn(), lockIfAwayTooLong: jest.fn(),
}));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/FaceLivenessModal', () => (props: unknown) => { mockLiveness(props); return null; });
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
    Field: ({ label, value, onChangeText, onFocus, keyboardType, placeholder }: { label: string; value: string; onChangeText: (v: string) => void; onFocus?: () => void; keyboardType?: string; placeholder?: string }) =>
      R.createElement(TextInput, { accessibilityLabel: label, value, onChangeText, onFocus, keyboardType: keyboardType as any, placeholder }),
  };
});

const base = { success: true, tier: 0, transaction_limit: '0', bvn_verified: false, nin_verified: false, face_verified: false,
  email_verified: false, account_provider: 'partnership', identity_face_available: true };

const byLabel = (tree: renderer.ReactTestRenderer, label: string) =>
  tree.root.findAll((n) => n.props.accessibilityLabel === label && (n.props.onPress || n.props.onChangeText))[0];

beforeEach(() => {
  mockApiJson.mockReset();
  mockReplace.mockReset();
  mockFaceSheet.mockReset();
  mockLiveness.mockReset();
});

it('starts on the BVN form, even before the email is verified, and sends the SMS code', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? base
    : { success: true, otp_required: true, identity_verification_provider: 'prembly', delivery: 'phone •••8888' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  expect(JSON.stringify(tree.toJSON())).toContain('Identity and address');
  expect(JSON.stringify(tree.toJSON())).not.toContain('Verify email');
  expect(byLabel(tree, 'Send code').props.disabled).toBe(true);
  await act(async () => { byLabel(tree, 'Bank Verification Number (BVN)').props.onChangeText('12345678901'); });
  await act(async () => { await byLabel(tree, 'Send code').props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.bvnStart, { bvn: '12345678901' });
  expect(JSON.stringify(tree.toJSON())).toContain('phone •••8888');
  await act(async () => tree.unmount());
});

it('keeps the code step on the same page: BVN, the SMS/face choice, then the code boxes', async () => {
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return base;
    if (path === EP.kyc.bvnStart) return { success: true, otp_required: true, identity_verification_provider: 'prembly',
      tracking_id: 'track-1', otp_route: 'bvn', delivery: '0801 ••• 5678' };
    return { success: false };
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { byLabel(tree, 'Bank Verification Number (BVN)').props.onChangeText('12345678901'); });
  await act(async () => { await byLabel(tree, 'Send code').props.onPress(); });
  const text = () => JSON.stringify(tree.toJSON());
  expect(text()).toContain('Identity and address');
  expect(text()).not.toContain('Confirm your BVN');
  expect(byLabel(tree, 'Bank Verification Number (BVN)').props.value).toBe('12345678901');
  expect(!!byLabel(tree, 'SMS code, Sent to the phone on your BVN')).toBe(true);
  expect(text()).toContain('Enter the code sent to ');
  expect(text()).toContain('0801 ••• 5678');
  expect(text()).toContain('It can take a minute to arrive.');
  expect(text()).toContain('Resend in 1:00');
  expect(byLabel(tree, 'Resend BVN code').props.disabled).toBe(true);
  expect(byLabel(tree, 'Continue').props.disabled).toBe(true);
  await act(async () => { byLabel(tree, '6-digit BVN verification code').props.onChangeText('123456'); });
  expect(byLabel(tree, 'Continue').props.disabled).toBe(false);
  await act(async () => tree.unmount());
});

it('opens the BVN face check inside the app, polls it, and closes it once verified', async () => {
  jest.useFakeTimers();
  let verified = false;
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return verified ? { ...base, bvn_verified: true, tier: 1 } : base;
    if (path === EP.kyc.identityFaceStart) return { success: true, url: 'https://face.example.com/check', session: 'face-session' };
    if (path === EP.kyc.identityFaceStatus) return verified
      ? { ...base, success: true, bvn_verified: true, tier: 1, status: 'verified' }
      : { success: true, status: 'pending' };
    return { success: false };
  });
  try {
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Kyc />); });
    await act(async () => { byLabel(tree, 'Bank Verification Number (BVN)').props.onChangeText('12345678901'); });
    await act(async () => { byLabel(tree, 'Face check, A quick selfie to match your BVN').props.onPress(); });
    await act(async () => { await byLabel(tree, 'Start face check').props.onPress(); });
    const sheet = () => mockFaceSheet.mock.calls[mockFaceSheet.mock.calls.length - 1][0] as { url: string; visible: boolean; footer?: string };
    expect(sheet()).toMatchObject({ url: 'https://face.example.com/check', visible: true });
    expect(sheet().footer).toBe('Your photo is used only to match your BVN and is never stored by Zitch. When it’s done, you come straight back to finish sign-up.');
    await act(async () => { jest.advanceTimersByTime(3000); });
    expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.identityFaceStatus, { session: 'face-session' });
    expect(sheet().visible).toBe(true);
    verified = true;
    await act(async () => { jest.advanceTimersByTime(3000); });
    await act(async () => {});
    expect(sheet().visible).toBe(false);
    expect(JSON.stringify(tree.toJSON())).toContain('Your BVN is verified. Add your NIN, take a quick face check, then your address.');
    await act(async () => tree.unmount());
  } finally {
    jest.useRealTimers();
  }
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

it('asks for the address once the BVN is verified, sends it as the Tier 3 request and moves on', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? { ...base, bvn_verified: true, tier: 1 }
    : { success: true, address: '12 Allen Avenue, Ikeja, Lagos' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  expect(JSON.stringify(tree.toJSON())).toContain('Your BVN is verified. Add your NIN, take a quick face check, then your address.');
  const cont = () => byLabel(tree, 'Continue');
  expect(cont().props.disabled).toBe(true);
  await act(async () => { byLabel(tree, 'House number').props.onChangeText('12'); });
  await act(async () => { byLabel(tree, 'Street address').props.onChangeText('Allen Avenue'); });
  await act(async () => { byLabel(tree, 'State').props.onChangeText('lag'); });
  await act(async () => { byLabel(tree, 'Local government area').props.onChangeText('Ikeja'); });
  // a state has to come from the list
  expect(cont().props.disabled).toBe(true);
  await act(async () => { byLabel(tree, 'Lagos').props.onPress(); });
  expect(byLabel(tree, 'House number').props.keyboardType).toBe('number-pad');
  expect(byLabel(tree, 'Local government area').props.placeholder).toBe('Select or type your LGA');
  expect(cont().props.disabled).toBe(false);
  await act(async () => { await cont().props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.tier3Send, { house_number: '12', street: 'Allen Avenue', state: 'Lagos', lga: 'Ikeja' });
  // no NIN or face check given: no Tier 2 request, and no address "verification" call
  expect(mockApiJson.mock.calls.map(([p]) => p)).not.toContain(EP.wallet.wemaUpgradeTier2);
  expect(mockApiJson.mock.calls.map(([p]) => p)).not.toContain(EP.kyc.address);
  expect(mockReplace).toHaveBeenCalledWith('/setup');
  await act(async () => tree.unmount());
});

it('suggests the LGAs of the chosen state, and still takes one typed that is not listed', async () => {
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? { ...base, bvn_verified: true, tier: 1 }
    : { success: true });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { byLabel(tree, 'House number').props.onChangeText('12'); });
  await act(async () => { byLabel(tree, 'Street address').props.onChangeText('Allen Avenue'); });
  await act(async () => { byLabel(tree, 'State').props.onChangeText('Lagos'); });
  await act(async () => { byLabel(tree, 'Local government area').props.onChangeText('ik'); });
  expect(!!byLabel(tree, 'Ikeja')).toBe(true);
  expect(!!byLabel(tree, 'Ikorodu')).toBe(true);
  expect(!!byLabel(tree, 'Abuja Municipal')).toBe(false);
  await act(async () => { byLabel(tree, 'Ikeja').props.onPress(); });
  expect(byLabel(tree, 'Local government area').props.value).toBe('Ikeja');
  await act(async () => { byLabel(tree, 'Local government area').props.onChangeText('Ikeja GRA'); });
  expect(byLabel(tree, 'Continue').props.disabled).toBe(false);
  await act(async () => { await byLabel(tree, 'Continue').props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.tier3Send, { house_number: '12', street: 'Allen Avenue', state: 'Lagos', lga: 'Ikeja GRA' });
  await act(async () => tree.unmount());
});

it('takes the NIN, face check and address while the BVN is verified, and sends them only once it is', async () => {
  jest.useFakeTimers();
  let verified = false;
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return verified ? { ...base, bvn_verified: true, tier: 1 } : base;
    if (path === EP.kyc.identityFaceStart) return { success: true, url: 'https://face.example.com/check', session: 'face-session' };
    if (path === EP.kyc.identityFaceStatus) return verified
      ? { ...base, success: true, bvn_verified: true, tier: 1, status: 'verified' } : { success: true, status: 'pending' };
    if (path === EP.wallet.wemaUpgradeTier2) return { success: true, sent: true, tier2_state: 'pending' };
    if (path === EP.kyc.tier3Send) return { success: true, sent: true, tier3_state: 'processing' };
    return { success: false };
  });
  try {
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Kyc />); });
    const text = () => JSON.stringify(tree.toJSON());
    expect(text()).toContain('Dial *346# on the phone linked to your NIN to get it.');
    expect(text()).toContain('A quick face check');
    // all four steps are on the one page and can be filled in now
    await act(async () => { byLabel(tree, 'Bank Verification Number (BVN)').props.onChangeText('12345678901'); });
    await act(async () => { byLabel(tree, 'National Identification Number (NIN)').props.onChangeText('1234 5678 901'); });
    expect(byLabel(tree, 'Start face check').props.disabled).toBeFalsy();
    await act(async () => { byLabel(tree, 'Start face check').props.onPress(); });
    const modal = mockLiveness.mock.calls[mockLiveness.mock.calls.length - 1][0] as { visible: boolean; onCapture: (c: { image: string; token: string }) => void };
    expect(modal.visible).toBe(true);
    await act(async () => { modal.onCapture({ image: 'face-b64', token: 'proof' }); });
    expect(text()).toContain('Face check done');
    await act(async () => { byLabel(tree, 'House number').props.onChangeText('12'); });
    await act(async () => { byLabel(tree, 'Street address').props.onChangeText('Allen Avenue'); });
    await act(async () => { byLabel(tree, 'State').props.onChangeText('Lagos'); });
    await act(async () => { byLabel(tree, 'Local government area').props.onChangeText('Ikeja'); });
    // nothing goes to the bank before the BVN opens the account
    expect(mockApiJson.mock.calls.map(([p]) => p)).not.toEqual(expect.arrayContaining([EP.wallet.wemaUpgradeTier2]));
    expect(mockApiJson.mock.calls.map(([p]) => p)).not.toEqual(expect.arrayContaining([EP.kyc.tier3Send]));

    // the BVN by its own face check
    await act(async () => { byLabel(tree, 'Face check, A quick selfie to match your BVN').props.onPress(); });
    await act(async () => { await byLabel(tree, 'Start face check').props.onPress(); });
    verified = true;
    await act(async () => { jest.advanceTimersByTime(3000); });
    await act(async () => {});
    expect(text()).toContain('Your BVN is verified, and your NIN and face check are in. Now add your address.');
    // what was given carries over: no BVN asked for again, the address kept
    expect(byLabel(tree, 'House number').props.value).toBe('12');
    expect(byLabel(tree, 'Continue').props.disabled).toBe(false);
    await act(async () => { await byLabel(tree, 'Continue').props.onPress(); });
    expect(mockApiJson).toHaveBeenCalledWith(EP.wallet.wemaUpgradeTier2,
      { bvn: '12345678901', nin: '12345678901', live_image: 'face-b64', liveness_token: 'proof', at_signup: true });
    expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.tier3Send, { house_number: '12', street: 'Allen Avenue', state: 'Lagos', lga: 'Ikeja' });
    expect(mockReplace).toHaveBeenCalledWith('/setup');
    await act(async () => tree.unmount());
  } finally {
    jest.useRealTimers();
  }
});

it('does not send a request the bank already has again when the other one needs a retry', async () => {
  let addressAnswer: Record<string, unknown> = { success: false, message: 'Choose your LGA from the list.' };
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? { ...base, bvn_verified: true, tier: 1 }
    : path === EP.wallet.wemaUpgradeTier2 ? { success: true, sent: true, tier2_state: 'pending' }
    : path === EP.kyc.tier3Send ? addressAnswer : { success: false });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { byLabel(tree, 'National Identification Number (NIN)').props.onChangeText('12345678901'); });
  await act(async () => { byLabel(tree, 'Start face check').props.onPress(); });
  await act(async () => { (mockLiveness.mock.calls[mockLiveness.mock.calls.length - 1][0] as { onCapture: (c: object) => void }).onCapture({ image: 'f', token: 't' }); });
  await act(async () => { byLabel(tree, 'Bank Verification Number (BVN)').props.onChangeText('22222222222'); });
  await act(async () => { byLabel(tree, 'House number').props.onChangeText('12'); });
  await act(async () => { byLabel(tree, 'Street address').props.onChangeText('Allen Avenue'); });
  await act(async () => { byLabel(tree, 'State').props.onChangeText('Lagos'); });
  await act(async () => { byLabel(tree, 'Local government area').props.onChangeText('Ikeja'); });
  await act(async () => { await byLabel(tree, 'Continue').props.onPress(); });
  expect(mockReplace).not.toHaveBeenCalled();
  addressAnswer = { success: true, sent: true, tier3_state: 'processing' };
  await act(async () => { await byLabel(tree, 'Continue').props.onPress(); });
  const calls = mockApiJson.mock.calls.map(([p]) => p);
  expect(calls.filter((p) => p === EP.wallet.wemaUpgradeTier2)).toHaveLength(1);
  expect(calls.filter((p) => p === EP.kyc.tier3Send)).toHaveLength(2);
  expect(mockReplace).toHaveBeenCalledWith('/setup');
  await act(async () => tree.unmount());
});

it('sends the NIN and face check (Tier 2) and the address (Tier 3) as separate requests at the same time', async () => {
  const started: string[] = [];
  const release: (() => void)[] = [];
  mockApiJson.mockImplementation((path: string) => {
    if (path === EP.kyc.status) return Promise.resolve({ ...base, bvn_verified: true, tier: 1 });
    started.push(path);
    // neither answers until both have been sent
    return new Promise((resolve) => release.push(() => resolve(path === EP.kyc.tier3Send
      ? { success: true, sent: true, tier3_state: 'processing' }
      : { success: true, sent: true, tier2_state: 'pending' })));
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { byLabel(tree, 'National Identification Number (NIN)').props.onChangeText('1234 5678 901'); });
  expect(byLabel(tree, 'Continue').props.disabled).toBe(true);   // NIN without the face check
  await act(async () => { byLabel(tree, 'Start face check').props.onPress(); });
  const modal = () => mockLiveness.mock.calls[mockLiveness.mock.calls.length - 1][0] as { visible: boolean; onCapture: (c: { image: string; token: string }) => void };
  expect(modal().visible).toBe(true);
  await act(async () => { modal().onCapture({ image: 'face-b64', token: 'proof' }); });
  expect(JSON.stringify(tree.toJSON())).toContain('Face check done');
  // the BVN was verified in an earlier session: the bank needs it typed again
  await act(async () => { byLabel(tree, 'Bank Verification Number (BVN)').props.onChangeText('22222222222'); });
  await act(async () => { byLabel(tree, 'House number').props.onChangeText('12'); });
  await act(async () => { byLabel(tree, 'Street address').props.onChangeText('Allen Avenue'); });
  await act(async () => { byLabel(tree, 'State').props.onChangeText('Lagos'); });
  await act(async () => { byLabel(tree, 'Local government area').props.onChangeText('Ikeja'); });
  let pressed!: Promise<void>;
  await act(async () => { pressed = byLabel(tree, 'Continue').props.onPress(); });
  expect(started).toEqual([EP.wallet.wemaUpgradeTier2, EP.kyc.tier3Send]);
  expect(mockApiJson).toHaveBeenCalledWith(EP.wallet.wemaUpgradeTier2,
    { bvn: '22222222222', nin: '12345678901', live_image: 'face-b64', liveness_token: 'proof', at_signup: true });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.tier3Send, { house_number: '12', street: 'Allen Avenue', state: 'Lagos', lga: 'Ikeja' });
  await act(async () => { release.forEach((r) => r()); await pressed; });
  expect(mockReplace).toHaveBeenCalledWith('/setup');
  await act(async () => tree.unmount());
});

describe('the notice after sending (17 Nov: a confirmed Tier 2 applies at once)', () => {
  const { notify } = jest.requireMock('@/components/design/Notify') as { notify: jest.Mock };
  const fillAndSend = async (tier2Answer: Record<string, unknown>) => {
    mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status ? { ...base, bvn_verified: true, tier: 1 }
      : path === EP.wallet.wemaUpgradeTier2 ? tier2Answer
      : path === EP.kyc.tier3Send ? { success: true, sent: true, tier3_state: 'processing' } : { success: false });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Kyc />); });
    await act(async () => { byLabel(tree, 'National Identification Number (NIN)').props.onChangeText('12345678901'); });
    await act(async () => { byLabel(tree, 'Start face check').props.onPress(); });
    await act(async () => { (mockLiveness.mock.calls[mockLiveness.mock.calls.length - 1][0] as { onCapture: (c: object) => void }).onCapture({ image: 'f', token: 't' }); });
    await act(async () => { byLabel(tree, 'Bank Verification Number (BVN)').props.onChangeText('22222222222'); });
    await act(async () => { byLabel(tree, 'House number').props.onChangeText('12'); });
    await act(async () => { byLabel(tree, 'Street address').props.onChangeText('Allen Avenue'); });
    await act(async () => { byLabel(tree, 'State').props.onChangeText('Lagos'); });
    await act(async () => { byLabel(tree, 'Local government area').props.onChangeText('Ikeja'); });
    await act(async () => { await byLabel(tree, 'Continue').props.onPress(); });
    await act(async () => tree.unmount());
  };

  beforeEach(() => notify.mockReset());

  it('says Tier 2 is confirmed when the bank confirmed it there and then', async () => {
    await fillAndSend({ success: true, sent: true, tier2_state: 'confirmed', tier: 2 });
    expect(notify).toHaveBeenCalledWith('Tier 2 confirmed',
      'Your new limits apply now. Your bank is checking your address for Tier 3.', 'success');
    expect(JSON.stringify(notify.mock.calls)).not.toContain('Verify Tier 2');
  });

  it('never claims Tier 2 while the account is not on it, and asks for no tap on Verify', async () => {
    await fillAndSend({ success: true, sent: true, tier2_state: 'confirmed', tier: 0 });   // Tier 1 unfinished
    await fillAndSend({ success: true, sent: true, tier2_state: 'pending', tier: 1 });
    expect(notify.mock.calls.map((c) => c[0])).toEqual(['Sent for checking', 'Sent for checking']);
    expect(notify.mock.calls[0][1]).toBe('Your bank is checking them. Each tier applies as soon as the bank confirms it. Account limits shows where they are.');
  });
});
