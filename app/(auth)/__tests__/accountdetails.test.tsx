import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';

import AccountDetails from '@/app/(auth)/accountdetails';

const mockApiPost = jest.fn();
const mockGetToken = jest.fn();
const mockNotify = jest.fn();
const mockRouterPush = jest.fn();

jest.mock('expo-router', () => ({
  router: {
    back: jest.fn(),
    push: (...args: unknown[]) => mockRouterPush(...args),
  },
}));
jest.mock('@/lib/api', () => ({ apiPost: (...args: unknown[]) => mockApiPost(...args) }));
jest.mock('@/lib/secureStore', () => ({ getToken: (...args: unknown[]) => mockGetToken(...args) }));
jest.mock('@/lib/endpoints', () => ({
  EP: { wallet: { balance: '/balance' }, auth: { updateInfo: '/update', avatar: '/avatar' } },
}));
jest.mock('@/lib/session', () => ({ beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));
jest.mock('@/lib/wallet', () => ({ useWallet: () => ({ reload: jest.fn() }) }));
jest.mock('@/components/design/Notify', () => ({ notify: (...args: unknown[]) => mockNotify(...args) }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Brand', () => ({ Avatar: () => null }));
jest.mock('expo-image-picker', () => ({
  MediaTypeOptions: { Images: 'Images' },
  requestMediaLibraryPermissionsAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: { setItem: jest.fn() },
}));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { brand: '#090', ink1: '#111', ink3: '#333', surface: '#fff' } }),
  font: { bold: 'bold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const {
    Pressable, Text, TextInput: NativeTextInput, View,
  } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Header: () => null,
    Field: (props: Record<string, unknown>) => ReactActual.createElement(NativeTextInput, {
      ...props,
      accessibilityLabel: typeof props.label === 'string' ? props.label : undefined,
    }),
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) =>
      ReactActual.createElement(
        Pressable,
        { accessibilityLabel: label, onPress, disabled },
        ReactActual.createElement(Text, null, label),
      ),
    Tap: ({ children, onPress, disabled, accessibilityLabel }: {
      children: ReactNode; onPress: () => void; disabled?: boolean; accessibilityLabel?: string;
    }) => ReactActual.createElement(Pressable, { onPress, disabled, accessibilityLabel }, children),
  };
});

describe('AccountDetails verified phone protection', () => {
  beforeEach(() => {
    mockApiPost.mockReset();
    mockGetToken.mockReset().mockResolvedValue('session-token');
    mockNotify.mockReset();
    mockRouterPush.mockReset();
    mockApiPost
      .mockResolvedValueOnce({
        json: async () => ({
          success: true,
          user_first_name: 'Ada',
          user_last_name: 'Okafor',
          user_email: 'ada@example.com',
          user_phone_number: '08020000002',
        }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ success: true }) });
  });

  it('keeps the verified phone read-only and omits it from profile updates', async () => {
    let tree!: renderer.ReactTestRenderer;
    await act(async () => {
      tree = renderer.create(<AccountDetails />);
      await Promise.resolve();
      await Promise.resolve();
    });

    const phone = tree.root.findByProps({ accessibilityLabel: 'Verified phone' });
    expect(phone.props.value).toBe('08020000002');
    expect(phone.props.editable).toBe(false);

    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: 'First name' }).props.onChangeText('Adanna');
    });
    await act(async () => {
      await tree.root.findByProps({ accessibilityLabel: 'Save changes' }).props.onPress();
    });

    expect(mockApiPost).toHaveBeenLastCalledWith('/update', {
      email: 'ada@example.com',
      first_name: 'Adanna',
      last_name: 'Okafor',
    });
    expect(mockApiPost.mock.calls[1][1]).not.toHaveProperty('phone');
    expect(mockNotify).toHaveBeenCalledWith('Profile updated');
    act(() => tree.unmount());
  });

  it('routes phone-change help to support', async () => {
    let tree!: renderer.ReactTestRenderer;
    await act(async () => {
      tree = renderer.create(<AccountDetails />);
      await Promise.resolve();
      await Promise.resolve();
    });

    const contactSupport = tree.root.findByProps({ accessibilityLabel: 'Contact support to change verified phone' });
    await act(async () => { contactSupport.props.onPress(); });
    expect(mockRouterPush).toHaveBeenCalledWith('/support');
    act(() => tree.unmount());
  });
});
