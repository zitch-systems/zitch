import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Animated, ScrollView, View, type NativeScrollEvent, type NativeSyntheticEvent } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import { ZCoil } from '@/components/design/Loading';
import type { CoilRefreshProps } from '@/components/design/CoilRefresh';

/** The furthest the content travels however far the finger goes. */
const PULL_MAX = 120;
/** Let go past this and it refreshes. */
const TRIGGER = 64;
/** Where the content rests while it refreshes: the coil turns in the gap. */
const HOLD = 60;
/** The finger must travel this far down before a pull starts (a tap or a sideways swipe never does). */
const SLOP = 10;

/** Harder to pull the further it goes, never past PULL_MAX. */
const rubber = (d: number) => PULL_MAX * (1 - Math.exp(-d / (PULL_MAX * 1.6)));

/**
 * Pull to refresh with the Zitch coil (17 Nov 2026), Android.
 *
 * Android's scroll view does not bounce, so the pull is a pan gesture beside the
 * scroll, never instead of it: it starts only when the list is already at the
 * top and the finger moves down; a finger that moves up first, or a list that is
 * not at the top, leaves the gesture to the scroll view untouched. The content
 * slides down under the frozen top of the screen, the coil grows in the gap, and
 * past the trigger it turns there until the screen has reloaded.
 */
export const CoilRefreshScrollView = ({ refreshing, onRefresh, children, onScroll, ...scroll }: CoilRefreshProps) => {
  const [pull] = useState(() => new Animated.Value(0));
  const [coil, setCoil] = useState(false);
  const scrollY = useRef(0);
  const startY = useRef(0);
  const pulled = useRef(0);
  const active = useRef(false);      // this touch is a pull
  const held = useRef(false);        // let go past the trigger: waiting for the reload
  const sawRefreshing = useRef(false);
  const refreshingNow = useRef(refreshing);
  const onRefreshNow = useRef(onRefresh);
  onRefreshNow.current = onRefresh;
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const settle = (to: number) => {
    pulled.current = to;
    Animated.timing(pull, { toValue: to, duration: 220, useNativeDriver: true }).start(({ finished }) => {
      if (finished && to === 0 && !held.current) setCoil(false);
    });
  };
  const release = () => {
    held.current = false;
    sawRefreshing.current = false;
    timers.current.forEach(clearTimeout);
    timers.current = [];
    settle(0);
  };

  useEffect(() => {
    refreshingNow.current = refreshing;
    if (refreshing) sawRefreshing.current = true;
    else if (held.current && sawRefreshing.current) release();
  }, [refreshing]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => { timers.current.forEach(clearTimeout); }, []);

  const native = useMemo(() => Gesture.Native(), []);
  const pan = useMemo(() => Gesture.Pan()
    .withTestId('coil-pull')
    .runOnJS(true)
    .manualActivation(true)
    .onTouchesDown((e) => { startY.current = e.allTouches[0]?.absoluteY ?? 0; })
    .onTouchesMove((e, manager) => {
      if (active.current) return;   // already a pull: onUpdate follows the finger
      const dy = (e.allTouches[0]?.absoluteY ?? startY.current) - startY.current;
      // Never while it is already reloading, and only from the very top, downwards.
      if (held.current || refreshingNow.current || scrollY.current > 1 || dy < -SLOP) manager.fail();
      else if (dy > SLOP) manager.activate();
    })
    .onStart(() => { active.current = true; setCoil(true); })
    .onUpdate((e) => {
      const to = rubber(Math.max(0, e.translationY - SLOP));
      pulled.current = to;
      pull.setValue(to);
    })
    .onEnd(() => {
      if (pulled.current < TRIGGER) { settle(0); return; }
      held.current = true;
      settle(HOLD);
      onRefreshNow.current();
      // A screen that never reports refreshing (or reports it only briefly) still lets go.
      timers.current.push(setTimeout(() => { if (held.current && !sawRefreshing.current) release(); }, 900));
      timers.current.push(setTimeout(() => { if (held.current) release(); }, 15000));
    })
    .onFinalize((_e, success) => {
      active.current = false;
      if (!success && !held.current && pulled.current > 0) settle(0);
    })
    .simultaneousWithExternalGesture(native), [native]); // eslint-disable-line react-hooks/exhaustive-deps

  const track = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    scrollY.current = e.nativeEvent.contentOffset.y;
    onScroll?.(e);
  };
  const opacity = pull.interpolate({ inputRange: [0, 20, TRIGGER], outputRange: [0, 0.25, 1], extrapolate: 'clamp' });
  const scale = pull.interpolate({ inputRange: [0, TRIGGER], outputRange: [0.6, 1], extrapolate: 'clamp' });

  return (
    <GestureDetector gesture={pan}>
      <View style={{ flex: 1 }} collapsable={false}>
        {coil ? (
          <Animated.View pointerEvents="none" importantForAccessibility="no-hide-descendants"
            style={{ position: 'absolute', top: 12, left: 0, right: 0, alignItems: 'center', opacity, transform: [{ scale }] }}>
            <ZCoil height={46} box={38} />
          </Animated.View>
        ) : null}
        <Animated.View style={{ flex: 1, transform: [{ translateY: pull }] }}>
          <GestureDetector gesture={native}>
            <ScrollView {...scroll} scrollEventThrottle={16} onScroll={track}
              // The pull is the coil's: no stretch at the top on top of it.
              overScrollMode="never">
              {children}
            </ScrollView>
          </GestureDetector>
        </Animated.View>
      </View>
    </GestureDetector>
  );
};

export default CoilRefreshScrollView;
