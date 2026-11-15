/**
 * One tier as the account-limits ladder draws it: the numbered badge, "Tier N ·
 * Name", what it needs, a pill, and the per-transfer / daily limits. The Upgrade
 * to Tier 2 page uses the same row, so the two screens read the same.
 */
import React from 'react';
import { View, Text } from 'react-native';
import ZIcon from '@/components/design/ZIcon';
import { NText, money } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import { TIER_NEEDS, type LadderTier } from '@/lib/tierLadder';

export const tierAmount = (value: unknown) => (typeof value === 'string' || typeof value === 'number')
  && String(value).trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0
  ? money(Number(value)) : 'Not confirmed';

export const TierBadge = ({ tier, done, current }: { tier: number; done: boolean; current?: boolean }) => {
  const { c } = useTheme();
  return done ? (
    <View style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: current ? c.brand : '#00A01A', alignItems: 'center', justifyContent: 'center' }}>
      <ZIcon name="check" size={18} color="#fff" stroke={3} />
    </View>
  ) : (
    <View style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: c.surface, borderWidth: 2, borderStyle: 'dashed', borderColor: c.line, alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ fontSize: 14, fontFamily: font.extrabold, color: c.ink3 }}>{tier}</Text>
    </View>
  );
};

/** "Tier N · Name", what it needs, an optional pill, then the limits box. */
export const TierDetails = ({ t, done, pill }: { t: LadderTier; done: boolean; pill?: React.ReactNode }) => {
  const { c, theme } = useTheme();
  const tint = theme === 'dark' ? 'rgba(234,251,247,0.07)' : 'rgba(6,110,102,0.06)';
  return (
    <>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, minHeight: 36 }}>
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 15, fontFamily: font.extrabold, color: c.ink1 }}>Tier {t.tier} · {t.name}</Text>
          <Text style={{ fontSize: 12, marginTop: 2, fontFamily: font.regular, color: c.ink3 }}>{TIER_NEEDS[t.tier]}</Text>
        </View>
        {pill}
      </View>
      <View style={{ flexDirection: 'row', marginTop: 10, paddingVertical: 10, paddingHorizontal: 12, borderRadius: 14, backgroundColor: done ? tint : c.surface2 }}>
        {([['Per transfer', t.per_transfer], ['Daily', t.daily_transfer]] as const).map(([label, value]) => (
          <View key={label} style={{ flex: 1 }}>
            <Text style={{ fontSize: 11, fontFamily: font.semibold, color: c.ink3, letterSpacing: 0.2 }}>{label}</Text>
            <NText style={{ fontSize: 14, marginTop: 2, fontFamily: font.extrabold, color: done ? c.ink1 : c.ink2, fontVariant: ['tabular-nums'] }}>{tierAmount(value)}</NText>
          </View>
        ))}
      </View>
    </>
  );
};

/** A pill in the ladder's style: teal for your tier, green for verified, plain otherwise. */
export const TierPill = ({ label, tone }: { label: string; tone: 'current' | 'verified' | 'neutral' }) => {
  const { c } = useTheme();
  const bg = tone === 'current' ? 'rgba(6,110,102,0.12)' : tone === 'verified' ? 'rgba(0,160,26,0.10)' : c.surface2;
  const fg = tone === 'current' ? c.brand : tone === 'verified' ? '#15803D' : c.ink3;
  return (
    <View style={{ paddingVertical: 4, paddingHorizontal: 9, borderRadius: 999, backgroundColor: bg }}>
      <Text style={{ fontSize: 11.5, fontFamily: font.extrabold, color: fg }}>{label}</Text>
    </View>
  );
};
