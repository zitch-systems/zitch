import React from 'react';
import { RefreshControl, Text } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import { CoilRefreshScrollView } from '@/components/design/CoilRefresh';

// Pull to refresh with the Zitch coil (17 Nov 2026). iOS: the system's own pull and
// timing, its spinner hidden, the coil in the gap.
jest.mock('@/components/design/Loading', () => {
  const { View } = jest.requireActual<typeof import('react-native')>('react-native');
  const ReactActual = jest.requireActual<typeof import('react')>('react');
  return { ZCoil: () => ReactActual.createElement(View, { testID: 'zitch-coil' }) };
});

const render = (refreshing: boolean, onRefresh = jest.fn()) => {
  let tree!: renderer.ReactTestRenderer;
  act(() => {
    tree = renderer.create(
      <CoilRefreshScrollView refreshing={refreshing} onRefresh={onRefresh}><Text>Pay a bill</Text></CoilRefreshScrollView>,
    );
  });
  return tree;
};
const coils = (tree: renderer.ReactTestRenderer) => tree.root.findAll((n) => n.props.testID === 'zitch-coil' && typeof n.type !== 'string');

it('hides the system spinner and keeps its pull, so scrolling is the system\'s own', () => {
  const onRefresh = jest.fn();
  const tree = render(false, onRefresh);
  const control = tree.root.findByType(RefreshControl);
  expect(control.props.tintColor).toBe('transparent');
  expect(control.props.colors).toEqual(['transparent']);
  act(() => control.props.onRefresh());
  expect(onRefresh).toHaveBeenCalledTimes(1);
  act(() => tree.unmount());
});

it('shows the coil only while pulled down or refreshing', () => {
  const tree = render(false);
  expect(coils(tree)).toHaveLength(0);
  const scroll = tree.root.findByType(RefreshControl).parent!;
  const onScroll = tree.root.findAll((n) => typeof n.props.onScroll === 'function')[0].props.onScroll;
  act(() => onScroll({ nativeEvent: { contentOffset: { x: 0, y: -40 } } }));
  expect(coils(tree).length).toBeGreaterThan(0);
  act(() => onScroll({ nativeEvent: { contentOffset: { x: 0, y: 120 } } }));
  expect(coils(tree)).toHaveLength(0);
  expect(scroll).toBeTruthy();
  act(() => tree.update(<CoilRefreshScrollView refreshing onRefresh={jest.fn()}><Text>Pay a bill</Text></CoilRefreshScrollView>));
  expect(coils(tree).length).toBeGreaterThan(0);
  act(() => tree.unmount());
});
