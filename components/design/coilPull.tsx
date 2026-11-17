import React, { useEffect, useRef, useState } from 'react';
import { Animated, Platform, View } from 'react-native';
import { ZCoil } from '@/components/design/Loading';

/**
 * The pull that refreshes Home, Wallet and Cards with the Zitch coil, shared by
 * every platform (17 Nov 2026: "dragging down … should reload the page, it should
 * not affect the scrolling").
 *
 * A pull may start anywhere on the screen: on the part at the top that never
 * scrolls (Home's greeting, balance and quick actions; Wallet's title and card),
 * or on the scrolling part once it is at its very top. It starts only after the
 * finger has gone SLOP points DOWN; a tap, a sideways swipe or a finger that goes
 * up first is left alone, so scrolling never changes.
 */

/** The furthest the content travels however far the finger goes. */
export const PULL_MAX = 120;
/** Let go past this and it refreshes. */
export const TRIGGER = 64;
/** Where the content rests while it refreshes: the coil turns in the gap. */
export const HOLD = 60;
/** The finger must travel this far down before a pull starts. */
export const SLOP = 10;

/** Harder to pull the further it goes, never past PULL_MAX. */
export const rubber = (d: number) => PULL_MAX * (1 - Math.exp(-d / (PULL_MAX * 1.6)));

/**
 * Whether a finger that has moved `dy` points (down is positive) becomes a pull.
 * 'pull' starts one, 'scroll' leaves this touch to scrolling for good, 'wait'
 * decides on a later move. On the scrolling part a pull needs the list at its top;
 * on the still part at the top nothing scrolls, so any downward drag is a pull.
 */
export const decide = (dy: number, { onTop, scrollY, busy }: { onTop: boolean; scrollY: number; busy: boolean }): 'pull' | 'scroll' | 'wait' => {
  if (busy) return 'scroll';
  if (dy < -SLOP) return 'scroll';
  if (!onTop && scrollY > 1) return 'scroll';
  if (dy > SLOP) return 'pull';
  return 'wait';
};

/**
 * The pull's state: how far the content has moved, whether the coil shows, and
 * the hold while the screen reloads. Let go past TRIGGER and onRefresh runs; the
 * content waits at HOLD until `refreshing` has been true and gone false again (or
 * briefly, for a screen that never reports it), then slides back.
 */
export const usePull = (refreshing: boolean, onRefresh: () => void) => {
  const [pull] = useState(() => new Animated.Value(0));
  const [coil, setCoil] = useState(false);
  const pulled = useRef(0);
  const held = useRef(false);
  const sawRefreshing = useRef(false);
  const refreshingNow = useRef(refreshing);
  const onRefreshNow = useRef(onRefresh);
  onRefreshNow.current = onRefresh;
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const mounted = useRef(true);

  const settle = (to: number) => {
    pulled.current = to;
    Animated.timing(pull, { toValue: to, duration: 220, useNativeDriver: Platform.OS !== 'web' }).start(({ finished }) => {
      if (finished && to === 0 && !held.current && mounted.current) setCoil(false);
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

  useEffect(() => () => { mounted.current = false; timers.current.forEach(clearTimeout); }, []);

  return {
    pull,
    coil,
    /** reloading now: no new pull starts */
    busy: () => held.current || refreshingNow.current,
    begin: () => setCoil(true),
    /** follow the finger: `dy` is how far it has gone down since the touch began */
    move: (dy: number) => {
      const to = rubber(Math.max(0, dy - SLOP));
      pulled.current = to;
      pull.setValue(to);
    },
    /** the finger is up: refresh if pulled far enough, else spring back */
    end: () => {
      if (pulled.current < TRIGGER) { settle(0); return false; }
      held.current = true;
      settle(HOLD);
      onRefreshNow.current();
      timers.current.push(setTimeout(() => { if (held.current && !sawRefreshing.current) release(); }, 900));
      timers.current.push(setTimeout(() => { if (held.current) release(); }, 15000));
      return true;
    },
    /** the touch was taken away: spring back unless it is already reloading */
    cancel: () => { if (!held.current && pulled.current > 0) settle(0); },
  };
};

/** The coil in the gap the pull opens above the scrolling part. */
export const PullCoil = ({ pull }: { pull: Animated.Value }) => {
  const opacity = pull.interpolate({ inputRange: [0, 20, TRIGGER], outputRange: [0, 0.25, 1], extrapolate: 'clamp' });
  const scale = pull.interpolate({ inputRange: [0, TRIGGER], outputRange: [0.6, 1], extrapolate: 'clamp' });
  return (
    <Animated.View pointerEvents="none" importantForAccessibility="no-hide-descendants" accessibilityElementsHidden testID="pull-coil"
      style={{ position: 'absolute', top: 12, left: 0, right: 0, alignItems: 'center', opacity, transform: [{ scale }] }}>
      <ZCoil height={46} box={38} />
    </Animated.View>
  );
};

/** The still part at the top, measured so a touch can tell whether it began there. */
export const TopPart = ({ top, onHeight }: { top?: React.ReactNode; onHeight: (h: number) => void }) =>
  top ? <View collapsable={false} onLayout={(e) => onHeight(e.nativeEvent.layout.height)}>{top}</View> : null;
