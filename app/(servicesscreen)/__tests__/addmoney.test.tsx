import React, { type ReactNode } from 'react';
import { TextInput } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import AddMoney from '@/app/(servicesscreen)/addmoney';

const mockApiJson = jest.fn();
const mockPush = jest.fn();

jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('expo-router', () => ({
  router: { back: jest.fn(), push: (...args: unknown[]) => mockPush(...args) },
}));
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn() }));
jest.mock('expo-web-browser', () => ({ openBrowserAsync: jest.fn() }));
jest.mock('@/lib/session', () => ({ beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: {
    brand: '#0FA295', ink1: '#111', ink2: '#222', ink3: '#333', lime: '#0f0', line: '#ddd', surface: '#fff',
  } }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/flowkit', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text } = jest.requireActual<typeof import('react-native')>('react-native');
  return { Label: ({ children }: { children: ReactNode }) => ReactActual.createElement(Text, null, children) };
});
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text, TextInput, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Header: () => null,
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) => ReactActual.createElement(
      Pressable,
      { accessibilityLabel: label, onPress, disabled },
      ReactActual.createElement(Text, null, label),
    ),
    Field: ({ value, onChangeText, secureTextEntry }: { value: string; onChangeText: (value: string) => void; secureTextEntry?: boolean }) => ReactActual.createElement(
      TextInput,
      { value, onChangeText, secureTextEntry },
    ),
  };
});

describe('AddMoney VAS migration', () => {
  beforeEach(() => {
    mockApiJson.mockReset();
    mockPush.mockReset();
  });

  const enrollment = {
    success: true, provider: 'wema_vas', has_account: false, available: false,
    enrollment_available: true, spending_available: false,
    account_setup_state: 'vas_enrollment_required', migration_message: 'Set up your new virtual account.',
  };

  it('requires explicit consent and sends the selected verified identity only to VAS', async () => {
    mockApiJson.mockResolvedValueOnce(enrollment).mockResolvedValueOnce({ success: true })
      .mockResolvedValueOnce({ ...enrollment, enrollment_available: false, account_setup_state: 'vas_validation' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(tree.root.findByType(TextInput).props.secureTextEntry).toBe(true);
    await act(async () => { findControl(tree, 'Use NIN').props.onPress(); });
    await act(async () => { tree.root.findByType(TextInput).props.onChangeText('11111111111'); });
    expect(findControl(tree, 'Set up virtual account').props.disabled).toBe(true);
    await act(async () => { await findControl(tree, 'Set up virtual account').props.onPress(); });
    expect(mockApiJson).toHaveBeenCalledTimes(1);
    await act(async () => { findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress(); });
    await act(async () => { await findControl(tree, 'Set up virtual account').props.onPress(); });
    expect(mockApiJson).toHaveBeenNthCalledWith(2, '/api/wallet/vas/enroll/', { nin: '11111111111', consent: true });
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).not.toContain('11111111111');
  });

  it('clears the re-entered identity when enrollment fails', async () => {
    mockApiJson.mockResolvedValueOnce(enrollment).mockResolvedValueOnce({ success: false, message: 'Identity could not be verified.' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    await act(async () => { tree.root.findByType(TextInput).props.onChangeText('22222222222'); });
    await act(async () => { findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress(); });
    await act(async () => { await findControl(tree, 'Set up virtual account').props.onPress(); });
    expect(tree.root.findByType(TextInput).props.value).toBe('');
    expect(findControl(tree, 'Set up virtual account').props.disabled).toBe(true);
  });

  it.each(['vas_validation', 'restricted'])('never exposes a %s account for funding', async (state) => {
    mockApiJson.mockResolvedValueOnce({ ...enrollment, account_setup_state: state,
      enrollment_available: false, has_account: true, account_number: '7111234567' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(JSON.stringify(tree.toJSON())).not.toContain('7111 234 567');
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
  });

  it('shows a live VAS account with the spending limitation before funding', async () => {
    mockApiJson.mockResolvedValueOnce({ ...enrollment, account_setup_state: 'ready',
      available: true, has_account: true, enrollment_available: false,
      account_number: '7121234567', account_name: 'Zitch/Ada', bank_name: 'Wema Bank' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(findControl(tree, 'Copy account number')).toBeTruthy();
    expect(JSON.stringify(tree.toJSON())).toContain('Transfers and bill payments are currently unavailable.');
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
  });
});

const findControl = (tree: renderer.ReactTestRenderer, label: string) =>
  tree.root.findByProps({ accessibilityLabel: label });

describe('AddMoney face fallback', () => {
  beforeEach(() => {
    mockApiJson.mockReset();
    mockPush.mockReset();
  });

  it('hands a server-selected NIN OTP attempt to KYC instead of BVN confirmation', async () => {
    mockApiJson
      .mockResolvedValueOnce({ success: false })
      .mockResolvedValueOnce({ success: true, otp_required: true, tracking_id: 'bvn-track' })
      .mockResolvedValueOnce({
        success: true,
        status: 'account_otp_pending',
        account_setup_state: 'otp_pending',
        tracking_id: 'nin-track',
        using_bvn: false,
        otp_destination_kind: 'nin',
        otp_destination: '••••1234',
        bvn_verified: true,
        nin_verified: false,
        face_verified: false,
        tier: 1,
        transaction_limit: '100000',
      });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    await act(async () => { await Promise.resolve(); });

    await act(async () => {
      tree.root.findByType(TextInput).props.onChangeText('22222222222');
    });
    await act(async () => { await findControl(tree, 'Get my account').props.onPress(); });
    await act(async () => { await findControl(tree, 'Use face verification instead').props.onPress(); });

    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/kyc',
      params: {
        pending_identity: 'nin',
        pending_tracking_id: 'nin-track',
        pending_otp_destination: '••••1234',
      },
    });
    expect(mockApiJson).toHaveBeenCalledTimes(3);
    expect(findControl(tree, 'Use face verification instead')).toBeTruthy();
  });

  it('does not offer new BVN provisioning when account lookup is offline', async () => {
    mockApiJson.mockResolvedValueOnce({ success: false, offline: true });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    await act(async () => { await Promise.resolve(); });

    expect(findControl(tree, 'Try again')).toBeTruthy();
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
  });
});
