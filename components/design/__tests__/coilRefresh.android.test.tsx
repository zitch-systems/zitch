import React from 'react';
import { Text } from 'react-native';
import renderer, { act } from 'react-test-renderer';
import 'react-native-gesture-handler/jestSetup';
import { fireGestureHandler, getByGestureTestId } from 'react-native-gesture-handler/jest-utils';
import { State } from 'react-native-gesture-handler';

// Android: the pull is a gesture beside the scroll. Let go past the trigger and it
// refreshes; a short pull springs back without refreshing.
jest.mock('@/components/design/Loading', () => ({ ZCoil: () => null }));
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { CoilRefreshScrollView } = require('@/components/design/CoilRefresh.android');

const pull = (dy: number) => fireGestureHandler(getByGestureTestId('coil-pull'), [
  { state: State.BEGAN, translationY: 0 },
  { state: State.ACTIVE, translationY: dy / 2 },
  { state: State.ACTIVE, translationY: dy },
  { state: State.END, translationY: dy },
]);

it('refreshes after a long pull and not after a short one', () => {
  const onRefresh = jest.fn();
  let tree!: renderer.ReactTestRenderer;
  act(() => { tree = renderer.create(<CoilRefreshScrollView refreshing={false} onRefresh={onRefresh}><Text>Pay a bill</Text></CoilRefreshScrollView>); });
  act(() => { pull(40); });
  expect(onRefresh).not.toHaveBeenCalled();
  act(() => { pull(260); });
  expect(onRefresh).toHaveBeenCalledTimes(1);
  act(() => tree.unmount());
});
