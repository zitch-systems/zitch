import React, { useEffect, useRef } from 'react';
import { View, Text, Animated, Easing } from 'react-native';
import { ZMark } from '@/components/design/Brand';
import { useTheme, font } from '@/lib/theme';

/**
 * Branded loading indicator — a gently pulsing Zitch mark with an optional
 * label. Use instead of a bare ActivityIndicator (or a blank screen) so every
 * loading state feels intentional and on-brand.
 *
 *   <Loading />                       // full-screen, centered
 *   <Loading full={false} label="…"/> // inline block
 */
export const Loading = ({ label, full = true }: { label?: string; full?: boolean }) => {
  const { c } = useTheme();
  const a = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(a, { toValue: 1, duration: 700, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
        Animated.timing(a, { toValue: 0, duration: 700, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [a]);

  const scale = a.interpolate({ inputRange: [0, 1], outputRange: [0.85, 1.12] });
  const opacity = a.interpolate({ inputRange: [0, 1], outputRange: [0.55, 1] });

  return (
    <View
      style={
        full
          ? { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 14 }
          : { alignItems: 'center', justifyContent: 'center', gap: 12, paddingVertical: 26 }
      }
    >
      <Animated.View style={{ transform: [{ scale }], opacity }}>
        <ZMark size={44} />
      </Animated.View>
      {label ? <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.medium }}>{label}</Text> : null}
    </View>
  );
};

export default Loading;
