import React, { useCallback, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { LinearGradient } from 'expo-linear-gradient';
import { router, useFocusEffect } from 'expo-router';
import { Screen, Header, Card, Progress, money, NText } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { apiJson } from '@/lib/api';

type Status = {
  tier: number;
  tier_name?: string;
  transaction_limit: string;
  daily_transfer_limit?: string;
  daily_bill_limit?: string;
  bvn_verified: boolean;
  nin_verified: boolean;
  bank_tier?: number;
};

// The published Zitch ladder. Kept here as the DISPLAY table only — the tier a
// customer is actually on, and the limit actually enforced, both come from
// /api/kyc/status/ below. A table that disagreed with the server would be worse
// than no table at all.
const LADDER = [
  { tier: 1, daily: 50_000, balance: '₦300,000' },
  { tier: 2, daily: 200_000, balance: '₦500,000' },
  { tier: 3, daily: 5_000_000, balance: 'Unlimited' },
];

/** 816 693 8327 — grouped for reading aloud. Copy still uses the raw digits. */
const grouped = (n: string) => {
  const d = (n || '').replace(/\D/g, '');
  return d.length === 10 ? `${d.slice(0, 3)} ${d.slice(3, 6)} ${d.slice(6)}` : d;
};

const AccountLimits = () => {
  const { c, theme } = useTheme();
  const { accountNumber, accountName } = useWallet();
  const [status, setStatus] = useState<Status | null>(null);

  useFocusEffect(useCallback(() => {
    apiJson('/api/kyc/status/')
      .then((r) => { if (r?.success) setStatus(r as Status); })
      .catch(() => {});
  }, []));

  const tier = status?.tier ?? 1;
  // The per-transaction ceiling the server will actually enforce today. The
  // ladder row is what the tier is *entitled* to; this is what it *has*, and on
  // an account mid-upgrade they are not the same number.
  const limit = Number(status?.transaction_limit ?? 0);
  const ladderMax = LADDER[LADDER.length - 1].daily;

  const copy = async () => {
    if (!accountNumber) return;
    await Clipboard.setStringAsync(accountNumber);
    notify('Copied', 'Your account number is on the clipboard.');
  };

  const linkedId = status
    ? [status.bvn_verified && 'BVN', status.nin_verified && 'NIN'].filter(Boolean).join(' & ') || 'Not linked'
    : '—';

  return (
    <Screen>
      <Header title="Account Limits" onBack={() => router.back()} />

      {/* --- account + tier --- */}
      <LinearGradient
        colors={c.tierGradient}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={{ borderRadius: 24, padding: 20, marginBottom: 14, overflow: 'hidden' }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: theme === 'dark' ? '#E6D6AC' : '#7A6428' }}>
              Account Info
            </Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 6 }}>
              <Text style={{ fontSize: 26, fontFamily: font.extrabold, color: theme === 'dark' ? '#FFF6DF' : '#2B2205', letterSpacing: -0.4 }}>
                {grouped(accountNumber) || '—'}
              </Text>
              {!!accountNumber && (
                <Pressable
                  onPress={copy}
                  accessibilityRole="button"
                  accessibilityLabel="Copy account number"
                  hitSlop={8}
                  style={{ width: 30, height: 30, borderRadius: 15, backgroundColor: 'rgba(255,255,255,.45)', alignItems: 'center', justifyContent: 'center' }}
                >
                  <ZIcon name="copy" size={15} color={theme === 'dark' ? '#FFF6DF' : '#5C4A18'} />
                </Pressable>
              )}
            </View>
            {!!accountName && (
              <View style={{ alignSelf: 'flex-start', marginTop: 12, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999, backgroundColor: 'rgba(255,255,255,.45)' }}>
                <Text numberOfLines={1} style={{ fontSize: 12, fontFamily: font.semibold, color: theme === 'dark' ? '#FFF6DF' : '#4A3B12' }}>
                  {accountName.toUpperCase()}
                </Text>
              </View>
            )}
          </View>
          {/* Medal: the tier number in the middle of a ribboned disc. */}
          <View style={{ alignItems: 'center', justifyContent: 'center', width: 78, height: 78 }}>
            <ZIcon name="medal" size={74} color={theme === 'dark' ? 'rgba(255,246,223,.55)' : 'rgba(255,255,255,.75)'} stroke={1.6} />
            <View style={{ position: 'absolute', alignItems: 'center', paddingTop: 12 }}>
              <Text style={{ fontSize: 22, fontFamily: font.extrabold, color: theme === 'dark' ? '#FFF6DF' : '#5C4A18', lineHeight: 24 }}>{tier}</Text>
              <Text style={{ fontSize: 9.5, fontFamily: font.bold, color: theme === 'dark' ? '#E6D6AC' : '#7A6428', letterSpacing: 0.4 }}>TIER</Text>
            </View>
          </View>
        </View>
      </LinearGradient>

      {/* --- linked id --- */}
      <Card
        style={{ marginBottom: 14, flexDirection: 'row', alignItems: 'center', gap: 12 }}
        onPress={() => router.push('/kyc')}
      >
        <Text style={{ flex: 1, fontSize: 15.5, fontFamily: font.bold, color: c.ink1 }}>Linked ID</Text>
        <Text style={{ fontSize: 14, fontFamily: font.semibold, color: linkedId === 'Not linked' ? c.ink3 : c.ink2 }}>{linkedId}</Text>
        <ZIcon name="right" size={17} color={c.ink3} />
      </Card>

      {/* --- daily limit --- */}
      <Card style={{ marginBottom: 14 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <Text style={{ flex: 1, fontSize: 15.5, fontFamily: font.bold, color: c.ink1 }}>Daily Transaction Limit</Text>
          <Pressable
            onPress={() => router.push('/kyc')}
            accessibilityRole="button"
            accessibilityLabel="Raise your limit"
            hitSlop={8}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}
          >
            <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.brand }}>{tier >= 3 ? 'Details' : 'Raise'}</Text>
            <ZIcon name="right" size={15} color={c.brand} />
          </Pressable>
        </View>
        <View style={{ borderRadius: 16, backgroundColor: c.surface2, padding: 14 }}>
          <Progress value={limit} max={ladderMax} />
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 10 }}>
            <NText style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3 }}>Min {money(0)}</NText>
            <NText style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3 }}>
              Max <NText style={{ fontFamily: font.bold, color: c.ink2 }}>{money(limit)}</NText>
            </NText>
          </View>
        </View>
      </Card>

      {/* --- ladder --- */}
      <Card style={{ marginBottom: 24 }} pad={0}>
        <Text style={{ fontSize: 15.5, fontFamily: font.bold, color: c.ink1, padding: 18, paddingBottom: 14 }}>Level Benefit</Text>
        <View style={{ marginHorizontal: 14, marginBottom: 14, borderRadius: 16, borderWidth: 1, borderColor: c.line, overflow: 'hidden' }}>
          <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 10, padding: 14, backgroundColor: c.surface2 }}>
            <Text style={{ flex: 1.1, fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>Tier</Text>
            <Text style={{ flex: 1.3, fontSize: 11, fontFamily: font.regular, color: c.ink3, textAlign: 'right' }}>Maximum{'\n'}daily transaction limit</Text>
            <Text style={{ flex: 1, fontSize: 11, fontFamily: font.regular, color: c.ink3, textAlign: 'right' }}>Maximum{'\n'}account balance</Text>
          </View>
          {LADDER.map((row, i) => {
            const current = row.tier === tier;
            return (
              <View
                key={row.tier}
                style={{
                  flexDirection: 'row', alignItems: 'center', gap: 10, padding: 14,
                  backgroundColor: i % 2 ? c.surface2 : c.surface,
                  borderTopWidth: 1, borderTopColor: c.line,
                }}
              >
                <View style={{ flex: 1.1, flexDirection: 'row', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                  <Text style={{ fontSize: 14.5, fontFamily: font.bold, color: c.ink1 }}>Tier {row.tier}</Text>
                  {current && (
                    <View style={{ paddingHorizontal: 8, paddingVertical: 2.5, borderRadius: 999, backgroundColor: c.brand }}>
                      <Text style={{ fontSize: 10, fontFamily: font.bold, color: c.inkOnBrand }}>Current</Text>
                    </View>
                  )}
                </View>
                <NText style={{ flex: 1.3, fontSize: 13.5, fontFamily: font.semibold, color: c.ink2, textAlign: 'right', fontVariant: ['tabular-nums'] }}>
                  ₦{row.daily.toLocaleString()}
                </NText>
                <NText style={{ flex: 1, fontSize: 13.5, fontFamily: font.semibold, color: c.ink2, textAlign: 'right' }}>
                  {row.balance}
                </NText>
              </View>
            );
          })}
        </View>
        <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, lineHeight: 18, paddingHorizontal: 18, paddingBottom: 18 }}>
          Your partner bank applies its own tier limits alongside these. Where the two differ, the lower one applies.
        </Text>
      </Card>
    </Screen>
  );
};

export default AccountLimits;
