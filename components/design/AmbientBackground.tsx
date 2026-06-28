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
  // Opacities mirror the `--bg-grad` tokens (brand glow top-right, cyan glow
  // bottom-left) — subtle on light, a touch stronger on dark, per tokens.css.
  const brandA = theme === 'dark' ? 0.18 : 0.1;
  const cyanA = theme === 'dark' ? 0.1 : 0.12;

  return (
    <View pointerEvents="none" style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}>
      <Svg width={width} height={height}>
        <Defs>
          <RadialGradient id="zGlowTop" cx="50%" cy="50%" r="50%">
            <Stop offset="0" stopColor={c.brand} stopOpacity={brandA} />
            <Stop offset="1" stopColor={c.brand} stopOpacity={0} />
          </RadialGradient>
          <RadialGradient id="zGlowBottom" cx="50%" cy="50%" r="50%">
            <Stop offset="0" stopColor={c.cyan} stopOpacity={cyanA} />
            <Stop offset="1" stopColor={c.cyan} stopOpacity={0} />
          </RadialGradient>
        </Defs>
        {/* top-right brand glow (token: 50% 40% at 88% 2%) */}
        <Circle cx={width * 0.88} cy={height * 0.02} r={width * 0.62} fill="url(#zGlowTop)" />
        {/* bottom-left cyan glow (token: 48% 42% at 2% 100%) */}
        <Circle cx={width * 0.02} cy={height * 1.0} r={width * 0.66} fill="url(#zGlowBottom)" />
      </Svg>
    </View>
  );
};

export default AmbientBackground;
