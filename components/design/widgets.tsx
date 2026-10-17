import React from 'react';
import { View, Text } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Image } from 'react-native';
import ZIcon from '@/components/design/ZIcon';
import { Tap } from '@/components/design/ui';
import { useTheme, font, radius, ICON_COLORS, iconTint } from '@/lib/theme';

// Section label with optional right-aligned action (e.g. "See all").
export const SectionLabel = ({ children, action, onAction }: { children: string; action?: string; onAction?: () => void }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 12 }}>
      <Text accessibilityRole="header" style={{ flex: 1, fontSize: 17, fontFamily: font.bold, color: c.ink1 }}>{children}</Text>
      {action && (
        <Tap onPress={onAction} accessibilityLabel={action} style={{ minHeight: 44, justifyContent: 'center', flexShrink: 0 }}>
          <Text style={{ fontSize: 13, fontFamily: font.semibold, color: c.brand }}>{action}</Text>
        </Tap>
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
  const { c, theme } = useTheme();
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
      <Text style={{ fontSize: 10, fontFamily: font.bold, color: theme === 'dark' ? c.bg : '#fff' }}>{label}</Text>
    </View>
  );
};

// 48px rounded icon tile used by the services grid + quick actions.
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
    <Tap onPress={onPress} accessibilityLabel={[label, badge].filter(Boolean).join(', ')} style={{ alignItems: 'center', gap: 7, minHeight: 76, maxWidth: '100%', flexShrink: 1, paddingHorizontal: 3 }}>
      <View>
        <View
          style={{
            width: 48,
            height: 48,
            borderRadius: round ? 24 : 16,
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: iconTint(accent, theme === 'dark'),
          }}
        >
          <ZIcon name={icon} size={24} color={accent} stroke={2} />
        </View>
        {badge && <Badge label={badge} hot={hot} />}
      </View>
      <Text style={{ fontSize: 12, lineHeight: 18, fontFamily: font.medium, color: c.ink2, textAlign: 'center', flexShrink: 1 }}>{label}</Text>
    </Tap>
  );
};
