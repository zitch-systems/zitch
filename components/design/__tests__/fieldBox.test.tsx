import React from 'react';
import renderer, { act } from 'react-test-renderer';
import { StyleSheet, TextInput, View } from 'react-native';
import { Field, Header, HEADER_TOP, NO_WEB_OUTLINE, Screen, topGap, webOutlineOff } from '../ui';

let mockTop = 0;
jest.mock('react-native-safe-area-context', () => {
  const R = jest.requireActual('react');
  const { View: NativeView } = jest.requireActual('react-native');
  return {
    useSafeAreaInsets: () => ({ top: mockTop, bottom: 0, left: 0, right: 0 }),
    SafeAreaView: ({ children }: { children: React.ReactNode }) => R.createElement(NativeView, null, children),
  };
});
jest.mock('@/lib/screenCapture', () => ({ usePinScreenProtection: jest.fn() }));
jest.mock('@/components/design/ZIcon', () => () => null);
jest.mock('@/components/design/AmbientBackground', () => () => null);
jest.mock('@/components/design/Loading', () => ({ Loading: () => null, LoadingMark: () => null }));
jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn(async () => {}) }));

const mount = (el: React.ReactElement) => {
  let tree!: renderer.ReactTestRenderer;
  act(() => { tree = renderer.create(el); });
  return tree;
};

describe('Field: the whole box is the typing area', () => {
  it('puts the cursor in the input from a tap anywhere on the box', () => {
    const tree = mount(<Field label="Amount" value="" onChangeText={jest.fn()} placeholder="Enter amount" keyboardType="decimal-pad" />);
    const input = tree.root.findByType(TextInput);
    const focus = jest.spyOn(input.instance, 'focus').mockImplementation(() => {});
    const box = tree.root.find((n) => n.props.testID === 'field-box' && typeof n.props.onPress === 'function');
    act(() => box.props.onPress());
    expect(focus).toHaveBeenCalledTimes(1);
    expect(input.props.keyboardType).toBe('decimal-pad');
    act(() => tree.unmount());
  });

  it("keeps the caller's own ref working", () => {
    const ref = React.createRef<TextInput>();
    const tree = mount(<Field value="" onChangeText={jest.fn()} inputRef={ref} />);
    expect(ref.current).toBe(tree.root.findByType(TextInput).instance);
    act(() => tree.unmount());
  });

  it('is not a tap target when the field cannot be typed in', () => {
    const tree = mount(<Field value="Bank A" editable={false} />);
    expect(tree.root.findAll((n) => n.props.testID === 'field-box')).toHaveLength(0);
    act(() => tree.unmount());
    const picker = mount(<Field value="" pointerEvents="none" onChangeText={jest.fn()} />);
    expect(picker.root.findAll((n) => n.props.testID === 'field-box')).toHaveLength(0);
    act(() => picker.unmount());
  });

  it('draws no inner focus square on web', () => {
    // The input carries the shared no-outline style...
    const tree = mount(<Field value="" onChangeText={jest.fn()} />);
    expect(tree.root.findByType(TextInput).props.style).toContain(NO_WEB_OUTLINE);
    act(() => tree.unmount());
    // ...which on web switches the browser's own focus outline off, and on a
    // phone adds nothing.
    expect(webOutlineOff('web')).toEqual({ outlineStyle: 'none', outlineWidth: 0 });
    expect(webOutlineOff('ios')).toBeNull();
    expect(webOutlineOff('android')).toBeNull();
  });
});

describe('Screen and Header: room at the top', () => {
  it('adds a status bar of room where the device reports no top inset', () => {
    expect(topGap(0)).toBe(24);
    expect(topGap(10)).toBe(14);
    expect(topGap(47)).toBe(0);
    mockTop = 0;
    const tree = mount(<Screen><View /></Screen>);
    const gap = tree.root.find((n) => n.props.testID === 'screen-top-gap' && typeof n.type === 'string');
    expect(StyleSheet.flatten(gap.props.style).height).toBe(24);
    act(() => tree.unmount());
  });

  it('adds nothing on a phone that already has a status bar inset', () => {
    mockTop = 47;
    const tree = mount(<Screen><View /></Screen>);
    const gap = tree.root.find((n) => n.props.testID === 'screen-top-gap' && typeof n.type === 'string');
    expect(StyleSheet.flatten(gap.props.style).height).toBe(0);
    act(() => tree.unmount());
    mockTop = 0;
  });

  it('keeps the back arrow and title off the top edge', () => {
    expect(HEADER_TOP).toBe(12);
    const tree = mount(<Header title="Send money" onBack={jest.fn()} />);
    const row = tree.root.findAllByType(View)[0];
    expect(StyleSheet.flatten(row.props.style).paddingTop).toBe(12);
    act(() => tree.unmount());
  });
});
