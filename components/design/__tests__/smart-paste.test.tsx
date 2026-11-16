import React from 'react';
import renderer, { act } from 'react-test-renderer';
import { AppState, Platform } from 'react-native';

const mockPush = jest.fn();
const mockHas = jest.fn();
const mockGet = jest.fn();
let mockFocus: (() => void | (() => void)) | null = null;

jest.mock('react-native/Libraries/Components/Pressable/Pressable', () => {
  const ReactActual = jest.requireActual('react');
  return { default: function MockPressable({ children, ...props }: any) {
    return ReactActual.createElement('View', props, typeof children === 'function' ? children({ pressed: false }) : children);
  } };
});
jest.mock('expo-router', () => ({
  router: { push: (...args: unknown[]) => mockPush(...args) },
  useFocusEffect: (cb: () => void | (() => void)) => { mockFocus = cb; },
}));
jest.mock('expo-clipboard', () => ({ hasStringAsync: () => mockHas(), getStringAsync: () => mockGet() }));
jest.mock('@/lib/wallet', () => ({ useWallet: () => ({ accountNumber: '0123456789' }) }));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));
jest.mock('@/lib/screenCapture', () => ({ usePinScreenProtection: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/Notify', () => ({ notify: jest.fn() }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null, LoadingMark: () => null }));
jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn(async () => {}) }));

// eslint-disable-next-line import/first
import SmartPaste, { resetOfferedClipboardNumbers } from '../SmartPaste';

const text = (tree: renderer.ReactTestRenderer) => JSON.stringify(tree.toJSON());

async function openHome() {
  let tree!: renderer.ReactTestRenderer;
  act(() => { tree = renderer.create(<SmartPaste />); });
  let cleanup: void | (() => void);
  await act(async () => { cleanup = mockFocus?.(); });
  return { tree, cleanup: () => { if (typeof cleanup === 'function') cleanup(); } };
}

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  resetOfferedClipboardNumbers();
  Platform.OS = 'android';
  mockHas.mockResolvedValue(true);
});
afterEach(() => jest.useRealTimers());

it('offers a copied phone number as its account number and opens Send money with it', async () => {
  mockGet.mockResolvedValue('0808 110 3848');
  const { tree, cleanup } = await openHome();
  expect(text(tree)).toContain('Transfer to this account?');
  expect(text(tree)).toContain('8081103848');
  expect(text(tree)).toContain('0808 110 3848');
  act(() => tree.root.findByProps({ accessibilityLabel: 'Transfer now' }).props.onPress());
  act(() => { jest.advanceTimersByTime(300); });
  expect(mockPush).toHaveBeenCalledWith({ pathname: '/sendmoney', params: { identifier: '8081103848' } });
  cleanup(); act(() => tree.unmount());
});

it('offers each number once, and never the customer’s own account', async () => {
  mockGet.mockResolvedValue('8081103848');
  const first = await openHome();
  act(() => first.tree.root.findByProps({ accessibilityLabel: 'Ignore' }).props.onPress());
  expect(text(first.tree)).not.toContain('Transfer to this account?');
  first.cleanup(); act(() => first.tree.unmount());

  const again = await openHome();
  expect(text(again.tree)).not.toContain('Transfer to this account?');
  again.cleanup(); act(() => again.tree.unmount());

  mockGet.mockResolvedValue('0123456789');
  const own = await openHome();
  expect(text(own.tree)).not.toContain('Transfer to this account?');
  own.cleanup(); act(() => own.tree.unmount());
});

it('checks again when the app comes back to the front', async () => {
  mockGet.mockResolvedValue('hello');
  const listeners: ((s: string) => void)[] = [];
  const spy = jest.spyOn(AppState, 'addEventListener').mockImplementation((_e: any, fn: any) => { listeners.push(fn); return { remove: jest.fn() } as any; });
  const { tree, cleanup } = await openHome();
  expect(text(tree)).not.toContain('Transfer to this account?');
  mockGet.mockResolvedValue('012 399 9888');
  expect(listeners).toHaveLength(1);
  await act(async () => { listeners.forEach((fn) => fn('active')); await Promise.resolve(); await Promise.resolve(); });
  expect(text(tree)).toContain('0123999888');
  cleanup(); act(() => tree.unmount()); spy.mockRestore();
});

it('does not read the clipboard by itself in a browser', async () => {
  Platform.OS = 'web';
  mockGet.mockResolvedValue('8081103848');
  const { tree, cleanup } = await openHome();
  expect(mockGet).not.toHaveBeenCalled();
  expect(text(tree)).toContain('Pay from clipboard');
  cleanup(); act(() => tree.unmount());
});
