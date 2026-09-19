import React, { type ReactNode } from 'react';
import { Alert } from 'react-native';
import renderer, { act } from 'react-test-renderer';

import Cards from '@/app/(homepage)/cards';
import type { VirtualCard } from '@/lib/services/cards';

const mockList = jest.fn();
const mockFreeze = jest.fn();
const mockNotify = jest.fn();

jest.mock('@/lib/services/cards', () => ({
  cardsService: {
    list: (...args: unknown[]) => mockList(...args),
    freeze: (...args: unknown[]) => mockFreeze(...args),
    create: jest.fn(),
    fund: jest.fn(),
    details: jest.fn(),
  },
}));
jest.mock('@/lib/secureStore', () => ({ getToken: jest.fn().mockResolvedValue('token') }));
jest.mock('@/lib/pendingSpend', () => ({
  acquireSpendAttempt: jest.fn(),
  clearSpendAttempt: jest.fn(),
}));
jest.mock('@/components/design/Notify', () => ({
  notify: (...args: unknown[]) => mockNotify(...args),
}));
jest.mock('@/lib/wallet', () => ({ useWallet: () => ({ reload: jest.fn() }) }));
jest.mock('expo-router', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  return {
    useFocusEffect: (effect: () => void | (() => void)) => ReactActual.useEffect(effect, [effect]),
  };
});
jest.mock('expo-linear-gradient', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    LinearGradient: ({ children }: { children: ReactNode }) =>
      ReactActual.createElement(View, null, children),
  };
});
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/flowkit', () => ({ QuickAmounts: () => null }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: {
    brand: '#0FA295', ink1: '#111', ink2: '#222', ink3: '#333', line: '#ddd', surface: '#fff',
  } }),
  font: {
    bold: 'bold', extrabold: 'extrabold', medium: 'medium', regular: 'regular',
    semibold: 'semibold',
  },
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) =>
      ReactActual.createElement(
        Pressable,
        { accessibilityLabel: label, disabled, onPress },
        ReactActual.createElement(Text, null, label),
      ),
    Field: () => null,
    Sheet: ({ children, open }: { children: ReactNode; open: boolean }) =>
      open ? ReactActual.createElement(View, null, children) : null,
    PinPad: () => null,
    Naira: () => null,
    money: (amount: number) => `₦${amount}`,
  };
});

const card = (capabilities: VirtualCard['capabilities'], frozen = false): VirtualCard => ({
  id: 7,
  brand: 'Verve',
  last4: '1234',
  masked: '5061 •••• •••• 1234',
  expiry: '12/29',
  holder: 'ADA EZE',
  balance: '0.00',
  status: frozen ? 'frozen' : 'active',
  frozen,
  capabilities,
});

const wemaCapabilities = {
  can_fund: false,
  can_unfreeze: false,
  permanent_block: true,
};

describe('card provider capabilities', () => {
  beforeEach(() => {
    mockList.mockReset();
    mockFreeze.mockReset();
    mockNotify.mockReset();
    jest.spyOn(Alert, 'alert').mockImplementation(() => {});
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('hides Wema funding and confirms that blocking is permanent', async () => {
    const active = card(wemaCapabilities);
    mockList.mockResolvedValue({ success: true, cards: [active] });
    mockFreeze.mockResolvedValue({
      success: true,
      _httpOk: true,
      _httpStatus: 200,
      message: 'Card permanently blocked',
      card: card(wemaCapabilities, true),
    });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Cards />); });

    expect(tree.root.findAllByProps({ accessibilityLabel: 'Fund' })).toHaveLength(0);
    expect(tree.root.findByProps({ accessibilityLabel: 'Block' })).toBeTruthy();
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Unfreeze' })).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).toContain('Blocking it is permanent');

    act(() => tree.root.findByProps({ accessibilityLabel: 'Block' }).props.onPress());
    expect(Alert.alert).toHaveBeenCalledWith(
      'Permanently block this card?',
      expect.stringContaining('cannot be undone'),
      expect.any(Array),
    );
    const buttons = (Alert.alert as jest.Mock).mock.calls[0][2];
    await act(async () => { await buttons[1].onPress(); });

    expect(mockFreeze).toHaveBeenCalledWith(7);
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Unfreeze' })).toHaveLength(0);
    expect(tree.root.findByProps({ accessibilityLabel: 'Blocked' })).toBeTruthy();
    expect(JSON.stringify(tree.toJSON())).toContain('PERMANENTLY BLOCKED');
  });

  it('preserves generic issuer fund and reversible freeze actions', async () => {
    mockList.mockResolvedValue({
      success: true,
      cards: [card({ can_fund: true, can_unfreeze: true, permanent_block: false })],
    });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Cards />); });

    expect(tree.root.findByProps({ accessibilityLabel: 'Fund' })).toBeTruthy();
    expect(tree.root.findByProps({ accessibilityLabel: 'Freeze' })).toBeTruthy();
    expect(JSON.stringify(tree.toJSON())).toContain('Fund it from your wallet');
  });

  it('offers reload instead of retry after an ambiguous permanent block', async () => {
    const active = card(wemaCapabilities);
    mockList.mockResolvedValue({ success: true, cards: [active] });
    mockFreeze.mockResolvedValue({
      success: false,
      pending: true,
      _httpOk: false,
      _httpStatus: 409,
      code: 'card_status_pending',
      message: 'Outcome unconfirmed',
    });

    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Cards />); });
    act(() => tree.root.findByProps({ accessibilityLabel: 'Block' }).props.onPress());
    const buttons = (Alert.alert as jest.Mock).mock.calls[0][2];
    await act(async () => { await buttons[1].onPress(); });

    expect(tree.root.findByProps({ accessibilityLabel: 'Reload status' })).toBeTruthy();
    expect(tree.root.findAllByProps({ accessibilityLabel: 'Block' })).toHaveLength(0);
    expect(mockNotify).toHaveBeenCalledWith(
      'Card status not confirmed',
      expect.stringContaining('Do not try again'),
      'info',
    );
  });
});
