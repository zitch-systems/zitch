import React from 'react';
import { Platform, Text, TextProps, TextStyle, StyleProp } from 'react-native';

// The platform system font has a better-designed ₦ glyph than Inter on most
// devices (Apple SF on iOS, Roboto on Android). These helpers render the ₦
// symbol in the system font without disturbing the surrounding Inter text.
const SYS_FONT = Platform.select({ ios: 'System', android: 'sans-serif', default: undefined });

const nairaStyle: TextStyle = { fontFamily: SYS_FONT as any };

export const Naira = ({ style, ...rest }: TextProps) => (
  <Text {...rest} style={[nairaStyle, style]}>₦</Text>
);

// Drop-in Text replacement: splits a string child on the ₦ glyph and renders
// each ₦ inside a nested <Text> styled with the system font. Non-string
// children pass through unchanged.
type NTextProps = TextProps & { children?: React.ReactNode };

const splitNaira = (s: string, baseStyle?: StyleProp<TextStyle>) => {
  const parts = s.split('₦');
  if (parts.length === 1) return s;
  const out: React.ReactNode[] = [];
  parts.forEach((p, i) => {
    if (i > 0) out.push(<Text key={`n-${i}`} style={[nairaStyle, baseStyle]}>₦</Text>);
    if (p) out.push(p);
  });
  return out;
};

export const NText = ({ children, style, ...rest }: NTextProps) => {
  if (typeof children === 'string') {
    return <Text {...rest} style={style}>{splitNaira(children, style)}</Text>;
  }
  if (Array.isArray(children)) {
    const mapped = children.map((ch, i) =>
      typeof ch === 'string'
        ? <React.Fragment key={`s-${i}`}>{splitNaira(ch, style)}</React.Fragment>
        : ch
    );
    return <Text {...rest} style={style}>{mapped}</Text>;
  }
  return <Text {...rest} style={style}>{children}</Text>;
};
