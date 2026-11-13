import React, { useEffect, useMemo, useState } from 'react';
import { View, Text, Animated, Easing, AccessibilityInfo, PixelRatio } from 'react-native';
import { useTheme, font } from '@/lib/theme';

// Direct (relative) require so the sprite always resolves in the bundle,
// independent of path-alias handling — the loader's mark must never be missing.
//
// The sheet is baked from the website's canvas renderer
// (landing/assets/zitch-loader.v1.js) by scripts/bake-loader-sprite.cjs, so the
// app and the web play the very same animation. Re-run that script to change it;
// the constants below must match the ones at the top of the script.
const SPRITE = require('../../assets/images/zitch-loader-sprite.png');
const FRAMES = 84;      // one 2.8s beat at 30fps
const COLS = 12;
const ROWS = 7;
const FRAME_W = 168;    // px per cell in the sheet
const FRAME_H = 256;

/** One beat of the coil: wind up → one full turn → settle and rest on the Z.
 *  Matches the web loader's default, so the two are indistinguishable. */
const BEAT_MS = 2800;

// A stepped interpolation: the value holds on each frame and jumps to the next,
// so the sheet never slides visibly between cells. Built once for every loader.
const STEP_IN: number[] = [];
const STEP_COL: number[] = [];
const STEP_ROW: number[] = [];
for (let i = 0; i < FRAMES; i++) {
  STEP_IN.push(i / FRAMES, (i + 1) / FRAMES - 1e-6);
  STEP_COL.push(i % COLS, i % COLS);
  STEP_ROW.push(Math.floor(i / COLS), Math.floor(i / COLS));
}

/**
 * ZCoil — the Zitch "Z" as a ribbon wrapped round a cylinder: it winds up, makes
 * one full turn, then settles on the logo pose, squashing and stretching like a
 * spring as it goes. This is the branded loader from the design file, frame for
 * frame.
 *
 * It plays as a sprite sheet: ONE image inside a clipped box, moved a whole cell
 * at a time by two native-driver transforms. No per-frame JS and no stack of
 * animated views — an earlier coil built from 13 animated rectangles cost 39
 * interpolations per loader and was dropped for that reason; this one costs two,
 * and keeps running smoothly while the JS thread is busy (which is exactly when
 * a loader is on screen).
 *
 * `height` is the height of one cell. The ribbon itself fills roughly the middle
 * three-quarters of it; the rest is transparent padding that keeps neighbouring
 * frames from bleeding in. `box` is the square layout footprint: the cell is
 * centred on it and may overhang (only its transparent padding does).
 *
 * Honours the OS "reduce motion" setting: vestibular-sensitive users get the
 * still Z pose (frame 0) instead of a perpetual spin, and the surrounding label
 * still says what is happening.
 */
const ZCoil = ({ height, box }: { height: number; box: number }) => {
  const [beat] = useState(() => new Animated.Value(0));
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
    // resume from wherever the value was left, jumping the coil mid-turn.
    beat.setValue(0);
    const loop = Animated.loop(
      Animated.timing(beat, {
        toValue: 1, duration: BEAT_MS, easing: Easing.linear, useNativeDriver: true,
      }),
    );
    loop.start();
    return () => loop.stop();
  }, [beat, reduceMotion]);

  // Snap the cell to whole device pixels so every frame lands on the same pixel
  // grid — a fractional cell would make the coil shimmer by a sub-pixel per frame.
  const h = PixelRatio.roundToNearestPixel(height);
  const w = PixelRatio.roundToNearestPixel((height * FRAME_W) / FRAME_H);

  const transform = useMemo(
    () => [
      { translateX: beat.interpolate({ inputRange: STEP_IN, outputRange: STEP_COL.map((col) => -col * w), extrapolate: 'clamp' }) },
      { translateY: beat.interpolate({ inputRange: STEP_IN, outputRange: STEP_ROW.map((row) => -row * h), extrapolate: 'clamp' }) },
    ],
    [beat, w, h],
  );

  return (
    <View style={{ width: box, height: box }}>
      <View style={{ position: 'absolute', left: (box - w) / 2, top: (box - h) / 2, width: w, height: h, overflow: 'hidden' }}>
        <Animated.Image
          source={SPRITE}
          accessible={false}
          fadeDuration={0}
          resizeMode="stretch"
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            width: w * COLS,
            height: h * ROWS,
            transform: reduceMotion ? [] : transform,
          }}
        />
      </View>
    </View>
  );
};

/**
 * Branded loading indicator — the Zitch coil turning on its axis, so loading is
 * unmistakably active and on-brand (never blank).
 *
 *   <Loading />                        // full-screen splash (coil + ZITCH wordmark)
 *   <Loading label="Processing…" />    // full-screen with a status label
 *   <Loading full={false} />           // inline block (coil only)
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
        {/* Soft brand halo behind the coil. It does NOT animate, so the glow stays
            put while the ribbon turns. */}
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
        {/* A cell slightly taller than the box: its transparent padding overflows,
            leaving the ribbon itself comfortably inside the halo. */}
        <ZCoil height={size * 1.1} box={size} />
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
 * Compact inline variant — the same coil, sized to sit inside a field or list row
 * (e.g. the bank auto-detect state on Send money). The cell is drawn a little
 * larger than `size` so the ribbon itself (not its transparent padding) fills the
 * box; the layout footprint is still exactly `size` × `size`.
 */
export const LoadingMark = ({ size = 20, label = 'Loading' }: { size?: number; label?: string }) => (
  <View
    accessible
    accessibilityRole="progressbar"
    accessibilityLabel={label}
    style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}
  >
    <ZCoil height={size * 1.3} box={size} />
  </View>
);

export default Loading;
