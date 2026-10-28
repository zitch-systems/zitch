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
const mockProgress = jest.fn();
const mockUpgrade = jest.fn();
const mockAddress = jest.fn();
const mockCameraPermission = jest.fn();
const mockCamera = jest.fn();
const mockPick = jest.fn();
const mockRead = jest.fn();
jest.mock('@/components/AuthGuard', () => ({ children }: any) => mockAuthenticated ? children : null);
jest.mock('expo-router', () => ({ useFocusEffect: (callback: () => (() => void)) => {
  const ReactActual = jest.requireActual('react');
  ReactActual.useEffect(() => { mockFocus = callback; mockBlur = callback(); return () => mockBlur(); }, [callback]);
} }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockGeneration }));
jest.mock('@/lib/session', () => ({ beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));
jest.mock('@/lib/services/banking', () => ({ banking: {
  status: () => mockStatus(), identity: (...args: any[]) => mockIdentity(...args),
  confirm: (...args: any[]) => mockConfirm(...args), progress: (...args: any[]) => mockProgress(...args),
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
  return { Screen: ({ children }: any) => ReactActual.createElement(View, null, children), Header: () => null,
    Field: ({ label, ...props }: any) => ReactActual.createElement(TextInput, { accessibilityLabel: label, ...props }),
    Btn: ({ label, ...props }: any) => ReactActual.createElement(Pressable, { accessibilityLabel: label, ...props }, ReactActual.createElement(Text, null, label)) };
});

const profile = { otherNames: 'Ada', lastName: 'Eze', phoneNo: '08011112222', email: 'ada@example.test', dateOfBirth: '01/01/1990', address: '1 Test Road', gender: 1 };
const ready = { success: true, provider: '9psb', identity_available: true, bank_tier: 1, profile, bvn_verified: false, nin_verified: false, address_verification_available: false };
const pending = { success: false, pending: true, tracking_id: 'bound-bank-ref', otp_required: true, account_setup_state: 'pending' };
let tree: renderer.ReactTestRenderer;
const control = (label: string) => tree.root.findByProps({ accessibilityLabel: label });
const field = (label: string) => tree.root.findAllByProps({ accessibilityLabel: label }).find((node) => node.props.onChangeText)!;
const output = () => JSON.stringify(tree.toJSON());
async function mount() { await act(async () => { tree = renderer.create(<Kyc />); }); }
async function enter(kind = 'bvn') {
  if (kind === 'nin') await act(async () => { tree.root.findAllByProps({ accessibilityLabel: 'NIN' }).find((node) => node.props.onPress)!.props.onPress(); });
  await act(async () => { field(kind.toUpperCase()).props.onChangeText('12345678901'); });
}
beforeEach(() => {
  jest.clearAllMocks(); mockGeneration = 1; mockAuthenticated = true;
  mockStatus.mockReset().mockResolvedValue(ready); mockIdentity.mockReset().mockResolvedValue(pending);
  mockConfirm.mockReset().mockResolvedValue({ ...pending, otp_required: false, account_setup_state: 'opening' });
  mockProgress.mockReset().mockResolvedValue(pending); mockUpgrade.mockReset().mockResolvedValue({ pending: true });
  mockCameraPermission.mockReset().mockResolvedValue({ granted: true });
  mockCamera.mockReset().mockResolvedValue({ canceled: false, assets: [{ base64: 'face-image' }] });
  mockPick.mockReset().mockResolvedValue({ canceled: true }); mockRead.mockReset().mockResolvedValue('encoded-document');
});
afterEach(async () => { if (tree) await act(async () => tree.unmount()); });

it.each(['bvn', 'nin'])('uses a bank-bound %s OTP and keeps account opening pending after acceptance', async (kind) => {
  await mount(); await enter(kind);
  await act(async () => { await control('Verify with bank OTP').props.onPress(); });
  expect(mockIdentity).toHaveBeenCalledWith(kind, '12345678901', profile);
  await act(async () => { field('Six-digit bank OTP').props.onChangeText('123456'); });
  await act(async () => { await control('Confirm bank OTP').props.onPress(); });
  expect(mockConfirm).toHaveBeenCalledWith('bound-bank-ref', '123456');
  expect(output()).toContain('Check verification status');
  expect(output()).not.toContain('Verify with bank OTP');
});

it.each(['bvn', 'nin'])('offers direct bank facial verification for %s without sending OTP first', async (kind) => {
  await mount(); await enter(kind);
  await act(async () => { await control('Verify with a face photo').props.onPress(); });
  expect(mockIdentity).toHaveBeenCalledTimes(1);
  expect(mockIdentity).toHaveBeenCalledWith(kind, '12345678901', profile, 'face-image');
  expect(mockConfirm).not.toHaveBeenCalled();
});

it('resumes the authenticated customer challenge without storing the identifier locally', async () => {
  mockStatus.mockResolvedValue({ ...ready, identity_challenge: { ...pending, kind: 'nin' } }); await mount();
  expect(output()).toContain('Six-digit bank OTP');
  expect(field('NIN').props.value).toBe('');
  expect(mockIdentity).not.toHaveBeenCalled();
});

it.each(['expired', 'failed'])('lets the customer start again after a terminal %s challenge', async (state) => {
  mockStatus.mockResolvedValue({ ...ready, identity_challenge: { ...pending, kind: 'bvn' } });
  mockProgress.mockResolvedValue({ success: false, account_setup_state: state }); await mount();
  await act(async () => { await control('Check verification status').props.onPress(); });
  expect(output()).toContain('Verify with bank OTP'); expect(output()).not.toContain('Six-digit bank OTP');
});

it('does not fetch or mount identity collection while signed out', async () => {
  mockAuthenticated = false; await mount(); expect(mockStatus).not.toHaveBeenCalled(); expect(mockIdentity).not.toHaveBeenCalled();
});

it('does not collect identities when the server disables verification', async () => {
  mockStatus.mockResolvedValue({ ...ready, identity_available: false }); await mount();
  expect(output()).not.toContain('Verify with bank OTP'); expect(output()).not.toContain('Residential address');
});

it('offers retry after an unsuccessful status read without inventing a verification step', async () => {
  mockStatus.mockRejectedValueOnce(new Error('offline')); await mount();
  expect(output()).toContain('Could not load verification status');
  expect(output()).not.toContain('Verify with bank OTP');
  await act(async () => { await control('Refresh verification status').props.onPress(); });
  expect(output()).toContain('Verify with bank OTP');
});

it('discards a challenge response from a replaced session', async () => {
  let finish!: (value: unknown) => void;
  mockIdentity.mockReturnValue(new Promise((resolve) => { finish = resolve; })); await mount(); await enter();
  await act(async () => { control('Verify with bank OTP').props.onPress(); });
  mockGeneration += 1;
  await act(async () => { finish(pending); });
  expect(output()).not.toContain('Six-digit bank OTP');
});

it('discards a delayed camera capture after route blur and refocus', async () => {
  let finish!: (value: unknown) => void;
  mockCamera.mockReturnValue(new Promise((resolve) => { finish = resolve; })); await mount(); await enter();
  await act(async () => { control('Verify with a face photo').props.onPress(); });
  await act(async () => { mockBlur(); mockBlur = mockFocus(); });
  await act(async () => { finish({ canceled: false, assets: [{ base64: 'old-face' }] }); });
  expect(mockIdentity).not.toHaveBeenCalled(); expect(field('BVN').props.value).toBe('');
});

it('prevents simultaneous camera verification requests', async () => {
  await mount(); await enter(); const launch = control('Verify with a face photo').props.onPress;
  await act(async () => { launch(); launch(); });
  expect(mockCamera).toHaveBeenCalledTimes(1); expect(mockIdentity).toHaveBeenCalledTimes(1);
});

it('accepts PDFs only for utility bills and keeps identity/signature uploads image-only', async () => {
  await mount(); await act(async () => { control('Tier upgrade').props.onPress(); });
  await act(async () => { await control('Upload ID front').props.onPress(); });
  expect(mockPick).toHaveBeenLastCalledWith(expect.objectContaining({ type: ['image/jpeg', 'image/png'] }));
  await act(async () => { await control('Upload Utility bill').props.onPress(); });
  expect(mockPick).toHaveBeenLastCalledWith(expect.objectContaining({ type: ['application/pdf', 'image/jpeg', 'image/png'] }));
});

it('discards uploaded documents when their session changes during file reading', async () => {
  let finish!: (value: string) => void;
  mockPick.mockResolvedValue({ canceled: false, assets: [{ uri: 'local-document', size: 100 }] });
  mockRead.mockReturnValue(new Promise((resolve) => { finish = resolve; })); await mount();
  await act(async () => { control('Tier upgrade').props.onPress(); });
  await act(async () => { control('Upload ID front').props.onPress(); });
  mockGeneration += 1;
  await act(async () => { finish('old-account-document'); });
  expect(output()).not.toContain('Attached: ID front');
});

it('does not collect a new residential address or offer submission while verification is unavailable', async () => {
  await mount(); await act(async () => { control('Tier 3 address').props.onPress(); });
  expect(output()).toContain('Address verification is temporarily unavailable');
  expect(output()).not.toContain('Full residential address');
  expect(control('Request address verification').props.disabled).toBe(true);
  expect(mockAddress).not.toHaveBeenCalled();
});

it('discards raw uploaded documents after the bank accepts an upgrade for review', async () => {
  mockPick.mockResolvedValue({ canceled: false, assets: [{ uri: 'local-document', size: 100 }] }); await mount();
  await act(async () => { control('Tier upgrade').props.onPress(); });
  await act(async () => { await control('Upload ID front').props.onPress(); });
  expect(output()).toContain('Attached: ID front');
  await act(async () => { await control('Submit for bank review').props.onPress(); });
  expect(mockUpgrade).toHaveBeenCalledTimes(1); expect(output()).not.toContain('Attached: ID front');
});
