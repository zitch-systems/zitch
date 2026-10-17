import React from 'react';
import renderer, { act } from 'react-test-renderer';
import { TextInput } from 'react-native';
import BottomNav from '../BottomNav';
import Sidebar from '../Sidebar';
import { Field, Sheet, Toggle } from '../ui';

const mockPush = jest.fn();
jest.mock('expo-router', () => ({ router: { push: (...args: unknown[]) => mockPush(...args) } }));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));
jest.mock('@/lib/screenCapture', () => ({ usePinScreenProtection: jest.fn() }));
jest.mock('@/lib/device', () => ({ useRailWidth: () => 200 }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/WhatsAppGlyph', () => ({ WhatsAppGlyph: () => null }));
jest.mock('@/components/design/Brand', () => ({ ZMark: () => null, ZWordmark: () => null, Avatar: () => null }));
jest.mock('@/components/design/Loading', () => ({ Loading: () => null, LoadingMark: () => null }));
jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn(async () => {}) }));

const routes = ['home', 'wallet', 'cards', 'me', 'history', 'analysis', 'notifications', 'txndetail', 'convert', 'loan'].map(name => ({ name, key: `${name}-key` }));
const propsFor = (route: string) => ({ state: { routes, index: routes.findIndex(r => r.name === route) }, navigation: { navigate: jest.fn(), emit: jest.fn(() => ({ defaultPrevented: false })) }, descriptors: {}, insets: { top: 0, bottom: 0, left: 0, right: 0 } } as any);

beforeEach(() => jest.clearAllMocks());

describe.each([['phone', BottomNav], ['tablet', Sidebar]] as const)('%s banking navigation', (_name, Nav) => {
  it('highlights the parent of activity and returns to its main tab on press', () => {
    const props = propsFor('history');
    const tree = renderer.create(<Nav {...props} />);
    const wallet = tree.root.findByProps({ accessibilityRole: 'tab', accessibilityLabel: 'Wallet' });
    expect(wallet.props.accessibilityState.selected).toBe(true);
    act(() => wallet.props.onPress());
    expect(props.navigation.emit).toHaveBeenCalledWith({ type: 'tabPress', target: 'wallet-key', canPreventDefault: true });
    expect(props.navigation.navigate).toHaveBeenCalledWith('wallet');
    act(() => wallet.props.onLongPress());
    expect(props.navigation.emit).toHaveBeenCalledWith({ type: 'tabLongPress', target: 'wallet-key' });
    tree.unmount();
  });

  it('offers the same four tabs plus WhatsApp and respects prevented navigation', () => {
    const props = propsFor('home');
    props.navigation.emit.mockReturnValue({ defaultPrevented: true });
    const tree = renderer.create(<Nav {...props} />);
    const cards = tree.root.findByProps({ accessibilityRole: 'tab', accessibilityLabel: 'Cards' });
    act(() => cards.props.onPress());
    expect(props.navigation.navigate).not.toHaveBeenCalled();
    expect(tree.root.findAllByProps({ accessibilityRole: 'tab', accessibilityLabel: 'Convert' })).toHaveLength(0);
    act(() => tree.root.findByProps({ accessibilityRole: 'button', accessibilityLabel: 'Bank on WhatsApp' }).props.onPress());
    expect(mockPush).toHaveBeenCalledWith('/linkwhatsapp');
    tree.unmount();
  });
});

it('labels a setting switch and makes a pending update non-interactive', () => {
  const change = jest.fn();
  const tree = renderer.create(<Toggle on={false} onChange={change} accessibilityLabel="Transaction alerts" disabled />);
  const toggle = tree.root.findByProps({ accessibilityRole: 'switch' });
  expect(toggle.props.accessibilityLabel).toBe('Transaction alerts');
  expect(toggle.props.accessibilityState).toEqual({ checked: false, disabled: true });
  expect(toggle.props.onPress).toBeUndefined();
  act(() => tree.update(<Toggle on={false} onChange={change} accessibilityLabel="Transaction alerts" />));
  act(() => tree.root.findByProps({ accessibilityRole: 'switch' }).props.onPress());
  expect(change).toHaveBeenCalledWith(true);
  tree.unmount();
});

it('exposes field errors and readonly state to assistive technology', () => {
  const tree = renderer.create(<Field label="Amount" value="0" editable={false} error="Enter an amount above zero" />);
  const input = tree.root.findByType(TextInput);
  expect(input.props.accessibilityLabel).toBe('Amount');
  expect(input.props.accessibilityHint).toBe('Enter an amount above zero');
  expect(input.props.accessibilityState.disabled).toBe(true);
  expect(tree.root.findAllByProps({ accessibilityRole: 'alert' }).length).toBeGreaterThan(0);
  tree.unmount();
});

it('provides an explicit accessible sheet close action and unmounts hidden content', () => {
  const close = jest.fn();
  const tree = renderer.create(<Sheet open onClose={close} title="Choose bank"><Field label="Search bank" /></Sheet>);
  act(() => tree.root.findByProps({ accessibilityRole: 'button', accessibilityLabel: 'Close Choose bank' }).props.onPress());
  expect(close).toHaveBeenCalledTimes(1);
  act(() => tree.update(<Sheet open={false} onClose={close} title="Choose bank"><Field label="Search bank" /></Sheet>));
  expect(tree.toJSON()).toBeNull();
  tree.unmount();
});
