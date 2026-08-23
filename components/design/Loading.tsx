import React, { useEffect, useState } from 'react';
import { View, Text, Animated, Easing, AccessibilityInfo } from 'react-native';
import { useTheme, font } from '@/lib/theme';

// Direct (relative) require so the logo always resolves in the bundle,
// independent of path-alias handling — the loader's mark must never be missing.
const LOGO = require('../../assets/images/zitch-mark.png');

/** One turn of the mark. Slow enough to read as the logo rather than a blur. */
const SPIN_MS = 1800;

/**
 * ZSpin — the Zitch mark rotating about its own vertical axis, like a coin on a
 * table. One native-driver loop, one Image: this replaced a 13-band helical coil
 * that approximated a spinning "Z" out of animated rectangles. Showing the real
 * logo is both more on-brand and dramatically cheaper — the coil ran 39 baked
 * interpolations (three per band) on every loader in the app, including the
 * inline ones that sit inside list rows.
 *
 * `perspective` is what makes it read as rotation in depth rather than a flat
 * horizontal squash; without it a rotateY just scales the image on X. It scales
 * with `size` so the foreshortening looks the same at 20px and 120px.
 *
 * Honours the OS "reduce motion" setting: vestibular-sensitive users get a still
 * mark instead of a perpetual spin, and the surrounding label still says what is
 * happening.
 */
const ZSpin = ({ size }: { size: number }) => {
  const [spin] = useState(() => new Animated.Value(0));
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((on) => { if (alive) setReduceMotion(on); })
      .catch(() => { /* default to animating */ });
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduceMotion);
    return () => { alive = false; sub.remove(); };
  }, []);

  useEffect(() => {
    if (reduceMotion) return;
    // Reset first: a loop restarted after a motion-setting change would otherwise
    // resume from wherever the value was left, jumping the mark mid-turn.
    spin.setValue(0);
    const loop = Animated.loop(
      Animated.timing(spin, {
        toValue: 1, duration: SPIN_MS, easing: Easing.linear, useNativeDriver: true,
      }),
    );
    loop.start();
    return () => loop.stop();
  }, [spin, reduceMotion]);

  const rotateY = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });

  return (
    <Animated.Image
      source={LOGO}
      resizeMode="contain"
      accessible={false}
      style={{
        width: size,
        height: size,
        transform: reduceMotion ? [] : [{ perspective: size * 8 }, { rotateY }],
      }}
    />
  );
};

/**
 * Branded loading indicator — the Zitch mark spinning on its own axis, so
 * loading is unmistakably active and on-brand (never blank).
 *
 *   <Loading />                        // full-screen splash (mark + ZITCH wordmark)
 *   <Loading label="Processing…" />    // full-screen with a status label
 *   <Loading full={false} />           // inline block (mark only)
 */
export const Loading = ({ label, full = true }: { label?: string; full?: boolean }) => {
  const { c } = useTheme();
  const size = full ? 96 : 64;

  return (
    <View
      accessible
      accessibilityRole="progressbar"
      accessibilityLabel={label || 'Loading'}
      style={
        full
          ? { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 22 }
          : { alignItems: 'center', justifyContent: 'center', gap: 16, paddingVertical: 28 }
      }
    >
      <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
        {/* Soft brand halo behind the mark. Sits under the spinning image and
            does NOT rotate, so the glow stays put while the logo turns. */}
        <View
          style={{
            position: 'absolute',
            width: size,
            height: size,
            borderRadius: size,
            backgroundColor: c.brand,
            opacity: 0.12,
          }}
        />
        <ZSpin size={size * 0.66} />
      </View>
      {label ? (
        <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.medium }}>{label}</Text>
      ) : full ? (
        // No status label → show the wordmark + tagline splash treatment.
        <View style={{ alignItems: 'center', gap: 4 }}>
          <Text style={{ fontSize: 22, fontFamily: font.extrabold, letterSpacing: 6, color: c.ink1 }}>ZITCH</Text>
          <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.medium, letterSpacing: 0.3 }}>Pay. Send. Grow.</Text>
        </View>
      ) : null}
    </View>
  );
};

/**
 * Compact inline variant — the same spinning mark, sized to sit inside a field or
 * list row (e.g. the bank auto-detect state on Send money). No ring around it any
 * more: the logo itself is the moving part, so a second spinning element was just
 * competing with it.
 */
export const LoadingMark = ({ size = 20 }: { size?: number }) => (
  <View
    accessible
    accessibilityRole="progressbar"
    accessibilityLabel="Loading"
    style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}
  >
    <ZSpin size={size} />
  </View>
);

export default Loading;
