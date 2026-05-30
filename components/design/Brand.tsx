import React from 'react';
import { View, Text, Image, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import Svg, { Circle, Path } from 'react-native-svg';
import { font } from '@/lib/theme';

// Ribbon mark / circular badge — uses the brand PNGs from the design handoff.
export const ZMark = ({ size = 40, badge = false }: { size?: number; badge?: boolean }) => {
  if (badge) {
    return (
      <Image
        source={require('@/assets/images/zitch-badge.png')}
        style={{ width: size, height: size, borderRadius: size / 2 }}
        resizeMode="cover"
      />
    );
  }
  return (
    <Image
      source={require('@/assets/images/zitch-mark.png')}
      style={{ width: size, height: size }}
      resizeMode="contain"
    />
  );
};

export const ZWordmark = ({ size = 20, color = '#000' }: { size?: number; color?: string }) => (
  <Text style={{ fontFamily: font.bold, fontSize: size, letterSpacing: size * 0.16, color }}>
    ZITCH
  </Text>
);

// Avatar — gradient disc with a simple person silhouette (matches prototype).
export const Avatar = ({ size = 44, ring, surface = '#fff' }: { size?: number; ring?: string; surface?: string }) => {
  const ringStyle = ring
    ? { borderWidth: 2, borderColor: surface, ...StyleSheet.flatten({}) }
    : null;
  return (
    <View
      style={{
        width: size,
        height: size,
        borderRadius: size / 2,
        overflow: 'hidden',
        ...(ring
          ? { borderWidth: 4, borderColor: ring }
          : {}),
      }}
    >
      <LinearGradient
        colors={['#FFD27A', '#FF9F6B']}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={{ flex: 1, alignItems: 'center', justifyContent: 'flex-end' }}
      >
        <Svg width={size * 0.8} height={size * 0.8} viewBox="0 0 40 40">
          <Circle cx={20} cy={15} r={7} fill="#5B3A29" />
          <Path d="M6 40c0-8 6.3-13 14-13s14 5 14 13Z" fill="#7A4B33" />
        </Svg>
      </LinearGradient>
    </View>
  );
};
