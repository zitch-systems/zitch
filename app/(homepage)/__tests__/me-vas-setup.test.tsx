import React, { type ReactNode } from 'react';
import { Pressable } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import Me from '@/app/(homepage)/me';

const mockPush = jest.fn();
let mockProvider = 'wema_vas';
const mockReload = jest.fn();
const mockGetStatus = jest.fn().mockResolvedValue({ tier: 1 });
jest.mock('expo-router', () => ({
  router: { push: (...args: unknown[]) => mockPush(...args) },
  useFocusEffect: (callback: () => void) => {
    const ReactActual = jest.requireActual<typeof import('react')>('react');
    ReactActual.useEffect(callback, [callback]);
  },
}));
jest.mock('@/lib/wallet', () => ({ useWallet: () => ({
  totalBalance: 0, firstName: 'Ada', showBal: true, fundingProvider: mockProvider, reload: mockReload,
}) }));
jest.mock('@/lib/api', () => ({ apiPost: jest.fn() }));
jest.mock('@/lib/services/kyc', () => ({ kycService: { getStatus: () => mockGetStatus() } }));
jest.mock('@/lib/secureStore', () => ({ getToken: async () => 'session', clearSession: jest.fn() }));
jest.mock('@/lib/biometrics', () => ({ isBiometricEnabled: async () => false }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Brand', () => ({ Avatar: () => null }));
jest.mock('@/components/design/WhatsAppGlyph', () => ({ WhatsAppGlyph: () => null }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/widgets', () => ({ Hero: ({ children }: { children: ReactNode }) => children }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: {}, theme: 'light', setTheme: jest.fn() }), font: {},
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Text, View, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Card: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    NText: Text, money: String,
    ZItem: ({ title, sub, onPress }: { title: string; sub?: string; onPress: () => void }) =>
      ReactActual.createElement(Pressable, { accessibilityLabel: title, onPress },
        ReactActual.createElement(Text, null, sub)),
  };
});

it.each(['wema_vas', 'partnership'])('offers same-profile VAS setup only for the selected VAS provider: %s', async (provider) => {
  mockProvider = provider;
  mockPush.mockReset();
  let tree!: renderer.ReactTestRenderer;
  try {
    await act(async () => { tree = renderer.create(<Me />); });
    const controls = tree.root.findAllByType(Pressable)
      .filter((node) => node.props.accessibilityLabel === 'Continue VAS setup');
    expect(controls).toHaveLength(provider === 'wema_vas' ? 1 : 0);
    if (provider === 'wema_vas') {
      act(() => controls[0].props.onPress());
      expect(mockPush).toHaveBeenCalledWith('/addmoney');
      expect(JSON.stringify(tree.toJSON())).toContain('Use your existing Zitch profile');
    }
  } finally {
    if (tree) act(() => tree.unmount());
  }
});
