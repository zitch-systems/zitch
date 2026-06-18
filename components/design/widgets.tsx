import React from 'react';
import { View, Text, Pressable } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Image } from 'react-native';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font, radius } from '@/lib/theme';

// Section label with optional right-aligned action (e.g. "See all").
export const SectionLabel = ({ children, action, onAction }: { children: string; action?: string; onAction?: () => void }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
      <Text style={{ fontSize: 17, fontFamily: font.bold, color: c.ink1 }}>{children}</Text>
      {action && (
        <Pressable onPress={onAction}>
          <Text style={{ fontSize: 13, fontFamily: font.semibold, color: c.brand }}>{action}</Text>
        </Pressable>
      )}
    </View>
  );
};

// Brand hero gradient card with the ribbon watermark.
export const Hero = ({ children, style, watermark = 120 }: { children: React.ReactNode; style?: any; watermark?: number }) => {
  const { c } = useTheme();
  return (
    <LinearGradient
      colors={c.heroGradient}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={{
        borderRadius: 22,
        padding: 18,
        overflow: 'hidden',
        shadowColor: '#00847B',
        shadowOpacity: 0.5,
        shadowRadius: 22,
        shadowOffset: { width: 0, height: 16 },
        elevation: 6,
        ...style,
      }}
    >
      <Image
        source={require('@/assets/images/zitch-mark.png')}
        style={{ position: 'absolute', right: -18, bottom: -22, width: watermark, height: watermark, opacity: 0.18 }}
        resizeMode="contain"
      />
      {children}
    </LinearGradient>
  );
};

export const Badge = ({ label, hot }: { label: string; hot?: boolean }) => {
  const { c } = useTheme();
  return (
    <View
      style={{
        position: 'absolute',
        top: -7,
        right: -10,
        paddingHorizontal: 6,
        paddingVertical: 3,
        borderRadius: 999,
        backgroundColor: hot ? c.red : c.amber,
      }}
    >
      <Text style={{ fontSize: 9, fontFamily: font.bold, color: '#fff' }}>{label}</Text>
    </View>
  );
};

// 48px rounded icon tile used by the services grid + quick actions.
// Per-icon accent colours so each service reads as its own colourful tile
// (Opay-style) instead of one flat brand tint. Anything not listed falls back
// to the brand colour, so a new icon still looks intentional.
export const ICON_COLORS: Record<string, string> = {
  airtime: '#0FA295',
  data: '#2D7FF9',
  dice: '#F5A623',     // betting
  tv: '#7A5CFF',       // cable
  fixed: '#16A34A',    // save
  loan: '#FF3B3B',
  jamb: '#5B6CFF',     // exams
  bills: '#FB8C00',    // electricity
  send: '#0FA295',     // transfer
  withdraw: '#16A34A',
  insurance: '#00B8D4',
  remita: '#7A5CFF',
  movie: '#FF4D8D',
  convert: '#00B8D4',
  invite: '#F5A623',
  spark: '#00B51D',
  more: '#64748B',
};

// A soft translucent tint of the accent (hex + alpha) that sits cleanly on both
// light and dark surfaces, so we don't need a separate colour per theme.
const tintFor = (hex: string, dark: boolean) => hex + (dark ? '33' : '1F');

export const ServiceTile = ({
  icon,
  label,
  onPress,
  badge,
  hot,
  round,
}: {
  icon: string;
  label: string;
  onPress?: () => void;
  badge?: string;
  hot?: boolean;
  round?: boolean;
}) => {
  const { c, theme } = useTheme();
  const accent = ICON_COLORS[icon] ?? c.brand;
  return (
    <Pressable onPress={onPress} style={{ alignItems: 'center', gap: 7 }}>
      <View>
        <View
          style={{
            width: 54,
            height: 54,
            borderRadius: round ? 27 : 18,
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: tintFor(accent, theme === 'dark'),
          }}
        >
          <ZIcon name={icon} size={27} color={accent} stroke={2} />
        </View>
        {badge && <Badge label={badge} hot={hot} />}
      </View>
      <Text style={{ fontSize: 12, fontFamily: font.medium, color: c.ink2 }}>{label}</Text>
    </Pressable>
  );
};
