import React from 'react';
import { View, Text, Image } from 'react-native';
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
export const Avatar = ({ size = 44, ring, surface = '#fff', uri }: { size?: number; ring?: string; surface?: string; uri?: string }) => {
  // Show the user's uploaded photo when present; otherwise the default illustration.
  const content = uri ? (
    <Image source={{ uri }} style={{ width: '100%', height: '100%' }} resizeMode="cover" />
  ) : (
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
  );

  const disc = (br: number) => (
    <View style={{ flex: 1, borderRadius: br, overflow: 'hidden' }}>{content}</View>
  );

  // Ringed: a brand-coloured outer ring with a surface-coloured gap between it
  // and the disc (a "story ring"). The 2px ring + 2px surface gap keep the outer
  // footprint at exactly `size`, so ringed and un-ringed avatars align. Without a
  // ring, the plain disc fills the whole `size`.
  if (ring) {
    return (
      <View style={{ width: size, height: size, borderRadius: size / 2, borderWidth: 2, borderColor: ring, backgroundColor: surface, padding: 2 }}>
        {disc((size - 8) / 2)}
      </View>
    );
  }
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, overflow: 'hidden' }}>{content}</View>
  );
};
