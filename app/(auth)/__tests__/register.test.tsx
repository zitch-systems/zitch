import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import Register from '@/app/(auth)/register';

const mockPublicPost = jest.fn();
const mockMultiSet = jest.fn();
const mockPush = jest.fn();

jest.mock('@/lib/api', () => ({ publicPost: (...args: unknown[]) => mockPublicPost(...args) }));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: { multiSet: (...args: unknown[]) => mockMultiSet(...args) },
}));
jest.mock('expo-router', () => ({
  router: { push: (...args: unknown[]) => mockPush(...args), replace: jest.fn() },
  Link: ({ children }: { children: ReactNode }) => children,
}));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Loading', () => ({ Loading: () => null }));
jest.mock('@/lib/theme', () => ({
  useTheme: () => ({ c: { ink1: '#111', ink3: '#333', red: '#f00', brand: '#090' } }),
  font: { bold: 'bold', extrabold: 'extrabold', regular: 'regular', semibold: 'semibold' },
}));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { Pressable, Text, TextInput, View } = jest.requireActual<typeof import('react-native')>('react-native');
  return {
    Screen: ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children),
    Header: () => null,
    Field: ({ label, value, onChangeText }: { label: string; value: string; onChangeText: (value: string) => void }) =>
      ReactActual.createElement(TextInput, { accessibilityLabel: label, value, onChangeText }),
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) =>
      ReactActual.createElement(
        Pressable,
        { accessibilityLabel: label, onPress, disabled },
        ReactActual.createElement(Text, null, label),
      ),
  };
});

const control = (tree: renderer.ReactTestRenderer, label: string) =>
  tree.root.findByProps({ accessibilityLabel: label });

describe('registration identity handoff', () => {
  beforeEach(() => {
    mockPublicPost.mockReset();
    mockMultiSet.mockReset();
    mockPush.mockReset();
    mockMultiSet.mockResolvedValue(undefined);
    mockPublicPost.mockResolvedValue({ ok: true, json: async () => ({ success: true }) });
  });

  it('stores the normalized name before continuing to OTP account creation', async () => {
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Register />); });

    await act(async () => { control(tree, 'Full name').props.onChangeText('  Ada   Nneka Okafor '); });
    await act(async () => { control(tree, 'Phone number').props.onChangeText('08012345678'); });
    await act(async () => { control(tree, 'Email address').props.onChangeText('ADA@EXAMPLE.COM '); });
    expect(control(tree, 'Continue').props.disabled).toBe(false);

    await act(async () => { await control(tree, 'Continue').props.onPress(); });

    expect(mockPublicPost).toHaveBeenCalledWith('/api/phone_verification/', {
      email: 'ada@example.com',
      phone: '08012345678',
    });
    expect(mockMultiSet).toHaveBeenCalledWith(expect.arrayContaining([
      ['UserFirstName', 'Ada'],
      ['UserLastName', 'Nneka Okafor'],
      ['UserPhone', '08012345678'],
      ['UserEmail', 'ada@example.com'],
    ]));
    expect(mockPush).toHaveBeenCalledWith('/otp');
  });

  it('sends only one signup request for repeated taps in the same frame', async () => {
    let resolveRequest!: (value: unknown) => void;
    mockPublicPost.mockReturnValue(new Promise((resolve) => { resolveRequest = resolve; }));
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Register />); });
    await act(async () => { control(tree, 'Full name').props.onChangeText('Ada Okafor'); });
    await act(async () => { control(tree, 'Phone number').props.onChangeText('08012345678'); });
    await act(async () => { control(tree, 'Email address').props.onChangeText('ada@example.com'); });

    let first!: Promise<void>;
    await act(async () => {
      const button = control(tree, 'Continue');
      first = button.props.onPress();
      button.props.onPress();
      await Promise.resolve();
    });
    expect(mockPublicPost).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveRequest({ ok: true, json: async () => ({ success: true }) });
      await first;
    });
  });

  it('does not accept a single name as a full name', async () => {
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Register />); });
    await act(async () => { control(tree, 'Full name').props.onChangeText('Ada'); });
    await act(async () => { control(tree, 'Phone number').props.onChangeText('08012345678'); });
    await act(async () => { control(tree, 'Email address').props.onChangeText('ada@example.com'); });
    expect(control(tree, 'Continue').props.disabled).toBe(true);
  });

  it('requires an email because verified phone and email are Tier 1 prerequisites', async () => {
    let tree!: renderer.ReactTestRenderer;
    await act(async () => { tree = renderer.create(<Register />); });
    await act(async () => { control(tree, 'Full name').props.onChangeText('Ada Okafor'); });
    await act(async () => { control(tree, 'Phone number').props.onChangeText('08012345678'); });
    expect(control(tree, 'Continue').props.disabled).toBe(true);
  });
});
