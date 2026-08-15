import React, { useMemo, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { Screen, Header, Card, Progress, money, NText, txnState } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font, ICON_COLORS, iconTint } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { monthKey, monthLabel } from '@/lib/format';

// The label a spending category shows under the chart. Keyed by the icon the
// wallet mapper already assigned, so a category can never drift from the colour
// and glyph the transaction list gave the same rows.
const CATEGORY_LABEL: Record<string, string> = {
  send: 'Transfers',
  airtime: 'Airtime',
  data: 'Data',
  bills: 'Electricity',
  tv: 'Cable TV',
  dice: 'Betting',
  loan: 'Loan',
  fixed: 'Savings',
  deposit: 'Wallet funding',
  wallet: 'Other',
};

const Analysis = () => {
  const { c, theme } = useTheme();
  const { txns } = useWallet();
  const { month } = useLocalSearchParams<{ month?: string }>();
  // Read once on mount rather than on every render: the clock is not a prop, and
  // a screen left open across midnight on the 1st should not silently re-scope
  // itself to a different month under the reader.
  const [thisMonth] = useState(() => monthKey(Date.now()));
  const key = String(month || thisMonth);

  const { rows, spent, received, top } = useMemo(() => {
    // Failed transactions are excluded throughout: nothing left the account, so
    // counting them would describe spending that never happened.
    const kept = txns.filter(
      (t) => t.ts && monthKey(t.ts) === key && txnState(t.status) !== 'failed',
    );
    const out = kept.filter((t) => t.dir === 'out');
    const totals = new Map<string, number>();
    for (const t of out) {
      totals.set(t.icon, (totals.get(t.icon) ?? 0) + Math.abs(t.amount));
    }
    const list = [...totals.entries()]
      .map(([icon, total]) => ({ icon, total, label: CATEGORY_LABEL[icon] ?? 'Other' }))
      .sort((a, b) => b.total - a.total);
    return {
      rows: list,
      spent: out.reduce((s, t) => s + Math.abs(t.amount), 0),
      received: kept.filter((t) => t.dir === 'in').reduce((s, t) => s + Math.abs(t.amount), 0),
      top: list[0]?.total ?? 0,
    };
  }, [txns, key]);

  return (
    <Screen>
      <Header title="Analysis" sub={key === 'undated' ? undefined : monthLabel(key)} onBack={() => router.back()} />

      <Card style={{ marginBottom: 14, flexDirection: 'row' }}>
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: c.ink3 }}>Money out</Text>
          <NText style={{ fontSize: 21, fontFamily: font.extrabold, color: c.ink1, marginTop: 4, letterSpacing: -0.3 }}>{money(spent)}</NText>
        </View>
        <View style={{ width: 1, backgroundColor: c.line, marginHorizontal: 16 }} />
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: c.ink3 }}>Money in</Text>
          <NText style={{ fontSize: 21, fontFamily: font.extrabold, color: c.lime, marginTop: 4, letterSpacing: -0.3 }}>{money(received)}</NText>
        </View>
      </Card>

      <Card>
        <Text style={{ fontSize: 15.5, fontFamily: font.bold, color: c.ink1, marginBottom: 4 }}>Where it went</Text>
        <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: c.ink3, marginBottom: 18 }}>
          Your spending this month, largest first.
        </Text>
        {rows.length === 0 ? (
          <Text style={{ fontSize: 13.5, fontFamily: font.regular, color: c.ink3, paddingVertical: 20, textAlign: 'center' }}>
            No spending in this period.
          </Text>
        ) : (
          rows.map((r) => {
            const accent = ICON_COLORS[r.icon] ?? c.ink2;
            const share = spent > 0 ? Math.round((r.total / spent) * 100) : 0;
            return (
              <View key={r.icon} style={{ marginBottom: 18 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 11, marginBottom: 9 }}>
                  <View style={{ width: 34, height: 34, borderRadius: 17, backgroundColor: iconTint(accent, theme === 'dark'), alignItems: 'center', justifyContent: 'center' }}>
                    <ZIcon name={r.icon} size={17} color={accent} stroke={2} />
                  </View>
                  <Text style={{ flex: 1, fontSize: 14.5, fontFamily: font.semibold, color: c.ink1 }}>{r.label}</Text>
                  <View style={{ alignItems: 'flex-end' }}>
                    <NText style={{ fontSize: 14.5, fontFamily: font.bold, color: c.ink1, fontVariant: ['tabular-nums'] }}>{money(r.total)}</NText>
                    <Text style={{ fontSize: 11.5, fontFamily: font.regular, color: c.ink3, marginTop: 1 }}>{share}%</Text>
                  </View>
                </View>
                {/* Scaled against the LARGEST category, not the total — at ten
                    categories every bar against the total is a sliver, and the
                    comparison the chart exists to make becomes unreadable. */}
                <Progress value={r.total} max={top} tone={accent} />
              </View>
            );
          })
        )}
      </Card>
    </Screen>
  );
};

export default Analysis;
