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
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('@/lib/secureStore', () => ({ getToken: () => mockGetToken() }));
jest.mock('expo-router', () => ({
  router: { back: jest.fn() }, useLocalSearchParams: () => mockParams,
  Redirect: (props: { href: string }) => { mockRedirect(props.href); return null; },
  useFocusEffect: (callback: () => void) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    ReactActual.useEffect(callback, [callback]);
  },
}));
jest.mock('expo-image-picker', () => ({}));
jest.mock('expo-web-browser', () => ({}));
jest.mock('@/lib/session', () => ({
  beginExternalActivity: jest.fn(), endExternalActivity: jest.fn(),
  isSessionLocked: () => mockSessionLocked(),
}));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/FaceLivenessModal', () => () => null);
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
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
    Header: () => null, money: String,
    Tap: ({ children, ...props }: { children: ReactNode }) => ReactActual.createElement(Pressable, props, children),
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) => ReactActual.createElement(
      Pressable, { accessibilityLabel: label, onPress, disabled }, ReactActual.createElement(Text, null, label)),
    Field: ({ value, onChangeText }: { value: string; onChangeText: (value: string) => void }) => ReactActual.createElement(
      TextInput, { value, onChangeText }),
  };
});

const state = { success: true, tier: 0, transaction_limit: '0', bvn_verified: false, nin_verified: false,
  face_verified: false, identity_verification_provider: 'prembly' };

beforeEach(() => {
  mockApiJson.mockReset();
  mockParams.verify_identity = 'bvn';
  mockGetToken.mockReset().mockResolvedValue('test-token');
  mockSessionLocked.mockReset().mockResolvedValue(false);
  mockRedirect.mockClear();
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
  const resend = tree.root.findAll((node) => node.props.children === 'Resend code' && typeof node.props.onPress === 'function')[0];
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

it('preserves the server-required address step for legacy accounts', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, tier: 2, bvn_verified: true, nin_verified: true,
    face_verified: true, email_verified: true, account_provider: 'partnership',
    address_verification_required: true, address_rail: 'wema' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  expect(JSON.stringify(tree.toJSON())).toContain('Address verification');
  await act(async () => tree.unmount());
});

it('refreshes an already verified BVN on the same profile without resetting verified history', async () => {
  const verifiedState = { ...state, tier: 2, bvn_verified: true, nin_verified: true,
    face_verified: true, email_verified: true, account_provider: 'wema_vas' };
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
    expect(menu).toContain('Selfie verification');
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


it('keeps VAS identity status without advertising a per-transaction limit', async () => {
  mockParams.verify_identity = '';
  mockApiJson.mockResolvedValue({ ...state, tier: 1, transaction_limit: '50000',
    bvn_verified: true, email_verified: true, account_provider: 'wema_vas' });
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<Kyc />); });
  const output = JSON.stringify(tree.toJSON());
  expect(output).toContain('Current tier');
  expect(output).not.toContain('Per-transaction limit');
  expect(output).not.toContain('50000');
  await act(async () => tree.unmount());
});
