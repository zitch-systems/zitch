import React, { type ReactNode } from 'react';
import { TextInput } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import AddMoney from '@/app/(servicesscreen)/addmoney';

let mockGeneration = 0;
const mockApiJson = jest.fn();
const mockPush = jest.fn();
let mockFocus: () => void | (() => void);
let mockBlur: (() => void) | void;

jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockGeneration }));
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('expo-router', () => ({
  router: { back: jest.fn(), push: (...args: unknown[]) => mockPush(...args) },
  useFocusEffect: (callback: () => void | (() => void)) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    mockFocus = callback;
    ReactActual.useEffect(() => {
      mockBlur = callback();
      return () => mockBlur?.();
    }, [callback]);
  },
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

describe('AddMoney Partnership funding', () => {
  beforeEach(() => {
    mockGeneration = 0;
    mockApiJson.mockReset();
    mockPush.mockReset();
  });

  it('shows the Partnership review without asking for another identity or exposing funding details', async () => {
    mockApiJson.mockResolvedValue({ success: true, provider: 'partnership',
      legacy_funding_review: true, account_setup_state: 'legacy_funding_review', available: false, has_account: false,
      migration_message: 'Your account needs a balance review.' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(JSON.stringify(tree.toJSON())).toContain('Account review in progress');
    expect(JSON.stringify(tree.toJSON())).toContain('balance review');
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).not.toContain('Get my account');
    await act(async () => tree.unmount());
  });

  it('explains the first Partnership setup for an already verified customer', async () => {
    mockApiJson.mockResolvedValue({ success: true, provider: 'partnership',
      partnership_setup_required: true, bvn_verified: true, has_account: false });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(JSON.stringify(tree.toJSON())).toContain('Your Zitch identity verification is saved.');
    expect(tree.root.findByType(TextInput)).toBeTruthy();
    await act(async () => tree.unmount());
  });

  it('resumes an existing Partnership OTP after reopening the screen', async () => {
    mockApiJson.mockResolvedValue({ success: true, provider: 'partnership',
      account_setup_state: 'otp_pending', otp_required: true, tracking_id: 'saved-bank-otp', has_account: false });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(JSON.stringify(tree.toJSON())).toContain('Confirm code');
    expect(JSON.stringify(tree.toJSON())).not.toContain('Get my account');
    expect(mockApiJson).toHaveBeenCalledTimes(1);
    await act(async () => tree.unmount());
  });

  it('does not request BVN or OTP again while Wema finishes account issuance', async () => {
    mockApiJson.mockResolvedValue({ success: true, provider: 'partnership',
      account_setup_state: 'processing', bvn_verified: true, otp_required: false, has_account: false });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(JSON.stringify(tree.toJSON())).toContain('Account setup is processing');
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    await act(async () => tree.unmount());
  });

  it('prioritizes a balance review over a stale processing state', async () => {
    mockApiJson.mockResolvedValue({ success: true, provider: 'partnership', legacy_funding_review: true,
      pending: true, account_setup_state: 'processing', migration_message: 'Contact support before adding money.' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(JSON.stringify(tree.toJSON())).toContain('Account review in progress');
    expect(JSON.stringify(tree.toJSON())).not.toContain('Account setup is processing');
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    act(() => tree.unmount());
  });

  it.each(['wema_vas', 'unknown'])('never offers a retired or unknown account for funding: %s', async (provider) => {
    mockApiJson.mockResolvedValue({ success: true, provider, can_fund: true,
      account_setup_state: 'ready', available: true, has_account: true, account_number: '7121234567' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(JSON.stringify(tree.toJSON())).toContain('Account review in progress');
    expect(JSON.stringify(tree.toJSON())).not.toContain('7121234567');
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
    act(() => tree.unmount());
  });
});

const findControl = (tree: renderer.ReactTestRenderer, label: string) =>
  tree.root.findByProps({ accessibilityLabel: label });

describe('AddMoney face fallback', () => {
  beforeEach(() => {
    mockGeneration = 0;
    mockApiJson.mockReset();
    mockPush.mockReset();
  });

  it('preserves a canonical balance review returned after face verification', async () => {
    mockApiJson.mockResolvedValueOnce({ success: true, provider: 'partnership', account_setup_state: 'identity_required' })
      .mockResolvedValueOnce({ success: true, url: 'https://example.com/face', session: 'face-session' })
      .mockResolvedValueOnce({ success: true, status: 'verified' })
      .mockResolvedValueOnce({ success: true, provider: 'partnership', legacy_funding_review: true,
        account_setup_state: 'legacy_funding_review', migration_message: 'Contact support before adding money.' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    act(() => tree.root.findByType(TextInput).props.onChangeText('12345678901'));
    await act(async () => { await findControl(tree, 'Use face verification instead').props.onPress(); });
    expect(JSON.stringify(tree.toJSON())).toContain('Account review in progress');
    expect(JSON.stringify(tree.toJSON())).not.toContain('Account setup is processing');
    expect(mockApiJson).toHaveBeenLastCalledWith('/api/wallet/account/');
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    act(() => tree.unmount());
  });

  it('starts a new Partnership account with NIN and offers face without first requesting SMS', async () => {
    mockApiJson.mockResolvedValueOnce({ success: true, provider: 'partnership', has_account: false, account_setup_state: 'identity_required' })
      .mockResolvedValueOnce({ success: true, otp_required: true, tracking_id: 'nin-otp', using_bvn: false, delivery: '••••4444' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    act(() => findControl(tree, 'Use NIN').props.onPress());
    act(() => tree.root.findByType(TextInput).props.onChangeText('12345678901'));
    expect(findControl(tree, 'Use face verification instead').props.disabled).toBe(false);
    await act(async () => { await findControl(tree, 'Get my account').props.onPress(); });
    expect(mockApiJson).toHaveBeenCalledWith('/api/wallet/account/create/', { nin: '12345678901' });
    expect(JSON.stringify(tree.toJSON())).toContain('••••4444');
    act(() => tree.unmount());
  });

  it('resumes a NIN challenge and stays on processing after OTP acceptance until the account is ready', async () => {
    mockApiJson.mockResolvedValueOnce({ success: true, provider: 'partnership', has_account: false, account_setup_state: 'otp_pending',
      tracking_id: 'saved-nin-otp', using_bvn: false, otp_destination_kind: 'nin', otp_destination: '••••4444' })
      .mockResolvedValueOnce({ success: true, pending: true, account_setup_state: 'processing' })
      .mockResolvedValueOnce({ success: true, provider: 'partnership', has_account: true, available: true, can_fund: true, account_setup_state: 'ready', account_number: '0454243073', account_name: 'Test Customer', bank_name: 'Wema Bank' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(JSON.stringify(tree.toJSON())).toContain('••••4444');
    act(() => tree.root.findByType(TextInput).props.onChangeText('123456'));
    await act(async () => { await findControl(tree, 'Confirm code').props.onPress(); });
    expect(mockApiJson).toHaveBeenCalledWith('/api/wallet/wema/verify-otp/', { tracking_id: 'saved-nin-otp', otp: '123456' });
    expect(JSON.stringify(tree.toJSON())).toContain('Account setup is processing');
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    await act(async () => { await findControl(tree, 'Check again').props.onPress(); });
    expect(JSON.stringify(tree.toJSON())).toContain('Fund by bank transfer');
    expect(JSON.stringify(tree.toJSON())).not.toContain('Account setup is processing');
    act(() => tree.unmount());
  });

  it('provides a working face route when a resumed OTP has no identity number in memory', async () => {
    mockApiJson.mockResolvedValue({ success: true, provider: 'partnership', account_setup_state: 'otp_pending', tracking_id: 'nin-otp', using_bvn: false });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    await act(async () => { await findControl(tree, 'Use face verification instead').props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/kyc', params: { verify_identity: 'nin' } });
    expect(mockApiJson).toHaveBeenCalledTimes(1);
    act(() => tree.unmount());
  });

  it('reconnects an already verified account without repeating BVN or NIN verification', async () => {
    mockApiJson.mockResolvedValueOnce({ success: true, provider: 'partnership', has_account: false,
      account_setup_state: 'identity_verified', nin_verified: true, partnership_setup_required: false })
      .mockResolvedValueOnce({ success: true, provider: 'partnership', account_number: '0454243073' })
      .mockResolvedValueOnce({ success: true, provider: 'partnership', can_fund: true, available: true, has_account: true, account_setup_state: 'ready', account_number: '0454243073', account_name: 'Test Customer', bank_name: 'Wema Bank' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    await act(async () => { await findControl(tree, 'Check my account number').props.onPress(); });
    expect(mockApiJson).toHaveBeenCalledWith('/api/wallet/account/create/', {});
    expect(mockApiJson).toHaveBeenLastCalledWith('/api/wallet/account/');
    expect(JSON.stringify(tree.toJSON())).toContain('Fund by bank transfer');
    expect(JSON.stringify(tree.toJSON())).not.toContain('Get my account');
    act(() => tree.unmount());
  });

  it('retains a current Partnership funding destination when only spending is held', async () => {
    mockApiJson.mockResolvedValue({ success: true, provider: 'partnership', can_fund: true,
      account_setup_state: 'bank_history_review', legacy_funding_review: false,
      account_number: '0454243073', account_name: 'Current Customer', available: true, has_account: true,
      spending_available: false, transfers_available: false, bill_payments_available: false,
      migration_message: 'Your account balance needs review before you can spend.' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(findControl(tree, 'Copy account number')).toBeTruthy();
    expect(JSON.stringify(tree.toJSON())).toContain('Your account balance needs review before you can spend.');
    expect(JSON.stringify(tree.toJSON())).not.toContain('Get my account');
    act(() => tree.unmount());
  });

  it('hides funding details when the server holds the account for balance review', async () => {
    mockApiJson.mockResolvedValue({ success: true, provider: 'partnership', can_fund: false, legacy_funding_review: true, account_setup_state: 'legacy_funding_review',
      account_number: '0454243073', account_name: 'Test Customer', bank_name: 'Wema Bank', available: true, has_account: true,
      spending_available: false, transfers_available: false, bill_payments_available: false,
      migration_message: 'Your account balance needs review before you can spend. Please contact Zitch Support.' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).toContain('Your account balance needs review before you can spend.');
    expect(JSON.stringify(tree.toJSON())).not.toContain('Get my account');
    act(() => tree.unmount());
  });

  it('hands a server-selected NIN OTP attempt to KYC instead of BVN confirmation', async () => {
    mockApiJson
      .mockResolvedValueOnce({ success: true, provider: 'partnership', has_account: false, account_setup_state: 'identity_required' })
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

  it.each([{ success: false, offline: true }, { success: false, message: 'Service unavailable' }, {}])('does not offer new BVN provisioning on an unsuccessful account lookup %j', async (response) => {
    mockApiJson.mockResolvedValueOnce(response);

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    await act(async () => { await Promise.resolve(); });

    expect(findControl(tree, 'Try again')).toBeTruthy();
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
  });
});

describe('AddMoney current account binding', () => {
  beforeEach(() => { mockGeneration = 0; mockApiJson.mockReset(); mockPush.mockReset(); });

  it('does not show funding details from an account lookup completed after sign-out', async () => {
    let finish!: (value: unknown) => void;
    mockApiJson.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    mockGeneration += 1;
    await act(async () => { finish({ success: true, provider: 'partnership', can_fund: true, available: true, has_account: true, account_setup_state: 'ready', account_number: '0454243073' }); });
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).not.toContain('0454');
    act(() => tree.unmount());
  });

  it('does not fetch the new customer account after an old provisioning request completes', async () => {
    let finish!: (value: unknown) => void;
    mockApiJson.mockResolvedValueOnce({ success: true, provider: 'partnership', account_setup_state: 'identity_required' })
      .mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    act(() => tree.root.findByType(TextInput).props.onChangeText('12345678901'));
    await act(async () => { void findControl(tree, 'Get my account').props.onPress(); });
    mockGeneration += 1;
    await act(async () => { finish({ success: true, account_number: '0454243073' }); });
    expect(mockApiJson).toHaveBeenCalledTimes(2);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
    act(() => tree.unmount());
  });

  it('offers support directly for an account that cannot safely receive funds', async () => {
    mockApiJson.mockResolvedValue({ success: true, provider: 'partnership', account_setup_state: 'legacy_funding_review', legacy_funding_review: true });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    act(() => findControl(tree, 'Contact support').props.onPress());
    expect(mockPush).toHaveBeenCalledWith('/support');
    act(() => tree.unmount());
  });
});
