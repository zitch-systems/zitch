import React, { useMemo, useRef, useState } from 'react';
import { Animated, RefreshControl, View, type NativeScrollEvent, type NativeSyntheticEvent, type ScrollViewProps } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import { ZCoil } from '@/components/design/Loading';
import { decide, PullCoil, TopPart, usePull } from '@/components/design/coilPull';

export type CoilRefreshProps = ScrollViewProps & {
  refreshing: boolean;
  onRefresh: () => void;
  children: React.ReactNode;
  /** The still part above the scroll (Home's greeting, balance and quick actions;
   *  Wallet's title and card). A downward drag on it refreshes too (17 Nov 2026). */
  top?: React.ReactNode;
};

/** How far the content is pulled, in points, before the coil is fully in. */
export const COIL_FULL = 72;

/**
 * Pull to refresh with the Zitch coil, iOS. (Android and the web have their own
 * files: CoilRefresh.android.tsx and CoilRefresh.web.tsx.)
 *
 * On the scrolling part iOS keeps the system's own pull: the scroll view bounces,
 * UIRefreshControl decides when it has been pulled far enough and holds the gap
 * while it loads. Only its spinner is hidden; the coil sits in that gap instead.
 * Scrolling is exactly the system's.
 *
 * The still part at the top never scrolls, so the system has no pull there. A
 * downward drag on it (17 Nov 2026: "dragging down … should reload the page")
 * moves the scrolling part down the same way and refreshes on letting go past
 * the trigger. A tap or a sideways or upward move there is left alone.
 */
export const CoilRefreshScrollView = ({ refreshing, onRefresh, children, onScroll, top, ...scroll }: CoilRefreshProps) => {
  const [offset] = useState(() => new Animated.Value(0));
  const [pulling, setPulling] = useState(false);
  const pullingNow = useRef(false);
  const p = usePull(refreshing, onRefresh);
  const startY = useRef(0);
  const active = useRef(false);

  const topPan = useMemo(() => Gesture.Pan()
    .withTestId('coil-pull-top')
    .runOnJS(true)
    .manualActivation(true)
    .onTouchesDown((e) => { startY.current = e.allTouches[0]?.absoluteY ?? 0; })
    .onTouchesMove((e, manager) => {
      if (active.current) return;
      const dy = (e.allTouches[0]?.absoluteY ?? startY.current) - startY.current;
      const d = decide(dy, { onTop: true, scrollY: 0, busy: p.busy() || pullingNow.current });
      if (d === 'scroll') manager.fail();
      else if (d === 'pull') manager.activate();
    })
    .onStart(() => { active.current = true; p.begin(); })
    .onUpdate((e) => p.move(e.translationY))
    .onEnd(() => { p.end(); })
    .onFinalize((_e, success) => { active.current = false; if (!success) p.cancel(); }), []); // eslint-disable-line react-hooks/exhaustive-deps

  const track = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const pulled = e.nativeEvent.contentOffset.y < -2;
    if (pulled !== pullingNow.current) { pullingNow.current = pulled; setPulling(pulled); }
    onScroll?.(e);
  };
  const opacity = offset.interpolate({ inputRange: [-COIL_FULL + 16, -16, 0], outputRange: [1, 0.2, 0], extrapolate: 'clamp' });
  const scale = offset.interpolate({ inputRange: [-COIL_FULL - 8, -16, 0], outputRange: [1, 0.6, 0.6], extrapolate: 'clamp' });

  return (
    <View style={{ flex: 1 }}>
      {top ? <GestureDetector gesture={topPan}><View collapsable={false}><TopPart top={top} onHeight={() => {}} /></View></GestureDetector> : null}
      <View style={{ flex: 1 }}>
        {/* Behind the content: it shows in the gap the pull opens above it. */}
        {p.coil ? <PullCoil pull={p.pull} /> : (pulling || refreshing) ? (
          <Animated.View pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants"
            style={{ position: 'absolute', top: 12, left: 0, right: 0, alignItems: 'center', opacity, transform: [{ scale }] }}>
            <ZCoil height={46} box={38} />
          </Animated.View>
        ) : null}
        <Animated.View style={{ flex: 1, transform: [{ translateY: p.pull }] }}>
          <Animated.ScrollView
            {...scroll}
            scrollEventThrottle={16}
            onScroll={Animated.event([{ nativeEvent: { contentOffset: { y: offset } } }], { useNativeDriver: true, listener: track })}
            refreshControl={
              // The system's pull and timing, without its spinner: the coil is the indicator.
              // While the still part's pull holds the gap, the system's stays out of it.
              <RefreshControl refreshing={refreshing && !p.coil} onRefresh={onRefresh} tintColor="transparent"
                colors={['transparent']} progressBackgroundColor="transparent" accessibilityLabel={refreshing ? 'Refreshing' : undefined} />
            }
          >
            {children}
          </Animated.ScrollView>
        </Animated.View>
      </View>
    </View>
  );
};

export default CoilRefreshScrollView;
