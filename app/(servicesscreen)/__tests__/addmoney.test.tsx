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
    Field: ({ value, onChangeText }: { value: string; onChangeText: (value: string) => void }) => ReactActual.createElement(
      TextInput,
      { value, onChangeText },
    ),
  };
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
});
