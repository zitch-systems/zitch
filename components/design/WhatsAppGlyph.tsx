import React from 'react';
import Svg, { Path } from 'react-native-svg';
import { WHATSAPP_GLYPH_PATH } from '@/lib/whatsappGlyph';

/**
 * The official WhatsApp logo glyph (filled), as a single source of truth so the
 * bottom-nav button, the Settings row and the Link screen stay pixel-identical.
 */
export const WhatsAppGlyph = ({ size = 26, color = '#fff' }: { size?: number; color?: string }) => (
  <Svg width={size} height={size} viewBox="0 0 24 24">
    <Path
      fill={color}
      d={WHATSAPP_GLYPH_PATH}
    />
  </Svg>
);

export default WhatsAppGlyph;
