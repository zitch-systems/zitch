import React, { useMemo, useRef } from 'react';
import { Animated, ScrollView, View, type NativeScrollEvent, type NativeSyntheticEvent } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import type { CoilRefreshProps } from '@/components/design/CoilRefresh';
import { decide, PullCoil, TopPart, usePull } from '@/components/design/coilPull';

export { HOLD, PULL_MAX, TRIGGER } from '@/components/design/coilPull';

/**
 * Pull to refresh with the Zitch coil, Android.
 *
 * Android's scroll view does not bounce, so the pull is a pan gesture beside the
 * scroll, never instead of it. It covers the whole screen: the still part at the
 * top (`top`) and the scrolling part. On the still part any downward drag is a
 * pull; on the scrolling part only once the list is at its top. A finger that
 * moves up first, or a list that is not at the top, leaves the touch to the
 * scroll view untouched. The scrolling part slides down under the still part,
 * the coil grows in the gap, and past the trigger it turns there until the
 * screen has reloaded.
 */
export const CoilRefreshScrollView = ({ refreshing, onRefresh, children, onScroll, top, ...scroll }: CoilRefreshProps) => {
  const p = usePull(refreshing, onRefresh);
  const scrollY = useRef(0);
  const startY = useRef(0);
  const onTop = useRef(false);
  const topH = useRef(0);
  const active = useRef(false);

  const native = useMemo(() => Gesture.Native(), []);
  const pan = useMemo(() => Gesture.Pan()
    .withTestId('coil-pull')
    .runOnJS(true)
    .manualActivation(true)
    .onTouchesDown((e) => {
      const t = e.allTouches[0];
      startY.current = t?.absoluteY ?? 0;
      // `y` is relative to this view, whose first child is the still part.
      onTop.current = !!top && (t?.y ?? Infinity) < topH.current;
    })
    .onTouchesMove((e, manager) => {
      if (active.current) return;   // already a pull: onUpdate follows the finger
      const dy = (e.allTouches[0]?.absoluteY ?? startY.current) - startY.current;
      const d = decide(dy, { onTop: onTop.current, scrollY: scrollY.current, busy: p.busy() });
      if (d === 'scroll') manager.fail();
      else if (d === 'pull') manager.activate();
    })
    .onStart(() => { active.current = true; p.begin(); })
    .onUpdate((e) => p.move(e.translationY))
    .onEnd(() => { p.end(); })
    .onFinalize((_e, success) => {
      active.current = false;
      if (!success) p.cancel();
    })
    .simultaneousWithExternalGesture(native), [native, top]); // eslint-disable-line react-hooks/exhaustive-deps

  const track = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    scrollY.current = e.nativeEvent.contentOffset.y;
    onScroll?.(e);
  };

  return (
    <GestureDetector gesture={pan}>
      <View style={{ flex: 1 }} collapsable={false}>
        <TopPart top={top} onHeight={(h) => { topH.current = h; }} />
        <View style={{ flex: 1 }}>
          {p.coil ? <PullCoil pull={p.pull} /> : null}
          <Animated.View style={{ flex: 1, transform: [{ translateY: p.pull }] }}>
            <GestureDetector gesture={native}>
              <ScrollView {...scroll} scrollEventThrottle={16} onScroll={track}
                // The pull is the coil's: no stretch at the top on top of it.
                overScrollMode="never">
                {children}
              </ScrollView>
            </GestureDetector>
          </Animated.View>
        </View>
      </View>
    </GestureDetector>
  );
};

export default CoilRefreshScrollView;
