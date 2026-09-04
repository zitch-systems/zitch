import React, { useEffect, useState } from 'react';
import { AccessibilityInfo, Animated, Easing, View, ViewStyle, StyleProp } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useTheme } from '@/lib/theme';

/**
 * Whether the OS "reduce motion" switch is on.
 *
 * Skeletons are the one loading affordance that covers the WHOLE screen, so an
 * unstoppable shimmer across every block is exactly the pattern that makes
 * vestibular-sensitive users put a phone down. Reduce motion turns the shimmer
 * off and leaves the blocks flat — still obviously placeholders, just still.
 */
export const useReduceMotion = (): boolean => {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((on) => { if (alive) setReduce(on); })
      .catch(() => { /* default to animating */ });
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduce);
    return () => { alive = false; sub.remove(); };
  }, []);
  return reduce;
};

const SHIMMER_MS = 1150;

type SkeletonProps = {
  width?: number | string;
  height?: number;
  radius?: number;
  style?: StyleProp<ViewStyle>;
};

/**
 * One placeholder block, shaped like the content it stands in for.
 *
 * WHY THIS EXISTS. Every data screen used to paint its final layout the instant
 * it mounted, with whatever the wallet context held at that moment — which on a
 * cold open is zero balance, no name, no account number and an empty activity
 * list. The customer got a fully-drawn dashboard stating they had ₦0.00, and a
 * moment later it silently became their real balance. That is worse than a blank
 * screen: a blank screen reads as "loading", but a rendered ₦0.00 reads as a
 * fact, and on a money app it is the most alarming fact there is.
 *
 * A skeleton says "this is the shape of what is coming" without ever asserting a
 * value. It also keeps the layout stable, so nothing jumps when the data lands.
 *
 * The shimmer is a translateX on a gradient band inside a clipped box: transform
 * is native-driver safe, so the animation keeps running on the UI thread while
 * JS is busy parsing the very response we are waiting for. An opacity pulse
 * driven from JS would stutter at exactly the moment it most needs not to.
 */
export const Skeleton = ({ width = '100%', height = 14, radius = 8, style }: SkeletonProps) => {
  const { c, theme } = useTheme();
  const reduceMotion = useReduceMotion();
  // useState with a lazy initialiser, not useRef().current — an Animated.Value
  // IS read during render (interpolate below), and reading a ref there is what
  // react-hooks/refs forbids. Lazy state gives the same construct-once semantics
  // without the lint violation, and is the pattern Loading.tsx already uses.
  const [shimmer] = useState(() => new Animated.Value(0));
  // The band is translated in PIXELS, so a percentage width has to be measured
  // before it can move. Until then the block renders flat, which is also exactly
  // what the first frame should look like.
  const [boxWidth, setBoxWidth] = useState(0);

  useEffect(() => {
    if (reduceMotion || boxWidth <= 0) return;
    shimmer.setValue(0);
    const loop = Animated.loop(
      Animated.timing(shimmer, {
        toValue: 1,
        duration: SHIMMER_MS,
        easing: Easing.inOut(Easing.ease),
        useNativeDriver: true,
      }),
    );
    loop.start();
    return () => loop.stop();
  }, [shimmer, reduceMotion, boxWidth]);

  const translateX = shimmer.interpolate({
    inputRange: [0, 1],
    outputRange: [-boxWidth, boxWidth],
  });

  // The highlight has to read on both grounds: a light wash on the dark theme,
  // white on the light one. Transparent at both ends so the band has no edges.
  const edge = theme === 'dark' ? 'rgba(255,255,255,0)' : 'rgba(255,255,255,0)';
  const peak = theme === 'dark' ? 'rgba(255,255,255,0.09)' : 'rgba(255,255,255,0.85)';

  return (
    <View
      onLayout={(e) => {
        const w = Math.round(e.nativeEvent.layout.width);
        // Guarded: onLayout fires on every re-measure, and setting state
        // unconditionally would restart the loop mid-shimmer on each one.
        setBoxWidth((prev) => (prev === w ? prev : w));
      }}
      accessible={false}
      // Hidden from screen readers on purpose. The container that owns the
      // loading state announces "Loading"; a dozen unlabelled blocks announcing
      // themselves individually is noise, not information.
      importantForAccessibility="no-hide-descendants"
      style={[
        {
          width: width as ViewStyle['width'],
          height,
          borderRadius: radius,
          backgroundColor: c.surface3,
          overflow: 'hidden',
        },
        style,
      ]}
    >
      {!reduceMotion && boxWidth > 0 ? (
        <Animated.View style={{ width: boxWidth, height: '100%', transform: [{ translateX }] }}>
          <LinearGradient
            colors={[edge, peak, edge]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 0 }}
            style={{ flex: 1 }}
          />
        </Animated.View>
      ) : null}
    </View>
  );
};

/**
 * A run of skeleton lines, the last one short — the shape written text actually
 * makes. Equal-length lines read as a table, not a paragraph.
 */
export const SkeletonLines = ({
  lines = 3,
  height = 12,
  gap = 8,
  lastWidth = '55%',
}: { lines?: number; height?: number; gap?: number; lastWidth?: string }) => (
  <View style={{ gap }}>
    {Array.from({ length: lines }).map((_, i) => (
      <Skeleton
        key={i}
        height={height}
        radius={height / 2}
        width={i === lines - 1 ? lastWidth : '100%'}
      />
    ))}
  </View>
);

/**
 * A placeholder transaction row, matching the real row's geometry: round icon,
 * two stacked lines, an amount on the right. Used by Home and History so the
 * list does not collapse to nothing and then push the page down when it fills.
 */
export const SkeletonRow = () => {
  const { c } = useTheme();
  return (
    <View
      accessible={false}
      importantForAccessibility="no-hide-descendants"
      style={{
        flexDirection: 'row', alignItems: 'center', gap: 12,
        paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: c.line,
      }}
    >
      <Skeleton width={40} height={40} radius={13} />
      <View style={{ flex: 1, gap: 7 }}>
        <Skeleton width="62%" height={12} radius={6} />
        <Skeleton width="38%" height={10} radius={5} />
      </View>
      <Skeleton width={68} height={13} radius={6} />
    </View>
  );
};

export default Skeleton;
