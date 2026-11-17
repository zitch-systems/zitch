import React, { type ReactNode } from 'react';
import { Text, TextInput } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import Kyc from '@/app/(auth)/kyc';
import { EP } from '@/lib/endpoints';

const mockApiJson = jest.fn();
const mockParams = { verify_identity: 'bvn' };
const mockGetToken = jest.fn();
const mockSessionLocked = jest.fn();
const mockRedirect = jest.fn();
const mockPush = jest.fn();
const mockNotify = jest.fn();
const mockPick = jest.fn();
const mockFaceSheet = jest.fn();
let mockGeneration = 0;
let mockBlur: (() => void) | undefined;
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('@/lib/secureStore', () => ({ getToken: () => mockGetToken(), getSessionGeneration: () => mockGeneration }));
jest.mock('expo-router', () => ({
  router: { back: jest.fn(), push: (...args: unknown[]) => mockPush(...args) }, useLocalSearchParams: () => mockParams,
  Redirect: (props: { href: string }) => { mockRedirect(props.href); return null; },
  useFocusEffect: (callback: () => (() => void) | void) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    ReactActual.useEffect(() => {
      const cleanup = callback();
      mockBlur = cleanup || undefined;
      return cleanup;
    }, [callback]);
  },
}));
jest.mock('expo-image-picker', () => ({
  requestMediaLibraryPermissionsAsync: async () => ({ granted: true }),
  launchImageLibraryAsync: (...args: unknown[]) => mockPick(...args),
  MediaTypeOptions: { Images: 'Images' },
}));
jest.mock('@/components/design/FaceVerifyModal', () => (props: unknown) => { mockFaceSheet(props); return null; });
jest.mock('@/lib/session', () => ({
  beginExternalActivity: jest.fn(), endExternalActivity: jest.fn(),
  isSessionLocked: () => mockSessionLocked(),
  isExternalActivityActive: () => false,
  enforceHardExpiry: jest.fn(), enforceIdleTimeout: jest.fn(), lockIfAwayTooLong: jest.fn(),
}));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/FaceLivenessModal', () => (props: any) => {
  const R = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return props.visible ? R.createElement(View, { ...props, testID: 'face-capture' }) : null;
});
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#0FA295', ink1: '#111', ink2: '#222', ink3: '#333', line: '#ddd', surface: '#fff' } }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text, TextInput, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Header: ({ onBack }: { onBack: () => void }) => ReactActual.createElement(Pressable, { accessibilityLabel: 'Go back', onPress: onBack }), money: String,
    Tap: ({ children, ...props }: { children: ReactNode }) => ReactActual.createElement(Pressable, props, children),
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) => ReactActual.createElement(
      Pressable, { accessibilityLabel: label, onPress, disabled }, ReactActual.createElement(Text, null, label)),
    Field: ({ value, onChangeText }: { value: string; onChangeText: (value: string) => void }) => ReactActual.createElement(
      TextInput, { value, onChangeText }),
  };
});

const state = { success: true, tier: 0, transaction_limit: '0', bvn_verified: false, nin_verified: false,
  face_verified: false, account_provider: 'partnership', identity_verification_provider: 'prembly' };

beforeEach(() => {
  mockGeneration = 0;
  mockNotify.mockReset();
  mockPick.mockReset();
  mockApiJson.mockReset();
  mockParams.verify_identity = 'bvn';
  mockGetToken.mockReset().mockResolvedValue('test-token');
  mockSessionLocked.mockReset().mockResolvedValue(false);
  mockRedirect.mockClear();
  mockPush.mockClear();
});

it.each(['bvn', 'nin'])('finishes a %s Prembly SMS challenge without bank tracking or account creation', async (kind) => {
  mockParams.verify_identity = kind;
  let verified = false;
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return { ...state, [`${kind}_verified`]: verified };
    if (path === (kind === 'bvn' ? EP.kyc.bvnConfirm : EP.kyc.ninConfirm)) {
      verified = true;
      return { ...state, [`${kind}_verified`]: true };
    }
    return { success: true, otp_required: true, identity_verification_provider: 'prembly', delivery: 'registered phone •••••8888' };
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { tree.root.findByType(TextInput).props.onChangeText('12345678901'); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: kind === 'bvn' ? 'Send verification code' : 'Send NIN verification code' }).props.onPress(); });
  expect(JSON.stringify(tree.toJSON())).toContain('registered phone •••••8888');
  await act(async () => { tree.root.findByType(TextInput).props.onChangeText('123456'); });
  const confirm = tree.root.findByProps({ accessibilityLabel: `Confirm ${kind.toUpperCase()}` });
  expect(confirm.props.disabled).toBe(false);
  await act(async () => { await confirm.props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(kind === 'bvn' ? EP.kyc.bvnConfirm : EP.kyc.ninConfirm, { otp: '123456' });
  expect(mockApiJson.mock.calls.map(([path]) => path)).not.toContain(EP.wallet.createAccount);
  expect(JSON.stringify(tree.toJSON())).not.toContain('12345678901');
  await act(async () => tree.unmount());
});

it.each(['bvn', 'nin'])('shows accepted %s OTP destinations and refreshes partial delivery on resend', async (kind) => {
  mockParams.verify_identity = kind;
  let attempts = 0;
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return state;
    attempts += 1;
    return {
      success: true, otp_required: true, identity_verification_provider: 'prembly',
      delivery: attempts === 1 ? 'registered phone •••••8888' : 'registered phone •••••8888 and email a***@example.com',
      delivery_channels: attempts === 1 ? ['sms'] : ['sms', 'email'],
      delivery_partial: attempts === 1,
      delivery_notice: attempts === 1 ? 'Email delivery failed. Use the code sent by SMS.' : '',
    };
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { tree.root.findByType(TextInput).props.onChangeText('12345678901'); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: kind === 'bvn' ? 'Send verification code' : 'Send NIN verification code' }).props.onPress(); });
  expect(JSON.stringify(tree.toJSON())).toContain('Email delivery failed. Use the code sent by SMS.');
  expect(JSON.stringify(tree.toJSON())).not.toContain('a***@example.com');
  const resend = tree.root.findByProps({ accessibilityLabel: `Resend ${kind.toUpperCase()} code` });
  await act(async () => { await resend.props.onPress(); });
  expect(JSON.stringify(tree.toJSON())).toContain('registered phone •••••8888 and email a***@example.com');
  expect(JSON.stringify(tree.toJSON())).not.toContain('Email delivery failed. Use the code sent by SMS.');
  await act(async () => tree.unmount());
});

it.each([
  { account_provider: 'wema_vas' },
  { address_verification_required: false },
  { address_rail: 'none' },
])('does not offer address verification when the server disables it: %j', async (providerPolicy) => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, tier: 2, bvn_verified: true, nin_verified: true,
    face_verified: true, email_verified: true, ...providerPolicy });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  expect(JSON.stringify(tree.toJSON())).not.toContain('Address verification');
  expect(JSON.stringify(tree.toJSON())).not.toContain('BVN/NIN are never stored in full.');
  await act(async () => tree.unmount());
});

it('shows Tier 3 (the home address) to a Tier 2 account and opens its own page', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, tier: 2, bvn_verified: true, nin_verified: true,
    face_verified: true, email_verified: true, account_provider: 'partnership',
    address_verification_required: true, address_rail: 'wema', tier3_state: 'not_sent' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Home address, Tier 3' }).props.onPress(); });
  expect(mockPush).toHaveBeenCalledWith('/tier3');
  await act(async () => tree.unmount());
});

it('refreshes an already verified BVN on the same profile without resetting verified history', async () => {
  const verifiedState = { ...state, tier: 2, bvn_verified: true, nin_verified: true,
    face_verified: true, email_verified: true, account_provider: 'partnership' };
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status || path === EP.kyc.bvnConfirm) return verifiedState;
    if (path === EP.kyc.bvnStart) return { success: true, otp_required: true,
      identity_verification_provider: 'prembly', delivery: 'registered phone •••••8888' };
    throw new Error(`Unexpected endpoint: ${path}`);
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  try {
    // An explicit proof refresh remains usable while the existing flags are true.
    await act(async () => { tree.root.findByType(TextInput).props.onChangeText('12345678901'); });
    await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Send verification code' }).props.onPress(); });
    expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.bvnStart, { bvn: '12345678901' });
    expect(JSON.stringify(tree.toJSON())).toContain('registered phone •••••8888');
    await act(async () => { tree.root.findByType(TextInput).props.onChangeText('123456'); });
    await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Confirm BVN' }).props.onPress(); });
    expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.bvnConfirm, { otp: '123456' });
    const paths = mockApiJson.mock.calls.map(([path]) => path);
    const confirmed = paths.indexOf(EP.kyc.bvnConfirm);
    expect(paths.slice(confirmed + 1)).toContain(EP.kyc.status);
    expect(new Set(paths)).toEqual(new Set([EP.kyc.status, EP.kyc.bvnStart, EP.kyc.bvnConfirm]));
    const menu = JSON.stringify(tree.toJSON());
    expect(menu).toContain('BVN verification');
    expect(menu).toContain('NIN verification');
    expect(menu).toContain('Face verification');
    expect(tree.root.findAllByType(Text).filter((node) => node.props.children === 'Verified')).toHaveLength(3);
    expect(menu).not.toContain('12345678901');
  } finally {
    act(() => tree.unmount());
  }
});

it.each([
  { name: 'signed out', token: null, locked: false },
  { name: 'locked', token: 'test-token', locked: true },
])('does not mount identity forms or fetch KYC while $name', async ({ token, locked }) => {
  mockGetToken.mockResolvedValue(token);
  mockSessionLocked.mockResolvedValue(locked);
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  try {
    expect(mockRedirect).toHaveBeenCalledWith('/signin');
    expect(mockApiJson).not.toHaveBeenCalled();
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
  } finally {
    act(() => tree.unmount());
  }
});


it('rejects retired provider status without advertising a per-transaction limit', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, tier: 1, transaction_limit: '50000',
    bvn_verified: true, email_verified: true, account_provider: 'wema_vas' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Your account needs review');
  expect(output).not.toContain('Per-transaction limit');
  expect(output).not.toContain('50000');
  await act(async () => tree.unmount());
});

it('prioritizes the account review hold over stale verification processing', async () => {
  mockApiJson.mockResolvedValue({ ...state, legacy_funding_review: true, pending: true,
    account_setup_state: 'processing', migration_message: 'Your account needs a balance review.' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  expect(JSON.stringify(tree.toJSON())).toContain('Your account needs a balance review.');
  expect(JSON.stringify(tree.toJSON())).not.toContain('Verification processing');
  expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
  act(() => tree.unmount());
});

it('offers BVN and NIN as alternatives for Tier 1 without requiring BVN first', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, email_verified: true, account_provider: 'partnership', identity_face_available: true });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('BVN verification');
  expect(output).toContain('NIN verification');
  expect(output).toContain('Tier 1: choose BVN or NIN');
  expect(output).not.toContain('Tier 2: NIN');
  act(() => tree.unmount());
});

it.each(['bvn', 'nin'])('offers direct %s face verification without first sending an SMS code', async (kind) => {
  mockParams.verify_identity = kind;
  mockApiJson.mockImplementation(async (path: string) => path === EP.kyc.status
    ? { ...state, email_verified: true, account_provider: 'partnership', identity_face_available: true }
    : { success: true, pending: true, message: 'Bank verification processing.' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  act(() => tree.root.findByType(TextInput).props.onChangeText('12345678901'));
  const face = tree.root.findByProps({ accessibilityLabel: 'Use face verification instead' });
  expect(face.props.disabled).toBe(false);
  await act(async () => { await face.props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.identityFaceStart, { [kind]: '12345678901', prefer_face: true });
  expect(mockApiJson.mock.calls.map(([path]) => path)).not.toContain(kind === 'bvn' ? EP.kyc.bvnStart : EP.kyc.nin);
  expect(JSON.stringify(tree.toJSON())).toContain('Verification processing');
  expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
  act(() => tree.unmount());
});

it.each(['bvn', 'nin'])('shows Tier 2 as the next step after a single verified %s', async (kind) => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, [`${kind}_verified`]: true, tier: 1,
    email_verified: true, account_provider: 'partnership', bank_upgrade_required: true });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Tier 2 · Face verification');
  expect(output).not.toContain(kind === 'bvn' ? 'NIN verification' : 'BVN verification');
  act(() => tree.unmount());
});

it('retries a failed status read instead of inventing a verification step', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ ...state, email_verified: true });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  expect(JSON.stringify(tree.toJSON())).toContain('We could not load your verification status');
  expect(JSON.stringify(tree.toJSON())).not.toContain('Verify email');
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Try again' }).props.onPress(); });
  expect(JSON.stringify(tree.toJSON())).toContain('BVN verification');
  act(() => tree.unmount());
});

it('does not collect Tier 2 identity details or a selfie when live verification is unavailable', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, bvn_verified: true, tier: 1, email_verified: true,
    account_provider: 'partnership', bank_upgrade_required: true, tier2_face_available: false,
    tier2_unavailable_reason: 'Please contact support while secure face verification is being enabled.' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Tier 2 verification is temporarily unavailable');
  expect(output).toContain('Please contact support while secure face verification is being enabled.');
  expect(output).not.toContain('Confirm your identity details and take a selfie');
  expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
  expect(tree.root.findByProps({ accessibilityLabel: 'Contact support' })).toBeTruthy();
  act(() => tree.unmount());
});

it('shows a sent address as Tier 3 processing, with Check again, never the form', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, bvn_verified: true, nin_verified: true, face_verified: true,
    tier: 2, email_verified: true, account_provider: 'partnership', address_rail: 'wema',
    address_verification_pending: true, address_verification_state: 'pending', tier3_state: 'processing',
    tier3_address_available: false, tier3_address_unavailable_reason: 'Contact support for address verification review.' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Tier 3 is processing');
  expect(output).toContain('Your address is with the bank. Tier 3 opens once the bank’s check can be confirmed. You don’t need to send it again.');
  // never sent to Support, never asked for the address again
  expect(output).not.toMatch(/Contact support|needs review|temporarily unavailable/);
  expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Check again' }).props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledTimes(2);
  act(() => tree.unmount());
});

it('sends Tier 3 to its own page whatever the old address flag says, never to Support', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, bvn_verified: true, nin_verified: true, face_verified: true,
    tier: 2, email_verified: true, account_provider: 'partnership', address_rail: 'wema', tier3_state: 'not_confirmed',
    tier3_address_available: false, tier3_address_unavailable_reason: 'Contact support to complete your address review.' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).not.toMatch(/temporarily unavailable|Contact support/);
  expect(output).not.toContain('Building number');
  expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Home address, Tier 3' }).props.onPress(); });
  expect(mockPush).toHaveBeenCalledWith('/tier3');
  act(() => tree.unmount());
});

it('shows Tier 2 done only once the account is on Tier 2, and a bank-confirmed check as ready to move up', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, bvn_verified: true, nin_verified: true, face_verified: true,
    tier: 1, email_verified: true, account_provider: 'partnership', tier2_state: 'ready' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  const card = tree.root.findByProps({ accessibilityLabel: 'Face verification, Confirmed by your bank · move up to Tier 2' });
  await act(async () => { card.props.onPress(); });
  expect(mockPush).toHaveBeenCalledWith('/tier2');
  act(() => tree.unmount());
});

it('shows an address verified on Tier 3 as done', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, bvn_verified: true, nin_verified: true, face_verified: true,
    tier: 3, email_verified: true, account_provider: 'partnership', address_rail: 'wema', tier3_state: 'done',
    address_verified: true, address_verification_state: 'verified' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  expect(tree.root.findByProps({ accessibilityLabel: 'Home address, Verified' })).toBeTruthy();
  expect(tree.root.findByProps({ accessibilityLabel: 'Face verification, Verified' })).toBeTruthy();
  act(() => tree.unmount());
});


it('does not resume an abandoned identity flow when its challenge arrives late', async () => {
  let finish!: (value: unknown) => void;
  mockApiJson.mockImplementation((path: string) => path === EP.kyc.status
    ? Promise.resolve(state)
    : new Promise(resolve => { finish = resolve; }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { tree.root.findByType(TextInput).props.onChangeText('12345678901'); });
  let sending!: Promise<void>;
  act(() => { sending = tree.root.findByProps({ accessibilityLabel: 'Send verification code' }).props.onPress(); });
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Go back' }).props.onPress(); });
  await act(async () => {
    finish({ success: true, otp_required: true, identity_verification_provider: 'prembly', delivery: 'old destination' });
    await sending;
  });
  expect(JSON.stringify(tree.toJSON())).not.toContain('Confirm your BVN');
  expect(JSON.stringify(tree.toJSON())).not.toContain('old destination');
  expect(mockNotify).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it('rejects a face capture from a session that no longer owns the KYC screen', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, tier: 1, email_verified: true, bvn_verified: true,
    bank_upgrade_required: true, tier2_face_available: true });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Tier 2 · Face verification, Your BVN and NIN, then a live face check' }).props.onPress(); });
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Take the live face check' }).props.onPress(); });
  const capture = tree.root.findByProps({ testID: 'face-capture' });
  mockGeneration += 1;
  act(() => { capture.props.onCapture({ image: 'old-customer-face', token: 'old-proof' }); });
  expect(JSON.stringify(tree.toJSON())).not.toContain('Face check passed');
  expect(mockApiJson).toHaveBeenCalledTimes(1);
  act(() => tree.unmount());
});


it('releases an abandoned KYC action on route blur so the preserved form can be used again', async () => {
  let finish!: (value: unknown) => void;
  mockApiJson.mockImplementation((path: string) => path === EP.kyc.status
    ? Promise.resolve(state)
    : new Promise(resolve => { finish = resolve; }));
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { tree.root.findByType(TextInput).props.onChangeText('12345678901'); });
  let sending!: Promise<void>;
  act(() => { sending = tree.root.findByProps({ accessibilityLabel: 'Send verification code' }).props.onPress(); });
  expect(tree.root.findByProps({ accessibilityLabel: 'Sending code…' }).props.disabled).toBe(true);
  act(() => { mockBlur?.(); });
  expect(tree.root.findByProps({ accessibilityLabel: 'Send verification code' }).props.disabled).toBe(false);
  await act(async () => {
    finish({ success: true, otp_required: true, identity_verification_provider: 'prembly', delivery: 'abandoned destination' });
    await sending;
  });
  expect(JSON.stringify(tree.toJSON())).not.toContain('abandoned destination');
  expect(mockNotify).not.toHaveBeenCalled();
  act(() => tree.unmount());
});

it.each([undefined, '0', '50000.00'])('leaves the per-transaction limit off the tier card (%j)', async (limit) => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, transaction_limit: limit });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Current tier');
  expect(output).not.toContain('Per-transaction limit');
  expect(output).not.toContain('Not confirmed');
  act(() => tree.unmount());
});


it('Tier 2 on the bank rail sends BVN, NIN and the checked photo with its proof together', async () => {
  mockParams.verify_identity = '';
  const verified = { ...state, bvn_verified: true, email_verified: true, phone_verified: true,
    tier: 1, bank_upgrade_required: true, tier2_face_rail: 'wema', tier2_face_flow: 'wema_selfie',
    tier2_face_available: true };
  let upgraded = false;
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return upgraded ? { ...verified, nin_verified: true, face_verified: true, tier: 2, bank_upgrade_required: false } : verified;
    if (path === EP.wallet.wemaUpgradeTier2) { upgraded = true; return { success: true, bvn_verified: true, nin_verified: true, face_verified: true, tier: 2 }; }
    throw new Error(`Unexpected API call: ${path}`);
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Tier 2 · Face verification, Your BVN and NIN, then a live face check' }).props.onPress(); });
  // Wema's Tier 2 takes BVN and NIN with the photo; no third-party face check.
  const inputs = tree.root.findAllByType(TextInput);
  expect(inputs).toHaveLength(2);
  expect(JSON.stringify(tree.toJSON())).not.toContain('Prembly');
  act(() => { inputs[0].props.onChangeText('22222222222'); });
  act(() => { inputs[1].props.onChangeText('33333333333'); });
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Take the live face check' }).props.onPress(); });
  // Passing the face check submits straight away with the numbers typed.
  await act(async () => { tree.root.findByProps({ testID: 'face-capture' }).props.onCapture({ image: 'SELFIE64', token: 'PROOF' }); });
  expect(tree.root.findAllByProps({ testID: 'face-capture' })).toHaveLength(0);
  const upgrades = mockApiJson.mock.calls.filter(([path]) => path === EP.wallet.wemaUpgradeTier2);
  expect(upgrades).toEqual([[EP.wallet.wemaUpgradeTier2,
    { bvn: '22222222222', nin: '33333333333', live_image: 'SELFIE64', liveness_token: 'PROOF' }]]);
  expect(mockNotify).toHaveBeenCalledWith('Identity verified — tier upgraded', undefined, 'success');
  await act(async () => tree.unmount());
});

it('holds a passed face check until the BVN and NIN are complete, then Finish sends it with its proof', async () => {
  mockParams.verify_identity = '';
  const verified = { ...state, bvn_verified: true, email_verified: true, phone_verified: true,
    tier: 1, bank_upgrade_required: true, tier2_face_rail: 'wema', tier2_face_flow: 'wema_selfie',
    tier2_face_available: true };
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return verified;
    if (path === EP.wallet.wemaUpgradeTier2) return { success: true, pending: true, message: 'Your bank is checking your selfie.' };
    throw new Error(`Unexpected API call: ${path}`);
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Tier 2 · Face verification, Your BVN and NIN, then a live face check' }).props.onPress(); });
  const inputs = tree.root.findAllByType(TextInput);
  act(() => { inputs[0].props.onChangeText('22222222222'); });
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Take the live face check' }).props.onPress(); });
  await act(async () => { tree.root.findByProps({ testID: 'face-capture' }).props.onCapture({ image: 'SELFIE64', token: 'PROOF' }); });
  expect(mockApiJson.mock.calls.map(([path]) => path)).not.toContain(EP.wallet.wemaUpgradeTier2);
  expect(JSON.stringify(tree.toJSON())).toContain('Face check passed');
  act(() => { tree.root.findAllByType(TextInput)[1].props.onChangeText('33333333333'); });
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Finish verification' }).props.onPress(); });
  expect(mockApiJson).toHaveBeenCalledWith(EP.wallet.wemaUpgradeTier2,
    { bvn: '22222222222', nin: '33333333333', live_image: 'SELFIE64', liveness_token: 'PROOF' });
  await act(async () => tree.unmount());
});

it('sends a customer with no bank account yet to the Tier 2 page without using the bank upgrade', async () => {
  mockParams.verify_identity = '';
  const verified = { ...state, bvn_verified: true, email_verified: true, phone_verified: true,
    tier: 1, bank_upgrade_required: false, has_wema_account: false, tier2_face_available: true };
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return verified;
    throw new Error(`Unexpected API call: ${path}`);
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  expect(JSON.stringify(tree.toJSON())).not.toContain('Prembly');
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Face verification, Tier 2' }).props.onPress(); });
  expect(mockPush).toHaveBeenCalledWith('/tier2');
  expect(mockApiJson.mock.calls.map(([path]) => path)).not.toContain(EP.wallet.wemaUpgradeTier2);
  await act(async () => tree.unmount());
});


it('a refused face check must be taken again, but a wrong NIN keeps it', async () => {
  mockParams.verify_identity = '';
  const verified = { ...state, bvn_verified: true, email_verified: true, phone_verified: true,
    tier: 1, bank_upgrade_required: true, tier2_face_rail: 'wema', tier2_face_flow: 'wema_selfie',
    tier2_face_available: true };
  const answers = [
    { success: false, message: 'That NIN does not match the NIN already verified on this account.', _httpStatus: 409 },
    { success: false, code: 'liveness_expired', message: 'Your face check has expired. Please take it again.', _httpStatus: 422 },
  ];
  mockApiJson.mockImplementation(async (path: string) => {
    if (path === EP.kyc.status) return verified;
    if (path === EP.wallet.wemaUpgradeTier2) return answers.shift();
    throw new Error(`Unexpected API call: ${path}`);
  });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  await act(async () => { tree.root.findByProps({ accessibilityLabel: 'Tier 2 · Face verification, Your BVN and NIN, then a live face check' }).props.onPress(); });
  const inputs = tree.root.findAllByType(TextInput);
  act(() => { inputs[0].props.onChangeText('22222222222'); });
  act(() => { inputs[1].props.onChangeText('44444444444'); });
  act(() => { tree.root.findByProps({ accessibilityLabel: 'Take the live face check' }).props.onPress(); });
  // The capture is sent straight away; the wrong NIN keeps the checked photo.
  await act(async () => { tree.root.findByProps({ testID: 'face-capture' }).props.onCapture({ image: 'SELFIE64', token: 'PROOF' }); });
  expect(JSON.stringify(tree.toJSON())).toContain('Face check passed');
  await act(async () => { await tree.root.findByProps({ accessibilityLabel: 'Finish verification' }).props.onPress(); });
  expect(JSON.stringify(tree.toJSON())).not.toContain('Face check passed');
  expect(tree.root.findByProps({ accessibilityLabel: 'Finish verification' }).props.disabled).toBe(true);
  await act(async () => tree.unmount());
});
