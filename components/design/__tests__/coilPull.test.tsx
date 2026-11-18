import React from 'react';
import { Text } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import 'react-native-gesture-handler/jestSetup';
import { fireGestureHandler, getByGestureTestId } from 'react-native-gesture-handler/jest-utils';
import { State } from 'react-native-gesture-handler';
import { decide, SLOP } from '@/components/design/coilPull';
import { CoilRefreshScrollView } from '@/components/design/CoilRefresh';

// 17 Nov 2026: "dragging down on the home wallet and card screen should reload the
// page, it should not affect the scrolling".
jest.mock('@/components/design/Loading', () => ({ ZCoil: () => null }));

describe('when a drag becomes a pull', () => {
  it('pulls from the still part at the top whatever the list is doing', () => {
    expect(decide(SLOP + 1, { onTop: true, scrollY: 400, busy: false })).toBe('pull');
  });
  it('pulls on the list only at its very top', () => {
    expect(decide(SLOP + 1, { onTop: false, scrollY: 0, busy: false })).toBe('pull');
    expect(decide(SLOP + 1, { onTop: false, scrollY: 40, busy: false })).toBe('scroll');
  });
  it('leaves a finger that goes up first, or barely moves, to scrolling and taps', () => {
    expect(decide(-(SLOP + 1), { onTop: false, scrollY: 0, busy: false })).toBe('scroll');
    expect(decide(-(SLOP + 1), { onTop: true, scrollY: 0, busy: false })).toBe('scroll');
    expect(decide(SLOP - 2, { onTop: true, scrollY: 0, busy: false })).toBe('wait');
  });
  it('does not start a second pull while the screen is reloading', () => {
    expect(decide(80, { onTop: true, scrollY: 0, busy: true })).toBe('scroll');
  });
});

const pull = (id: string, dy: number) => fireGestureHandler(getByGestureTestId(id), [
  { state: State.BEGAN, translationY: 0 },
  { state: State.ACTIVE, translationY: dy / 2 },
  { state: State.ACTIVE, translationY: dy },
  { state: State.END, translationY: dy },
]);

it('iOS: a long drag down on the still top refreshes, a short one does not', () => {
  const onRefresh = jest.fn();
  let tree!: renderer.ReactTestRenderer;
  act(() => {
    tree = renderer.create(
      <CoilRefreshScrollView refreshing={false} onRefresh={onRefresh} top={<Text>Good morning</Text>}><Text>Pay a bill</Text></CoilRefreshScrollView>,
    );
  });
  act(() => { pull('coil-pull-top', 40); });
  expect(onRefresh).not.toHaveBeenCalled();
  act(() => { pull('coil-pull-top', 260); });
  expect(onRefresh).toHaveBeenCalledTimes(1);
  act(() => tree.unmount());
});

it('Android: the still top sits inside the pull, and a long pull refreshes', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { CoilRefreshScrollView: AndroidPull } = require('@/components/design/CoilRefresh.android');
  const onRefresh = jest.fn();
  let tree!: renderer.ReactTestRenderer;
  act(() => {
    tree = renderer.create(
      <AndroidPull refreshing={false} onRefresh={onRefresh} top={<Text>Wallet</Text>}><Text>Recent activity</Text></AndroidPull>,
    );
  });
  expect(JSON.stringify(tree.toJSON())).toContain('Wallet');
  act(() => { pull('coil-pull', 260); });
  expect(onRefresh).toHaveBeenCalledTimes(1);
  act(() => tree.unmount());
});
