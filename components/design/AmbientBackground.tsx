import React from 'react';
import { View, useWindowDimensions } from 'react-native';
import Svg, { Defs, RadialGradient, Stop, Circle } from 'react-native-svg';
import { useTheme } from '@/lib/theme';

/**
 * AmbientBackground — soft, theme-aware radial glows that sit *behind* every
 * screen's content (rendered by <Screen/>). Replaces the old flat linear
 * gradient look with subtle depth: a warm brand glow top-right and a cyan glow
 * bottom-left, fading to fully transparent so the base gradient still shows.
 *
 * Rendered with pointerEvents="none" and absolute fill, so it never intercepts
 * touches and never affects layout. SVG radial gradients give a genuine soft
 * falloff (a plain low-opacity circle would read as a hard disc).
 */
const AmbientBackground = () => {
  const { c, theme } = useTheme();
  const { width, height } = useWindowDimensions();
  // Glows are a touch stronger on dark so the depth reads; gentle on light.
  const a = theme === 'dark' ? 0.45 : 0.5;

  return (
    <View pointerEvents="none" style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}>
      <Svg width={width} height={height}>
        <Defs>
          <RadialGradient id="zGlowTop" cx="50%" cy="50%" r="50%">
            <Stop offset="0" stopColor={c.brand} stopOpacity={a} />
            <Stop offset="1" stopColor={c.brand} stopOpacity={0} />
          </RadialGradient>
          <RadialGradient id="zGlowBottom" cx="50%" cy="50%" r="50%">
            <Stop offset="0" stopColor={c.cyan} stopOpacity={theme === 'dark' ? 0.22 : 0.3} />
            <Stop offset="1" stopColor={c.cyan} stopOpacity={0} />
          </RadialGradient>
          <RadialGradient id="zGlowMid" cx="50%" cy="50%" r="50%">
            <Stop offset="0" stopColor={c.brandDeep} stopOpacity={theme === 'dark' ? 0.18 : 0.14} />
            <Stop offset="1" stopColor={c.brandDeep} stopOpacity={0} />
          </RadialGradient>
        </Defs>
        {/* top-right brand glow */}
        <Circle cx={width * 0.92} cy={height * 0.06} r={width * 0.7} fill="url(#zGlowTop)" />
        {/* bottom-left cyan glow */}
        <Circle cx={width * 0.05} cy={height * 0.9} r={width * 0.75} fill="url(#zGlowBottom)" />
        {/* faint mid-left deep teal for body */}
        <Circle cx={width * 0.1} cy={height * 0.38} r={width * 0.55} fill="url(#zGlowMid)" />
      </Svg>
    </View>
  );
};

export default AmbientBackground;
