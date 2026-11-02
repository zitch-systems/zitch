import React from 'react';
import renderer, { act } from 'react-test-renderer';
import Kyc from '../kyc';

let mockGeneration = 1;
let mockAuthenticated = true;
let mockFocus!: () => (() => void);
let mockBlur!: () => void;
const mockStatus = jest.fn();
const mockIdentity = jest.fn();
const mockConfirm = jest.fn();
const mockResend = jest.fn();
const mockProgress = jest.fn();
const mockUpgrade = jest.fn();
const mockAddress = jest.fn();
const mockCameraPermission = jest.fn();
const mockCamera = jest.fn();
const mockPick = jest.fn();
const mockRead = jest.fn();
const mockStartEmail = jest.fn();
const mockConfirmEmail = jest.fn();
jest.mock('@/lib/services/kyc', () => ({ kycService: {
  startEmail: (...args: any[]) => mockStartEmail(...args),
  confirmEmail: (...args: any[]) => mockConfirmEmail(...args),
} }));
jest.mock('@/components/AuthGuard', () => ({ children }: any) => mockAuthenticated ? children : null);
jest.mock('expo-router', () => ({ router: { push: jest.fn(), replace: jest.fn(), back: jest.fn() }, useFocusEffect: (callback: () => (() => void)) => {
  const ReactActual = jest.requireActual('react');
  ReactActual.useEffect(() => { mockFocus = callback; mockBlur = callback(); return () => mockBlur(); }, [callback]);
} }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockGeneration }));
jest.mock('@/lib/session', () => ({ beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));
jest.mock('@/lib/services/banking', () => ({ banking: {
  status: () => mockStatus(), identity: (...args: any[]) => mockIdentity(...args),
  confirm: (...args: any[]) => mockConfirm(...args), progress: (...args: any[]) => mockProgress(...args),
  resend: (...args: any[]) => mockResend(...args),
  upgrade: (...args: any[]) => mockUpgrade(...args), address: (...args: any[]) => mockAddress(...args), addressStatus: jest.fn(),
} }));
jest.mock('expo-image-picker', () => ({ CameraType: { front: 'front' }, requestCameraPermissionsAsync: () => mockCameraPermission(), launchCameraAsync: () => mockCamera() }));
jest.mock('expo-document-picker', () => ({ getDocumentAsync: (...args: any[]) => mockPick(...args) }));
jest.mock('expo-file-system', () => ({ EncodingType: { Base64: 'base64' }, readAsStringAsync: (...args: any[]) => mockRead(...args) }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: { ink1: '#111', brand: '#099' } }), font: { regular: 'regular' } }));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual('react');
  const { View, Pressable, Text, TextInput } = jest.requireActual('react-native');
  return { Screen: ({ children, header }: any) => ReactActual.createElement(View, null, header, children),
    Header: ({ title, onBack }: any) => ReactActual.createElement(View, null, ReactActual.createElement(Text, null, title),
      onBack ? ReactActual.createElement(Pressable, { accessibilityLabel: 'Go back', onPress: onBack }) : null),
    Card: ({ children }: any) => ReactActual.createElement(View, null, children),
    Field: ({ label, ...props }: any) => ReactActual.createElement(TextInput, { accessibilityLabel: label, ...props }),
    Btn: ({ label, ...props }: any) => ReactActual.createElement(Pressable, { accessibilityLabel: label, ...props }, ReactActual.createElement(Text, null, label)),
    PillTabs: ({ options, value, onChange }: any) => ReactActual.createElement(View, null, options.map((o: any) => ReactActual.createElement(Pressable,
      { key: o.v, accessibilityLabel: o.label, accessibilityState: { selected: value === o.v }, onPress: () => onChange(o.v) }, ReactActual.createElement(Text, null, o.label)))) };
});

const profile = { otherNames: 'Ada', lastName: 'Eze', phoneNo: '08011112222', email: 'ada@example.test', dateOfBirth: '01/01/1990', address: '1 Test Road', gender: 1 };
const ready = { success: true, provider: '9psb', identity_available: true, bank_tier: 1, profile, bvn_verified: false, nin_verified: false, address_verification_available: false };
const pending = { success: false, pending: true, tracking_id: 'bound-bank-ref', otp_required: true, account_setup_state: 'pending' };
let tree: renderer.ReactTestRenderer;
const control = (label: string) => tree.root.findByProps({ accessibilityLabel: label });
const field = (label: string) => tree.root.findAllByProps({ accessibilityLabel: label }).find((node) => node.props.onChangeText)!;
const output = () => JSON.stringify(tree.toJSON());
async function mount() { await act(async () => { tree = renderer.create(<Kyc />); }); }
const pill = (label: string) => tree.root.findAllByProps({ accessibilityLabel: label }).find((node) => node.props.onPress && !node.props.onChangeText)!;
async function next() { await act(async () => { control('Next').props.onPress(); }); }
async function enter(kind = 'bvn') {
  await next();
  await act(async () => { pill(kind.toUpperCase()).props.onPress(); });
  await act(async () => { field(kind.toUpperCase()).props.onChangeText('12345678901'); });
  if (kind === 'nin') await act(async () => { field('NIMC User ID').props.onChangeText('NIMC-123'); });
}
const sent = (kind: string) => (kind === 'nin' ? { ...profile, ninUserId: 'NIMC-123' } : profile);
async function openFaceStep() {
  await act(async () => { control('Verify with my face instead').props.onPress(); });
}
beforeEach(() => {
  jest.clearAllMocks(); mockGeneration = 1; mockAuthenticated = true;
  mockStatus.mockReset().mockResolvedValue(ready); mockIdentity.mockReset().mockResolvedValue(pending);
  mockConfirm.mockReset().mockResolvedValue({ ...pending, otp_required: false, account_setup_state: 'opening' });
  mockProgress.mockReset().mockResolvedValue(pending); mockUpgrade.mockReset().mockResolvedValue({ pending: true });
  mockCameraPermission.mockReset().mockResolvedValue({ granted: true });
  mockCamera.mockReset().mockResolvedValue({ canceled: false, assets: [{ base64: 'face-image' }] });
  mockPick.mockReset().mockResolvedValue({ canceled: true }); mockRead.mockReset().mockResolvedValue('encoded-document');
  mockStartEmail.mockReset().mockResolvedValue({ success: true, message: 'We sent an email code.' });
  mockConfirmEmail.mockReset().mockResolvedValue({ success: true });
});
afterEach(async () => { if (tree) await act(async () => tree.unmount()); });

it.each(['bvn', 'nin'])('uses a bank-bound %s OTP and keeps account opening pending after acceptance', async (kind) => {
  await mount(); await enter(kind);
  await act(async () => { await control('Send code').props.onPress(); });
  expect(mockIdentity).toHaveBeenCalledWith(kind, '12345678901', sent(kind));
  await act(async () => { field('Six-digit code').props.onChangeText('123456'); });
  await act(async () => { await control('Verify').props.onPress(); });
  expect(mockConfirm).toHaveBeenCalledWith('bound-bank-ref', '123456');
  expect(output()).toContain('Check status');
  expect(output()).not.toContain('Send code');
});

it('starts with BVN and shows the details first, with Next to one ID page', async () => {
  await mount();
  expect(output()).toContain('Your details'); expect(output()).not.toContain('Step 1 of 2');
  expect(control('Next').props.disabled).toBe(false);
  await next();
  expect(output()).toContain('Verify your identity'); expect(output()).not.toContain('Home address');
  expect(pill('BVN').props.accessibilityState.selected).toBe(true);
  expect(pill('NIN').props.accessibilityState.selected).toBe(false);
  expect(output()).not.toContain('NIMC User ID');
  // No tier tabs, status narration or test-mode notes on this page.
  for (const gone of ['Tier upgrade', 'Tier 3 address', 'Codes expire', 'Test mode', 'Account tier']) expect(output()).not.toContain(gone);
  await act(async () => { control('Go back').props.onPress(); });
  expect(output()).toContain('Home address');
});

it('sends a code, verifies it, and resends a new one after a minute', async () => {
  jest.useFakeTimers();
  try {
    await mount(); await enter('bvn');
    await act(async () => { await control('Send code').props.onPress(); });
    expect(output()).toContain('Six-digit code');
    expect(control('Resend code in 60s').props.disabled).toBe(true);
    await act(async () => { jest.advanceTimersByTime(61000); });
    mockIdentity.mockResolvedValue({ ...pending, tracking_id: 'second-ref' });
    await act(async () => { await control('Resend code').props.onPress(); });
    expect(mockIdentity).toHaveBeenCalledTimes(2);
    await act(async () => { field('Six-digit code').props.onChangeText('654321'); });
    await act(async () => { await control('Verify').props.onPress(); });
    expect(mockConfirm).toHaveBeenCalledWith('second-ref', '654321');
  } finally { jest.useRealTimers(); }
});

it('resends by reference when the app no longer holds the ID number', async () => {
  mockStatus.mockResolvedValue({ ...ready, identity_challenge: { ...pending, kind: 'bvn' } });
  mockResend.mockResolvedValue({ ...pending, tracking_id: 'new-ref' }); await mount();
  await act(async () => { await control('Resend code').props.onPress(); });
  expect(mockResend).toHaveBeenCalledWith('bound-bank-ref'); expect(mockIdentity).not.toHaveBeenCalled();
});

it('keeps Next disabled until the details are complete', async () => {
  mockStatus.mockResolvedValue({ ...ready, profile: { ...profile, address: '', gender: undefined } }); await mount();
  expect(control('Next').props.disabled).toBe(true);
  await act(async () => { field('Home address').props.onChangeText('2 Test Road'); });
  expect(control('Next').props.disabled).toBe(true);
  await act(async () => { pill('Female').props.onPress(); });
  expect(control('Next').props.disabled).toBe(false);
});

it.each(['bvn', 'nin'])('verifies %s on its own face verification screen without sending a code', async (kind) => {
  mockIdentity.mockResolvedValue({ ...pending, otp_required: false, account_setup_state: 'opening' });
  await mount(); await enter(kind); await openFaceStep();
  expect(output()).toContain('Open camera'); expect(output()).not.toContain('Send code');
  await act(async () => { await control('Open camera').props.onPress(); });
  expect(mockIdentity).not.toHaveBeenCalled();
  expect(output()).toContain('Retake photo');
  await act(async () => { await control('Verify my face').props.onPress(); });
  expect(mockIdentity).toHaveBeenCalledTimes(1);
  expect(mockIdentity).toHaveBeenCalledWith(kind, '12345678901', sent(kind), 'face-image');
  expect(mockConfirm).not.toHaveBeenCalled();
  expect(output()).toContain('Check status');
});

it('returns from the face verification screen without sending anything', async () => {
  await mount(); await enter('bvn'); await openFaceStep();
  await act(async () => { await control('Back').props.onPress(); });
  expect(output()).toContain('Verify with my face instead'); expect(mockCamera).not.toHaveBeenCalled(); expect(mockIdentity).not.toHaveBeenCalled();
});

it('masks the date of birth to DD/MM/YYYY digits and rejects impossible dates', async () => {
  await mount();
  await act(async () => { field('Date of birth').props.onChangeText('03a08b1994'); });
  expect(field('Date of birth').props.value).toBe('03/08/1994');
  expect(field('Date of birth').props.keyboardType).toBe('number-pad');
  await act(async () => { field('Date of birth').props.onChangeText('31021990'); });
  expect(field('Date of birth').props.error).toBe('Enter a real date of birth');
  expect(control('Next').props.disabled).toBe(true);
});

it('asks for the NIMC User ID only for NIN', async () => {
  await mount(); await enter('nin');
  await act(async () => { field('NIMC User ID').props.onChangeText(''); });
  expect(control('Send code').props.disabled).toBe(true);
  await act(async () => { pill('BVN').props.onPress(); });
  expect(output()).not.toContain('NIMC User ID');
  expect(output()).not.toContain('9PSB'); expect(output()).not.toContain('Prembly');
});

it('resumes the authenticated customer challenge without storing the identifier locally', async () => {
  mockStatus.mockResolvedValue({ ...ready, identity_challenge: { ...pending, kind: 'nin' } }); await mount();
  expect(output()).toContain('Six-digit code');
  expect(field('NIN').props.value).toBe('');
  expect(mockIdentity).not.toHaveBeenCalled();
});

it.each(['expired', 'failed'])('lets the customer start again after a terminal %s challenge', async (state) => {
  mockStatus.mockResolvedValue({ ...ready, identity_challenge: { ...pending, otp_required: false, account_setup_state: 'opening', kind: 'bvn' } });
  mockProgress.mockResolvedValue({ success: false, account_setup_state: state }); await mount();
  await act(async () => { await control('Check status').props.onPress(); });
  expect(output()).toContain('Send code'); expect(output()).not.toContain('Six-digit code');
});

it('does not fetch or mount identity collection while signed out', async () => {
  mockAuthenticated = false; await mount(); expect(mockStatus).not.toHaveBeenCalled(); expect(mockIdentity).not.toHaveBeenCalled();
});

it('does not collect identities when the server disables verification', async () => {
  mockStatus.mockResolvedValue({ ...ready, identity_available: false }); await mount();
  expect(output()).not.toContain('Send code'); expect(output()).not.toContain('Home address');
});

it('completes email verification before collecting bank identity', async () => {
  mockStatus.mockResolvedValueOnce({ ...ready, email_verified: false, identity_available: false });
  await mount();
  expect(field('Email address').props.value).toBe(profile.email);
  expect(output()).not.toContain('Send code');
  await act(async () => { await control('Send email code').props.onPress(); });
  expect(mockStartEmail).toHaveBeenCalledWith(profile.email);
  await act(async () => { field('Six-digit email code').props.onChangeText('654321'); });
  await act(async () => { await control('Confirm email code').props.onPress(); });
  expect(mockConfirmEmail).toHaveBeenCalledWith('654321');
  expect(output()).toContain('Your details'); expect(output()).toContain('Next');
  expect(output()).not.toContain('Six-digit email code');
  expect(mockIdentity).not.toHaveBeenCalled();
});

it('keeps an incorrect email code in the email step without granting bank access', async () => {
  mockStatus.mockResolvedValue({ ...ready, email_verified: false, identity_available: false });
  mockConfirmEmail.mockResolvedValue({ success: false, message: 'Invalid code' });
  await mount();
  await act(async () => { await control('Send email code').props.onPress(); });
  await act(async () => { field('Six-digit email code').props.onChangeText('000000'); });
  await act(async () => { await control('Confirm email code').props.onPress(); });
  expect(output()).toContain('Invalid code');
  expect(output()).not.toContain('Send code');
  expect(mockStatus).toHaveBeenCalledTimes(1);
});

it('does not advance after failed email delivery and prevents duplicate sends', async () => {
  mockStatus.mockResolvedValue({ ...ready, email_verified: false, identity_available: false });
  let finish!: (value: unknown) => void;
  mockStartEmail.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  await mount();
  const send = control('Send email code').props.onPress;
  await act(async () => { send(); send(); });
  expect(mockStartEmail).toHaveBeenCalledTimes(1);
  await act(async () => { finish({ success: false, message: 'Email delivery unavailable' }); });
  expect(output()).toContain('Email delivery unavailable');
  expect(output()).not.toContain('Six-digit email code');
});

it('discards a delayed email response after the session changes', async () => {
  mockStatus.mockResolvedValue({ ...ready, email_verified: false, identity_available: false });
  let finish!: (value: unknown) => void;
  mockStartEmail.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  await mount();
  await act(async () => { control('Send email code').props.onPress(); });
  mockGeneration += 1;
  await act(async () => { finish({ success: true }); });
  expect(output()).not.toContain('Six-digit email code');
  expect(mockConfirmEmail).not.toHaveBeenCalled();
});

it('clears the email code when changing the address or leaving verification', async () => {
  mockStatus.mockResolvedValue({ ...ready, email_verified: false, identity_available: false });
  await mount();
  await act(async () => { await control('Send email code').props.onPress(); });
  await act(async () => { field('Six-digit email code').props.onChangeText('654321'); });
  await act(async () => { control('Change email address').props.onPress(); });
  expect(output()).not.toContain('654321');
  await act(async () => { field('Email address').props.onChangeText('new@example.test'); });
  await act(async () => { await control('Send email code').props.onPress(); });
  expect(mockStartEmail).toHaveBeenLastCalledWith('new@example.test');
  await act(async () => { field('Six-digit email code').props.onChangeText('123456'); });
  await act(async () => { mockBlur(); mockBlur = mockFocus(); });
  expect(output()).not.toContain('123456');
  expect(output()).not.toContain('Six-digit email code');
});

it('offers retry after an unsuccessful status read without inventing a verification step', async () => {
  mockStatus.mockRejectedValueOnce(new Error('offline')); await mount();
  expect(output()).toContain('Could not load verification status');
  expect(output()).not.toContain('Send code');
  await act(async () => { await control('Refresh verification status').props.onPress(); });
  expect(output()).toContain('Your details'); expect(output()).toContain('Next');
});

it('discards a challenge response from a replaced session', async () => {
  let finish!: (value: unknown) => void;
  mockIdentity.mockReturnValue(new Promise((resolve) => { finish = resolve; })); await mount(); await enter();
  await act(async () => { control('Send code').props.onPress(); });
  mockGeneration += 1;
  await act(async () => { finish(pending); });
  expect(output()).not.toContain('Six-digit code');
});

it('discards a delayed camera capture after route blur and refocus', async () => {
  let finish!: (value: unknown) => void;
  mockCamera.mockReturnValue(new Promise((resolve) => { finish = resolve; })); await mount(); await enter(); await openFaceStep();
  await act(async () => { control('Open camera').props.onPress(); });
  await act(async () => { mockBlur(); mockBlur = mockFocus(); });
  await act(async () => { finish({ canceled: false, assets: [{ base64: 'old-face' }] }); });
  expect(mockIdentity).not.toHaveBeenCalled(); expect(output()).not.toContain('old-face');
  expect(output()).toContain('Your details'); await next(); expect(field('BVN').props.value).toBe('');
});

it('prevents simultaneous camera verification requests', async () => {
  await mount(); await enter(); await openFaceStep(); const launch = control('Open camera').props.onPress;
  await act(async () => { launch(); launch(); });
  expect(mockCamera).toHaveBeenCalledTimes(1); expect(mockIdentity).not.toHaveBeenCalled();
  const confirm = control('Verify my face').props.onPress;
  await act(async () => { confirm(); confirm(); });
  expect(mockIdentity).toHaveBeenCalledTimes(1);
});
