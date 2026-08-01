import React, { useEffect, useMemo, useState } from 'react';
import { View, Text, Animated, Easing, Image } from 'react-native';
import { useTheme, font, palette } from '@/lib/theme';

// Direct (relative) require so the logo always resolves in the bundle,
// independent of path-alias handling — the loader's mark must never be missing.
const LOGO = require('../../assets/images/zitch-mark.png');

const TWO_PI = Math.PI * 2;

/**
 * Build a seamless-loop interpolation (input 0..1) that samples
 * `fn(2π·(s + phase))` — a sine/cosine wave baked into an Animated interpolation
 * so it can run on the native driver. Output at s=0 and s=1 are equal (both land
 * on `phase`), so the loop is perfectly continuous.
 */
const wave = (
  spin: Animated.Value,
  phase: number,
  fn: (a: number) => number,
  amp: number,
  bias = 0,
) => {
  const N = 24;
  const inputRange: number[] = [];
  const outputRange: number[] = [];
  for (let k = 0; k <= N; k++) {
    const s = k / N;
    inputRange.push(s);
    outputRange.push(bias + amp * fn(TWO_PI * (s + phase)));
  }
  return spin.interpolate({ inputRange, outputRange });
};

// Coil geometry — a stack of flat teal bands wound as a helix around a cylinder.
// Rotating the whole helix reads as the Zitch "Z" tape screwing upward, matching
// the branded loading animation from design.
const SEGMENTS = 13;   // bands up the column
const TURNS = 2.3;     // how many times the tape wraps over the visible height
const RADIUS = 23;     // half-width the bands sweep across (the cylinder radius)
const BAND_W = 34;     // band width at the front of the cylinder
const BAND_H = 9;      // band thickness
const SPACING = 8.5;   // vertical gap between bands

/**
 * ZCoil — the spinning helical Z. `scale` sizes the whole coil; `color` is the
 * band tint. One native-driver loop drives every band via baked sine/cosine
 * interpolations (front bands ride wide + bright, back bands narrow + dim), so
 * it stays 60fps even as a full-screen loader.
 */
const ZCoil = ({ scale = 1, color }: { scale?: number; color: string }) => {
  const [spin] = useState(() => new Animated.Value(0));

  useEffect(() => {
    const s = Animated.loop(
      Animated.timing(spin, { toValue: 1, duration: 3400, easing: Easing.linear, useNativeDriver: true }),
    );
    s.start();
    return () => s.stop();
  }, [spin]);

  const bands = useMemo(
    () =>
      Array.from({ length: SEGMENTS }, (_, i) => {
        const phase = (i / SEGMENTS) * TURNS;                 // where this band sits on the coil
        const y = (i - (SEGMENTS - 1) / 2) * SPACING;         // static vertical position (centred)
        return {
          key: i,
          y,
          tx: wave(spin, phase, Math.sin, RADIUS),            // sweep left↔right across the cylinder
          sx: wave(spin, phase, Math.cos, 0.42, 0.58),        // 0.16 (back) … 1.0 (front) foreshorten
          op: wave(spin, phase, Math.cos, 0.34, 0.62),        // 0.28 (back) … 0.96 (front) depth fade
        };
      }),
    [spin],
  );

  const boxW = (RADIUS * 2 + BAND_W) * scale;
  const boxH = (SEGMENTS * SPACING + BAND_H) * scale;

  return (
    <View style={{ width: boxW, height: boxH, alignItems: 'center', justifyContent: 'center' }}>
      {/* soft brand glow behind the coil (the reference's blurred halo) */}
      <View
        style={{
          position: 'absolute',
          width: boxW * 0.9,
          height: boxW * 0.9,
          borderRadius: boxW,
          backgroundColor: color,
          opacity: 0.13,
        }}
      />
      <View style={{ transform: [{ scale }] }}>
        {bands.map((b) => (
          <Animated.View
            key={b.key}
            style={{
              position: 'absolute',
              left: -BAND_W / 2,
              top: b.y - BAND_H / 2,
              width: BAND_W,
              height: BAND_H,
              borderRadius: BAND_H / 2,
              backgroundColor: color,
              opacity: b.op,
              transform: [{ translateX: b.tx }, { scaleX: b.sx }],
            }}
          />
        ))}
      </View>
    </View>
  );
};

/**
 * Branded loading indicator — the Zitch "Z" as a spinning teal coil, so loading
 * is unmistakably active and on-brand (never blank). Theme-aware: a bright cyan
 * coil on dark, brand teal on light.
 *
 *   <Loading />                        // full-screen splash (coil + ZITCH wordmark)
 *   <Loading label="Processing…" />    // full-screen with a status label
 *   <Loading full={false} />           // inline block (coil only)
 */
export const Loading = ({ label, full = true }: { label?: string; full?: boolean }) => {
  const { c, theme } = useTheme();
  const coilColor = theme === 'dark' ? palette.cyan : palette.teal500;

  return (
    <View
      style={
        full
          ? { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 22 }
          : { alignItems: 'center', justifyContent: 'center', gap: 16, paddingVertical: 28 }
      }
    >
      <ZCoil scale={full ? 1 : 0.82} color={coilColor} />
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
 * Compact inline variant — a small spinning brand arc around the Zitch mark,
 * sized to sit inside a field or list row (e.g. the bank auto-detect state on
 * Send money). The coil needs room to read, so tiny inline spots keep the arc.
 */
export const LoadingMark = ({ size = 20 }: { size?: number }) => {
  const { c } = useTheme();
  const [spin] = useState(() => new Animated.Value(0));

  useEffect(() => {
    const s = Animated.loop(
      Animated.timing(spin, { toValue: 1, duration: 900, easing: Easing.linear, useNativeDriver: true }),
    );
    s.start();
    return () => s.stop();
  }, [spin]);

  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });

  return (
    <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
      <Animated.View
        style={{
          position: 'absolute',
          width: size,
          height: size,
          borderRadius: size / 2,
          borderWidth: Math.max(2, Math.round(size * 0.1)),
          borderColor: c.line,
          borderTopColor: c.brand,
          borderRightColor: c.brand,
          transform: [{ rotate }],
        }}
      />
      <Image source={LOGO} resizeMode="contain" style={{ width: size * 0.55, height: size * 0.55 }} />
    </View>
  );
};

export default Loading;
