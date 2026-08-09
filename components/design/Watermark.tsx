import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { font, useTheme } from '@/lib/theme';

/**
 * Faint tiled "ZITCH" watermark for the receipt card.
 *
 * Sits inside the view that gets captured, so it lands on the saved and shared
 * JPEG as well as on screen — the whole point is that a forwarded screenshot
 * still carries the mark. It is drawn OVER the rows rather than under them so a
 * crop cannot lift a clean region out of the middle, and kept very faint for the
 * same reason the server-side receipt is: a watermark that fights the amount
 * makes the artifact worse, and the reference is what actually proves a payment.
 *
 * `pointerEvents="none"` so it never intercepts a tap meant for the card.
 */
const ROWS = 14;
const WORD = 'ZITCH   •   ';

const Watermark = ({ opacity = 0.05 }: { opacity?: number }) => {
  const { c } = useTheme();
  return (
    <View
      pointerEvents="none"
      // Clipped to the card: the rows are deliberately wider and taller than the
      // area so the rotation leaves no bare corner.
      style={[StyleSheet.absoluteFill, { overflow: 'hidden', alignItems: 'center', justifyContent: 'center' }]}
    >
      <View style={{ transform: [{ rotate: '-30deg' }], opacity }}>
        {Array.from({ length: ROWS }).map((_, i) => (
          <Text
            key={i}
            numberOfLines={1}
            style={{
              fontSize: 22,
              fontFamily: font.extrabold,
              color: c.brand,
              letterSpacing: 2,
              // Alternate rows are nudged so the tiling reads as a texture
              // rather than a grid of stacked words.
              marginLeft: i % 2 ? 46 : 0,
              marginBottom: 26,
            }}
          >
            {WORD.repeat(6)}
          </Text>
        ))}
      </View>
    </View>
  );
};

export default Watermark;
