import React, { useEffect, useRef } from 'react';
import { View, Text, Animated, Easing } from 'react-native';
import { useTheme, font } from '@/lib/theme';

// Direct (relative) require so the logo always resolves in the bundle,
// independent of path-alias handling — the loader's mark must never be missing.
const LOGO = require('../../assets/images/zitch-mark.png');

/**
 * Branded loading indicator — the Zitch logo gently pulsing inside a sweeping
 * brand arc, so loading is unmistakably active and on-brand (never blank).
 *
 *   <Loading />                       // full-screen, centered
 *   <Loading full={false} label="…"/> // inline block
 */
export const Loading = ({ label, full = true }: { label?: string; full?: boolean }) => {
  const { c } = useTheme();
  const pulse = useRef(new Animated.Value(0)).current;
  const spin = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const p = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 750, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 750, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
      ]),
    );
    const s = Animated.loop(
      Animated.timing(spin, { toValue: 1, duration: 1000, easing: Easing.linear, useNativeDriver: true }),
    );
    p.start();
    s.start();
    return () => { p.stop(); s.stop(); };
  }, [pulse, spin]);

  const scale = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1.06] });
  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });

  return (
    <View
      style={
        full
          ? { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 16 }
          : { alignItems: 'center', justifyContent: 'center', gap: 14, paddingVertical: 30 }
      }
    >
      <View style={{ width: 92, height: 92, alignItems: 'center', justifyContent: 'center' }}>
        {/* sweeping brand arc (two adjacent coloured borders read as a moving arc) */}
        <Animated.View
          style={{
            position: 'absolute',
            width: 92,
            height: 92,
            borderRadius: 46,
            borderWidth: 4,
            borderColor: c.line,
            borderTopColor: c.brand,
            borderRightColor: c.brand,
            transform: [{ rotate }],
          }}
        />
        {/* pulsing Zitch logo */}
        <Animated.Image source={LOGO} resizeMode="contain" style={{ width: 52, height: 52, transform: [{ scale }] }} />
      </View>
      {label ? <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.medium }}>{label}</Text> : null}
    </View>
  );
};

export default Loading;
