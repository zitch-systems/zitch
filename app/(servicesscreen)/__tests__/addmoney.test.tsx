import React, { type ReactNode } from 'react';
import { TextInput } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import AddMoney from '@/app/(servicesscreen)/addmoney';

const mockApiJson = jest.fn();
const mockPush = jest.fn();
let mockFocus: () => void | (() => void);
let mockBlur: (() => void) | void;

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

describe('AddMoney VAS migration', () => {
  beforeEach(() => {
    mockApiJson.mockReset();
    mockPush.mockReset();
  });

  const enrollment = {
    success: true, provider: 'wema_vas', has_account: false, available: false,
    enrollment_available: true, spending_available: false,
    account_setup_state: 'vas_enrollment_required', migration_message: 'Set up your new virtual account.',
    enrollment_mode: 'validation', consent_version: 'server-validation-consent-version',
  };

  it('offers same-profile verification while allocation is blocked and shows the server reason', async () => {
    mockApiJson.mockResolvedValue({ ...enrollment, enrollment_available: false, test_mode: true,
      enrollment_status: 'review_required', enrollment_blockers: ['balance_review'],
      enrollment_message: 'Your existing balance needs review before account setup.', re_registration_required: true });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(JSON.stringify(tree.toJSON())).toContain('Your existing balance needs review before account setup.');
    expect(JSON.stringify(tree.toJSON())).toContain('Account setup needs review');
    expect(JSON.stringify(tree.toJSON())).not.toContain('Account activation pending');
    expect(JSON.stringify(tree.toJSON())).toContain('on this Zitch profile');
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    act(() => findControl(tree, 'Review verification').props.onPress());
    expect(mockPush).toHaveBeenLastCalledWith('/(auth)/kyc');
    act(() => findControl(tree, 'Confirm my verified name').props.onPress());
    expect(mockPush).toHaveBeenLastCalledWith({ pathname: '/(auth)/kyc', params: { verify_identity: 'bvn' } });
    expect(mockApiJson).toHaveBeenCalledTimes(1);
    await act(async () => tree.unmount());
  });

  it('reloads eligibility after verification returns and discards a blurred identity and consent', async () => {
    mockApiJson.mockResolvedValueOnce({ ...enrollment, enrollment_available: false })
      .mockResolvedValueOnce({ ...enrollment, test_mode: true })
      .mockResolvedValueOnce({ ...enrollment, test_mode: true });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    await act(async () => { mockBlur?.(); mockBlur = mockFocus(); });
    expect(tree.root.findByType(TextInput)).toBeTruthy();
    act(() => tree.root.findByType(TextInput).props.onChangeText('11111111111'));
    act(() => findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress());
    expect(findControl(tree, 'Set up account').props.disabled).toBe(false);
    await act(async () => { mockBlur?.(); mockBlur = mockFocus(); });
    expect(tree.root.findByType(TextInput).props.value).toBe('');
    expect(findControl(tree, 'Consent to VAS identity storage and sharing').props.accessibilityState.checked).toBe(false);
    expect(mockApiJson.mock.calls.map(([path]) => path)).toEqual(Array(3).fill('/api/wallet/account/'));
    await act(async () => tree.unmount());
  });

  it('requires explicit integration-validation consent and never allocates automatically', async () => {
    mockApiJson.mockResolvedValueOnce({ ...enrollment, test_mode: true,
      enrollment_mode: 'validation', consent_version: 'server-validation-consent-version' })
      .mockResolvedValueOnce({ success: true })
      .mockResolvedValueOnce({ ...enrollment, test_mode: true, enrollment_available: false,
        account_setup_state: 'vas_validation', validation_account_number: '7111234567' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(JSON.stringify(tree.toJSON())).toContain('Wema Bank for integration validation. Account activation remains pending.');
    expect(mockApiJson).toHaveBeenCalledTimes(1);
    act(() => tree.root.findByType(TextInput).props.onChangeText('11111111111'));
    expect(findControl(tree, 'Set up account').props.disabled).toBe(true);
    act(() => findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress());
    await act(async () => { await findControl(tree, 'Set up account').props.onPress(); });
    expect(mockApiJson).toHaveBeenNthCalledWith(2, '/api/wallet/vas/identity/start/', {
      identity_type: 'bvn', number: '11111111111', consent: true, enrollment_mode: 'validation', consent_version: 'server-validation-consent-version',
    });
    expect(JSON.stringify(tree.toJSON())).toContain('Account activation pending');
    expect(JSON.stringify(tree.toJSON())).not.toContain('Fund by bank transfer');
    await act(async () => tree.unmount());
  });

  it('requires explicit consent and sends the selected verified identity only to VAS', async () => {
    mockApiJson.mockResolvedValueOnce(enrollment).mockResolvedValueOnce({ success: true })
      .mockResolvedValueOnce({ ...enrollment, enrollment_available: false, account_setup_state: 'vas_validation' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(tree.root.findByType(TextInput).props.secureTextEntry).toBe(true);
    await act(async () => { findControl(tree, 'Use NIN').props.onPress(); });
    await act(async () => { tree.root.findByType(TextInput).props.onChangeText('11111111111'); });
    expect(findControl(tree, 'Set up account').props.disabled).toBe(true);
    await act(async () => { await findControl(tree, 'Set up account').props.onPress(); });
    expect(mockApiJson).toHaveBeenCalledTimes(1);
    await act(async () => { findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress(); });
    await act(async () => { await findControl(tree, 'Set up account').props.onPress(); });
    expect(mockApiJson).toHaveBeenNthCalledWith(2, '/api/wallet/vas/identity/start/', {
      identity_type: 'nin', number: '11111111111', consent: true,
      enrollment_mode: 'validation', consent_version: 'server-validation-consent-version',
    });
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).not.toContain('11111111111');
  });

  it('clears the re-entered identity when enrollment fails', async () => {
    mockApiJson.mockResolvedValueOnce(enrollment).mockResolvedValueOnce({ success: false, message: 'Identity could not be verified.' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    await act(async () => { tree.root.findByType(TextInput).props.onChangeText('22222222222'); });
    await act(async () => { findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress(); });
    await act(async () => { await findControl(tree, 'Set up account').props.onPress(); });
    expect(tree.root.findByType(TextInput).props.value).toBe('');
    expect(findControl(tree, 'Set up account').props.disabled).toBe(true);
  });

  it.each(['bvn', 'nin'])('verifies %s and allocates with one identity entry and no identity in the OTP request', async (kind) => {
    mockApiJson.mockResolvedValueOnce({ ...enrollment, enrollment_available: false,
      enrollment_status: 'verification_required', enrollment_blockers: ['identity_verification'] })
      .mockResolvedValueOnce({ success: true, otp_required: true, challenge_id: 'private-challenge', delivery: 'registered phone •••••8888' })
      .mockResolvedValueOnce({ success: true, identity_verified: true })
      .mockResolvedValueOnce({ ...enrollment, account_setup_state: 'vas_validation', enrollment_status: 'enrolled', enrollment_available: false });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    if (kind === 'nin') act(() => findControl(tree, 'Use NIN').props.onPress());
    act(() => tree.root.findByType(TextInput).props.onChangeText('12345678901'));
    act(() => findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress());
    await act(async () => { await findControl(tree, 'Set up account').props.onPress(); });
    expect(JSON.stringify(tree.toJSON())).not.toContain('12345678901');
    expect(JSON.stringify(tree.toJSON())).toContain('registered phone •••••8888');
    act(() => tree.root.findByType(TextInput).props.onChangeText('123456'));
    await act(async () => { await findControl(tree, 'Confirm and set up account').props.onPress(); });
    expect(mockApiJson).toHaveBeenNthCalledWith(2, '/api/wallet/vas/identity/start/', {
      identity_type: kind, number: '12345678901', consent: true,
      enrollment_mode: 'validation', consent_version: 'server-validation-consent-version',
    });
    expect(mockApiJson).toHaveBeenNthCalledWith(3, '/api/wallet/vas/identity/confirm/', { challenge_id: 'private-challenge', otp: '123456' });
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).toContain('Account activation pending');
    expect(mockPush).not.toHaveBeenCalled();
    await act(async () => tree.unmount());
  });

  it('keeps the ownership challenge through wrong-code and resend without requesting the identity again', async () => {
    mockApiJson.mockResolvedValueOnce(enrollment)
      .mockResolvedValueOnce({ success: true, otp_required: true, challenge_id: 'private-challenge', delivery: 'registered phone •••••8888', delivery_notice: 'Email delivery failed. Use SMS.' })
      .mockResolvedValueOnce({ success: false, message: 'Incorrect code.' })
      .mockResolvedValueOnce({ success: true, otp_required: true, challenge_id: 'private-challenge', delivery: 'registered phone •••••8888 and email a***@example.com' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    act(() => tree.root.findByType(TextInput).props.onChangeText('12345678901'));
    act(() => findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress());
    await act(async () => { await findControl(tree, 'Set up account').props.onPress(); });
    act(() => tree.root.findByType(TextInput).props.onChangeText('000000'));
    await act(async () => { await findControl(tree, 'Confirm and set up account').props.onPress(); });
    expect(findControl(tree, 'Resend code')).toBeTruthy();
    await act(async () => { await findControl(tree, 'Resend code').props.onPress(); });
    expect(mockApiJson).toHaveBeenLastCalledWith('/api/wallet/vas/identity/resend/', { challenge_id: 'private-challenge' });
    expect(JSON.stringify(tree.toJSON())).toContain('email a***@example.com');
    expect(JSON.stringify(tree.toJSON())).not.toContain('Email delivery failed');
    expect(tree.root.findByType(TextInput).props.value).toBe('');
    await act(async () => tree.unmount());
  });

  it('honours the server resend cooldown before accepting another OTP delivery request', async () => {
    jest.useFakeTimers();
    mockApiJson.mockResolvedValueOnce(enrollment)
      .mockResolvedValueOnce({ success: true, otp_required: true, challenge_id: 'private-challenge', resend_after: 1 });
    let tree!: renderer.ReactTestRenderer;
    try {
      await act(async () => { tree = renderer.create(<AddMoney />); });
      act(() => tree.root.findByType(TextInput).props.onChangeText('12345678901'));
      act(() => findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress());
      await act(async () => { await findControl(tree, 'Set up account').props.onPress(); });
      const resend = findControl(tree, 'Resend code in 1s');
      expect(resend.props.disabled).toBe(true);
      await act(async () => { await resend.props.onPress(); });
      expect(mockApiJson).toHaveBeenCalledTimes(2);
      await act(async () => { jest.advanceTimersByTime(1000); });
      expect(findControl(tree, 'Resend code').props.disabled).toBe(false);
    } finally {
      act(() => tree.unmount());
      jest.useRealTimers();
    }
  });

  it('retries allocation with the verified challenge without another identity or OTP entry', async () => {
    mockApiJson.mockResolvedValueOnce(enrollment)
      .mockResolvedValueOnce({ success: false, identity_verified: true, retry_available: true, challenge_id: 'verified-challenge', message: 'Account setup is temporarily unavailable.' })
      .mockResolvedValueOnce({ success: true, identity_verified: true })
      .mockResolvedValueOnce({ ...enrollment, account_setup_state: 'vas_validation', enrollment_status: 'enrolled', enrollment_available: false });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    act(() => tree.root.findByType(TextInput).props.onChangeText('12345678901'));
    act(() => findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress());
    await act(async () => { await findControl(tree, 'Set up account').props.onPress(); });
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).toContain('Your identity has been verified.');
    await act(async () => { await findControl(tree, 'Retry account setup').props.onPress(); });
    expect(mockApiJson).toHaveBeenNthCalledWith(3, '/api/wallet/vas/identity/confirm/', { challenge_id: 'verified-challenge' });
    expect(JSON.stringify(tree.toJSON())).toContain('Account activation pending');
    await act(async () => tree.unmount());
  });

  it('discards a blurred in-flight challenge response and requires fresh consent', async () => {
    let resolveStart!: (value: unknown) => void;
    mockApiJson.mockResolvedValueOnce(enrollment)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveStart = resolve; }))
      .mockResolvedValueOnce(enrollment);
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    act(() => tree.root.findByType(TextInput).props.onChangeText('12345678901'));
    act(() => findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress());
    let request!: Promise<void>;
    act(() => { request = findControl(tree, 'Set up account').props.onPress(); });
    await act(async () => { mockBlur?.(); mockBlur = mockFocus(); });
    await act(async () => { resolveStart({ success: true, otp_required: true, challenge_id: 'stale-challenge' }); await request; });
    expect(tree.root.findByType(TextInput).props.value).toBe('');
    expect(findControl(tree, 'Consent to VAS identity storage and sharing').props.accessibilityState.checked).toBe(false);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Confirm and set up account' })).toHaveLength(0);
    await act(async () => tree.unmount());
  });

  it('refreshes durable funding status when a completed challenge response was lost', async () => {
    mockApiJson.mockResolvedValueOnce(enrollment)
      .mockResolvedValueOnce({ success: true, otp_required: true, challenge_id: 'private-challenge' })
      .mockResolvedValueOnce({ success: false, code: 'vas_identity_challenge_expired', retry_available: false })
      .mockResolvedValueOnce({ ...enrollment, account_setup_state: 'vas_validation', enrollment_status: 'enrolled', enrollment_available: false });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    act(() => tree.root.findByType(TextInput).props.onChangeText('12345678901'));
    act(() => findControl(tree, 'Consent to VAS identity storage and sharing').props.onPress());
    await act(async () => { await findControl(tree, 'Set up account').props.onPress(); });
    act(() => tree.root.findByType(TextInput).props.onChangeText('123456'));
    await act(async () => { await findControl(tree, 'Confirm and set up account').props.onPress(); });
    expect(mockApiJson).toHaveBeenLastCalledWith('/api/wallet/account/');
    expect(tree.root.findAllByType(TextInput)).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).toContain('Account activation pending');
    await act(async () => tree.unmount());
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

  it('shows activation pending without exposing a validation number or copy action', async () => {
    mockApiJson.mockResolvedValueOnce({ ...enrollment, account_setup_state: 'vas_validation',
      test_mode: true, available: false, has_account: false, enrollment_available: false,
      validation_account_number: '7111234567', validation_account_name: 'Zitch/Ada' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    const output = JSON.stringify(tree.toJSON());
    expect(output).toContain('Account activation pending');
    expect(output).not.toContain('7111 234 567');
    expect(output).not.toContain('Zitch/Ada');
    expect(output).not.toMatch(/test account|test mode|account testing/i);
    expect(output).not.toContain('Fund by bank transfer');
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy test account number' })).toHaveLength(0);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Confirm my verified name' })).toHaveLength(0);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
    await act(async () => tree.unmount());
  });

  it.each([
    { test_mode: false, account_setup_state: 'vas_validation', validation_account_number: '7111234567' },
    { test_mode: true, account_setup_state: 'restricted', validation_account_number: '7111234567' },
    { test_mode: true, account_setup_state: 'vas_validation', validation_account_number: '7121234567' },
    { test_mode: true, account_setup_state: 'ready', account_number: '7111234567', has_account: true, available: true },
    { test_mode: false, account_setup_state: 'ready', account_number: '7111234567', has_account: true, available: true },
  ])('does not present an unapproved validation sample: %j', async (sample) => {
    mockApiJson.mockResolvedValueOnce({ ...enrollment, enrollment_available: false, ...sample });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy test account number' })).toHaveLength(0);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' })).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).not.toContain('7111 234 567');
    expect(JSON.stringify(tree.toJSON())).not.toContain('7121 234 567');
    await act(async () => tree.unmount());
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

  it.each([false, true])('keeps eligible bill payments available when the new account is ready: %s', async (ready) => {
    mockApiJson.mockResolvedValueOnce({ ...enrollment,
      account_setup_state: ready ? 'ready' : 'vas_enrollment_required',
      available: ready, has_account: ready, enrollment_available: !ready,
      bill_payments_available: true, transfers_available: false,
      account_number: ready ? '7121234567' : '', account_name: 'Zitch/Ada', bank_name: 'Wema Bank' });
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<AddMoney />); });
    const output = JSON.stringify(tree.toJSON());
    expect(output).toContain('Bill payments are available. Transfers are currently unavailable.');
    expect(output).not.toContain('Transfers and bill payments are currently unavailable.');
    expect(output).not.toContain('after Wema confirms');
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Copy account number' }).length > 0).toBe(ready);
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
