import React, { useEffect, useRef } from 'react';
import { View, Text, Animated, Easing } from 'react-native';
import { ZMark } from '@/components/design/Brand';
import { useTheme, font } from '@/lib/theme';

/**
 * Branded loading indicator — the Zitch logo pulsing inside a rotating brand
 * ring, so a loading state is unmistakably *active* (never a blank screen) and
 * on-brand.
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
        Animated.timing(pulse, { toValue: 1, duration: 700, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 700, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
      ]),
    );
    const s = Animated.loop(
      Animated.timing(spin, { toValue: 1, duration: 1100, easing: Easing.linear, useNativeDriver: true }),
    );
    p.start();
    s.start();
    return () => { p.stop(); s.stop(); };
  }, [pulse, spin]);

  const scale = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.9, 1.08] });
  const opacity = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.72, 1] });
  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });

  return (
    <View
      style={
        full
          ? { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 16 }
          : { alignItems: 'center', justifyContent: 'center', gap: 14, paddingVertical: 30 }
      }
    >
      <View style={{ width: 86, height: 86, alignItems: 'center', justifyContent: 'center' }}>
        {/* rotating brand ring — makes the loader obviously animating */}
        <Animated.View
          style={{
            position: 'absolute',
            width: 86,
            height: 86,
            borderRadius: 43,
            borderWidth: 3,
            borderColor: c.line,
            borderTopColor: c.brand,
            transform: [{ rotate }],
          }}
        />
        {/* pulsing Zitch logo */}
        <Animated.View style={{ transform: [{ scale }], opacity }}>
          <ZMark size={52} />
        </Animated.View>
      </View>
      {label ? <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.medium }}>{label}</Text> : null}
    </View>
  );
};

export default Loading;
