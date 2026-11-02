import React from 'react';
import { Platform, Text, TextProps, TextStyle, StyleProp } from 'react-native';

// iOS: Apple's system font has a cleaner ₦ than Inter, so the glyph uses it.
// Android: "sans-serif" is whatever the phone maker ships (Samsung's own font on
// Galaxy phones), and its ₦ lost the amount's weight and looked out of place,
// so on Android the ₦ stays in the surrounding font and weight.
const SYS_FONT = Platform.select({ ios: 'System', default: undefined });

const nairaStyle: TextStyle = SYS_FONT ? { fontFamily: SYS_FONT as any } : {};

export const Naira = ({ style, ...rest }: TextProps) => (
  <Text {...rest} style={[style, nairaStyle]}>₦</Text>
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
    if (i > 0) out.push(<Text key={`n-${i}`} style={[baseStyle, nairaStyle]}>₦</Text>);
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
