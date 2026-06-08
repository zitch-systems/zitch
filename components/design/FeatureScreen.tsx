import React from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { Screen, Header, Card, Btn } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { Hero } from '@/components/design/widgets';
import { useTheme, font } from '@/lib/theme';

export type FeaturePoint = { icon: string; title: string; sub?: string };

/**
 * Reusable, branded screen for a product that's available to enquire about but
 * whose end-to-end flow needs a provider integration (insurance, BNPL, etc.).
 * Replaces the generic "Coming Soon" with specific, useful content plus a real
 * call-to-action — never a dead end.
 */
const FeatureScreen = ({
  title,
  icon,
  tagline,
  points,
  primaryLabel = 'Chat with us',
  onPrimary,
  primaryIcon = 'chat',
  note,
}: {
  title: string;
  icon: string;
  tagline: string;
  points: FeaturePoint[];
  primaryLabel?: string;
  onPrimary?: () => void;
  primaryIcon?: string;
  note?: string;
}) => {
  const { c } = useTheme();
  return (
    <Screen pad={false}>
      <View style={{ paddingHorizontal: 20 }}>
        <Header title={title} onBack={() => router.back()} />
      </View>

      <View style={{ paddingHorizontal: 16 }}>
        <Hero style={{ padding: 18 }}>
          <View style={{ width: 54, height: 54, borderRadius: 17, backgroundColor: 'rgba(255,255,255,.18)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name={icon} size={28} color="#fff" />
          </View>
          <Text style={{ fontSize: 19, fontFamily: font.extrabold, color: '#fff', marginTop: 14 }}>{title}</Text>
          <Text style={{ fontSize: 13.5, color: 'rgba(255,255,255,.88)', marginTop: 6, lineHeight: 20, fontFamily: font.regular }}>{tagline}</Text>
        </Hero>

        <Card style={{ marginTop: 14 }} pad={0}>
          <View style={{ paddingHorizontal: 16, paddingVertical: 4 }}>
            {points.map((p, i) => (
              <View key={p.title} style={{ flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 13, borderBottomWidth: i === points.length - 1 ? 0 : 1, borderBottomColor: c.line }}>
                <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
                  <ZIcon name={p.icon} size={20} color={c.brand} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontFamily: font.semibold, color: c.ink1 }}>{p.title}</Text>
                  {p.sub ? <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 1, fontFamily: font.regular }}>{p.sub}</Text> : null}
                </View>
              </View>
            ))}
          </View>
        </Card>

        <View style={{ marginTop: 18 }}>
          <Btn label={primaryLabel} icon={primaryIcon} onPress={onPrimary ?? (() => router.push('/support'))} />
        </View>

        {note ? (
          <Text style={{ fontSize: 12, color: c.ink3, textAlign: 'center', marginTop: 14, paddingHorizontal: 16, fontFamily: font.regular }}>{note}</Text>
        ) : null}
      </View>
    </Screen>
  );
};

export default FeatureScreen;
