import React from 'react';
import renderer, { act } from 'react-test-renderer';
import { AccessibilityInfo, Animated } from 'react-native';
import BottomNav, { WIGGLE } from '../BottomNav';

jest.mock('expo-router', () => ({ router: { push: jest.fn() } }));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/WhatsAppGlyph', () => ({ WhatsAppGlyph: () => null }));
jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn(async () => {}) }));

const routes = ['home', 'wallet', 'cards', 'me'].map((name) => ({ name, key: `${name}-key` }));
const props = { state: { routes, index: 0 }, navigation: { navigate: jest.fn(), emit: jest.fn(() => ({ defaultPrevented: false })) }, descriptors: {}, insets: { top: 0, bottom: 0, left: 0, right: 0 } } as any;

/** The logo's current turn, in degrees (0 when it has no turn at all). */
const turnOf = (tree: renderer.ReactTestRenderer) => {
  const style = tree.root.findByProps({ testID: 'whatsapp-wiggle' }).props.style;
  const rotate = style?.transform?.[0]?.rotate;
  return rotate ? parseFloat(String(rotate.__getValue())) : null;
};

const mount = async () => {
  let tree!: renderer.ReactTestRenderer;
  await act(async () => { tree = renderer.create(<BottomNav {...props} />); });
  return tree;
};

describe('WhatsApp button ring wiggle', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => { jest.useRealTimers(); jest.restoreAllMocks(); });

  it('rests, then swings the logo like a ringing phone and back to level, on a loop', async () => {
    jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(false);
    const timing = jest.spyOn(Animated, 'timing');
    const delay = jest.spyOn(Animated, 'delay');
    const loop = jest.spyOn(Animated, 'loop');
    const tree = await mount();
    expect(turnOf(tree)).toBe(0);
    expect(delay).toHaveBeenLastCalledWith(WIGGLE.restMs);
    const swings = timing.mock.calls.slice(-WIGGLE.swings.length).map(([, config]) => (config as any).toValue);
    expect(swings).toEqual([...WIGGLE.swings]);
    expect(swings[0]).toBeLessThan(-10);
    expect(swings[1]).toBeGreaterThan(10);
    expect(swings[swings.length - 1]).toBe(0);
    expect(timing.mock.calls.every(([, config]) => (config as any).useNativeDriver)).toBe(true);
    expect(loop).toHaveBeenCalled();
    act(() => tree.unmount());
  });

  it('keeps the logo still when Reduce Motion is on', async () => {
    jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(true);
    const tree = await mount();
    const timing = jest.spyOn(Animated, 'timing');
    act(() => { jest.advanceTimersByTime(WIGGLE.restMs * 3); });
    expect(turnOf(tree)).toBeNull();
    expect(timing).not.toHaveBeenCalled();
    act(() => tree.unmount());
  });
});
