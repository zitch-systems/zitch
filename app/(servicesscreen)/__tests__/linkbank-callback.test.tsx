import React, { type ReactNode } from 'react';
import renderer, { act } from 'react-test-renderer';
import { router } from 'expo-router';
import { Btn } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import LinkBank from '@/app/(servicesscreen)/linkbank';

const mockApiJson = jest.fn();
const mockOpenAuth = jest.fn();
let mockSessionGeneration = 1;
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
jest.mock('expo-web-browser', () => ({ openAuthSessionAsync: (...args: unknown[]) => mockOpenAuth(...args) }));
jest.mock('expo-linking', () => ({
  createURL: () => 'zitch://linkbank',
  parse: (value: string) => {
    const queryParams: Record<string, string | string[]> = {};
    new URL(value).searchParams.forEach((entry, key) => {
      const previous = queryParams[key];
      queryParams[key] = previous === undefined ? entry : [...(Array.isArray(previous) ? previous : [previous]), entry];
    });
    return { queryParams };
  },
}));
jest.mock('expo-router', () => ({ router: { back: jest.fn(), replace: jest.fn() } }));
jest.mock('@/lib/session', () => ({ beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockSessionGeneration }));
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/lib/theme', () => ({ useTheme: () => ({ c: { brand: '#0FA295', ink1: '#111', ink3: '#333' } }), font: {} }));
jest.mock('@/components/design/ui', () => {
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  const { View, Text, Pressable } = jest.requireActual<typeof import('react-native')>('react-native');
  const Container = ({ children }: { children: ReactNode }) => ReactActual.createElement(View, null, children);
  return {
    Screen: Container, Card: Container, Header: () => null,
    Btn: ({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) => ReactActual.createElement(Pressable, { onPress, disabled }, ReactActual.createElement(Text, null, label)),
  };
});

let tree: renderer.ReactTestRenderer;
const press = async () => { await act(async () => { await tree.root.findByType(Btn).props.onPress(); }); };
beforeEach(async () => {
  jest.clearAllMocks();
  mockApiJson.mockReset();
  mockOpenAuth.mockReset();
  mockSessionGeneration = 1;
  mockApiJson.mockImplementation(async (path: string) => path.endsWith('/connect-init/')
    ? { success: true, mono_url: 'https://link.mono.co/attempt' }
    : { success: true });
  mockOpenAuth.mockResolvedValue({ type: 'success', url: 'zitch://linkbank?code=auth-code&state=opaque-state' });
  await act(async () => { tree = renderer.create(<LinkBank />); });
});
afterEach(() => { act(() => tree.unmount()); });

it('exchanges a valid app callback only after the provider browser returns', async () => {
  await press();
  expect(mockOpenAuth).toHaveBeenCalledWith('https://link.mono.co/attempt', 'zitch://linkbank');
  expect(mockApiJson).toHaveBeenCalledWith('/api/banklink/connect/', { code: 'auth-code', state: 'opaque-state' });
  expect(beginExternalActivity).toHaveBeenCalledTimes(1);
  expect(endExternalActivity).toHaveBeenCalledTimes(1);
  expect(router.replace).toHaveBeenCalledWith('/wallet');
});

it.each([
  'javascript:alert(1)', 'http://link.mono.co/attempt', 'https://example.com/attempt',
  'https://link.mono.co.example.com/attempt', 'https://user:pass@link.mono.co/attempt',
])('does not open an untrusted provider URL %s', async (mono_url) => {
  mockApiJson.mockResolvedValue({ success: true, mono_url });
  await press();
  expect(mockOpenAuth).not.toHaveBeenCalled();
  expect(mockApiJson).toHaveBeenCalledTimes(1);
  expect(notify).toHaveBeenCalledWith('Could not link bank', expect.any(String));
});

it.each([
  'https://example.com/linkbank?code=auth-code&state=opaque-state',
  'zitch://other?code=auth-code&state=opaque-state',
  'zitch://linkbank/other?code=auth-code&state=opaque-state',
  'zitch://linkbank?code=auth-code&state=opaque-state#ignored',
  'zitch://linkbank?code=auth-code&code=other&state=opaque-state',
  'zitch://linkbank?code=auth-code&state=opaque-state&state=other',
  'zitch://linkbank?code=auth-code',
])('never exchanges an unrelated or ambiguous callback %s', async (url) => {
  mockOpenAuth.mockResolvedValue({ type: 'success', url });
  await press();
  expect(mockApiJson).toHaveBeenCalledTimes(1);
  expect(router.replace).not.toHaveBeenCalled();
  expect(endExternalActivity).toHaveBeenCalledTimes(1);
});

it('discards a returned bank authorisation after the signed-in session changes', async () => {
  mockOpenAuth.mockImplementation(async () => {
    mockSessionGeneration += 1;
    return { type: 'success', url: 'zitch://linkbank?code=auth-code&state=opaque-state' };
  });
  await press();
  expect(mockApiJson).toHaveBeenCalledTimes(1);
  expect(router.replace).not.toHaveBeenCalled();
});

it('releases external activity protection after cancellation and suppresses duplicate taps', async () => {
  let resolveBrowser!: (value: unknown) => void;
  mockOpenAuth.mockImplementation(() => new Promise((resolve) => { resolveBrowser = resolve; }));
  await act(async () => {
    const connect = tree.root.findByType(Btn).props.onPress;
    void connect(); void connect();
  });
  expect(mockApiJson).toHaveBeenCalledTimes(1);
  expect(mockOpenAuth).toHaveBeenCalledTimes(1);
  await act(async () => { resolveBrowser({ type: 'cancel' }); });
  expect(mockApiJson).toHaveBeenCalledTimes(1);
  expect(endExternalActivity).toHaveBeenCalledTimes(1);
  expect(tree.root.findByType(Btn).props.disabled).toBe(false);
});
