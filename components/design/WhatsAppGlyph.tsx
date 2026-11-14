import React from 'react';
import Svg, { Path } from 'react-native-svg';
import { WHATSAPP_GLYPH_PATH, WHATSAPP_GLYPH_VIEWBOX, WHATSAPP_GREEN } from '@/lib/whatsappGlyph';

/**
 * The official WhatsApp logo, as a single source of truth so the bottom-nav
 * button, the Settings row and the Link screen stay pixel-identical. Green by
 * default; pass white where it sits on a WhatsApp-green tile.
 */
export const WhatsAppGlyph = ({ size = 26, color = WHATSAPP_GREEN }: { size?: number; color?: string }) => (
  <Svg width={size} height={size} viewBox={WHATSAPP_GLYPH_VIEWBOX}>
    <Path
      fill={color}
      fillRule="evenodd"
      clipRule="evenodd"
      d={WHATSAPP_GLYPH_PATH}
    />
  </Svg>
);

export default WhatsAppGlyph;
