import React, { useState, useCallback, useMemo } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import {
  Screen, Header, TxnRow, HeaderLink, SelectRow, PickerSheet, Card, NText, money, txnState,
} from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { monthKey, monthLabel, txnDate } from '@/lib/format';

// Category is derived from the icon the wallet mapper already assigned, so the
// filter and the row's own colour can never disagree about what a transaction
// is. `in`/`out` stay separate from the service categories because "money in"
// answers a different question than "airtime".
const CATEGORIES = [
  { v: 'all', label: 'All Categories' },
  { v: 'in', label: 'Money in' },
  { v: 'out', label: 'Money out' },
  { v: 'transfer', label: 'Transfers' },
  { v: 'airtime', label: 'Airtime & Data' },
  { v: 'bills', label: 'Bills & TV' },
];

const STATUSES = [
  { v: 'all', label: 'All Status' },
  { v: 'success', label: 'Successful' },
  { v: 'pending', label: 'Pending' },
  { v: 'failed', label: 'Failed' },
];

const inCategory = (t: { dir: string; icon: string }, cat: string) => {
  if (cat === 'all') return true;
  if (cat === 'in') return t.dir === 'in';
  if (cat === 'out') return t.dir === 'out';
  if (cat === 'transfer') return t.icon === 'send' || t.icon === 'deposit';
  if (cat === 'airtime') return t.icon === 'airtime' || t.icon === 'data';
  if (cat === 'bills') return t.icon === 'bills' || t.icon === 'tv';
  return true;
};

const History = () => {
  const { c } = useTheme();
  const { txns, reload } = useWallet();
  const [cat, setCat] = useState('all');
  const [status, setStatus] = useState('all');
  const [picker, setPicker] = useState<null | 'cat' | 'status'>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [refreshing, setRefreshing] = useState(false);

  // Pull the latest ledger on focus — otherwise History shows only whatever was
  // cached when the wallet last loaded (a transfer made elsewhere wouldn't appear
  // until some other screen happened to refresh).
  useFocusEffect(useCallback(() => { reload(); }, [reload]));

  // Pull-to-refresh: re-fetch the ledger while waiting on a pending transfer to
  // settle, instead of leaving/re-entering the screen to force a reload.
  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try { await reload(); } finally { setRefreshing(false); }
  }, [reload]);

  // Filter, then bucket into months. Both the In/Out totals and the rows come
  // from the SAME filtered set, so a total can never describe transactions the
  // list below it isn't showing — which is the whole point of putting a summary
  // at the top of a filtered list.
  const groups = useMemo(() => {
    const kept = txns.filter((t) => inCategory(t, cat) && (status === 'all' || txnState(t.status) === status));
    const byMonth = new Map<string, typeof kept>();
    for (const t of kept) {
      // Undated rows would otherwise vanish from a month-grouped list entirely.
      // They get their own bucket at the end rather than being dropped or
      // guessed into the current month.
      const key = t.ts ? monthKey(t.ts) : 'undated';
      const list = byMonth.get(key);
      if (list) list.push(t); else byMonth.set(key, [t]);
    }
    return [...byMonth.entries()]
      .sort((a, b) => (a[0] === 'undated' ? 1 : b[0] === 'undated' ? -1 : b[0].localeCompare(a[0])))
      .map(([key, rows]) => ({
        key,
        label: key === 'undated' ? 'Earlier' : monthLabel(key),
        rows,
        // Failed money never left the account, so counting it would overstate
        // both sides of the summary.
        moneyIn: rows.filter((t) => t.dir === 'in' && txnState(t.status) !== 'failed')
          .reduce((s, t) => s + Math.abs(t.amount), 0),
        moneyOut: rows.filter((t) => t.dir === 'out' && txnState(t.status) !== 'failed')
          .reduce((s, t) => s + Math.abs(t.amount), 0),
      }));
  }, [txns, cat, status]);

  const catLabel = CATEGORIES.find((x) => x.v === cat)?.label ?? 'All Categories';
  const statusLabel = STATUSES.find((x) => x.v === status)?.label ?? 'All Status';

  return (
    <Screen tab onRefresh={onRefresh} refreshing={refreshing}>
      <Header
        title="Transactions"
        onBack={() => router.back()}
        right={<HeaderLink label="Download" onPress={() => router.push('/statement')} />}
      />

      <View style={{ flexDirection: 'row', gap: 12, marginBottom: 16 }}>
        <SelectRow compact value={catLabel} onPress={() => setPicker('cat')} />
        <SelectRow compact value={statusLabel} onPress={() => setPicker('status')} />
      </View>

      {groups.length === 0 ? (
        <Card style={{ alignItems: 'center', paddingVertical: 42 }}>
          <View style={{ width: 56, height: 56, borderRadius: 28, backgroundColor: c.surface3, alignItems: 'center', justifyContent: 'center', marginBottom: 14 }}>
            <ZIcon name="history" size={26} color={c.ink3} />
          </View>
          <Text style={{ color: c.ink2, fontFamily: font.bold, fontSize: 15 }}>Nothing to show</Text>
          <Text style={{ color: c.ink3, fontFamily: font.regular, fontSize: 13, marginTop: 4, textAlign: 'center' }}>
            {cat === 'all' && status === 'all'
              ? 'Your transactions will appear here.'
              : 'No transactions match these filters.'}
          </Text>
        </Card>
      ) : (
        groups.map((g) => {
          const shut = !!collapsed[g.key];
          return (
            <Card key={g.key} style={{ marginBottom: 14 }} pad={16}>
              <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
                <Pressable
                  onPress={() => setCollapsed((p) => ({ ...p, [g.key]: !p[g.key] }))}
                  accessibilityRole="button"
                  accessibilityLabel={`${g.label}, ${shut ? 'expand' : 'collapse'}`}
                  style={{ flex: 1 }}
                  hitSlop={6}
                >
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                    <Text style={{ fontSize: 19, fontFamily: font.extrabold, color: c.ink1, letterSpacing: -0.2 }}>{g.label}</Text>
                    <View style={{ transform: [{ rotate: shut ? '-90deg' : '0deg' }] }}>
                      <ZIcon name="down" size={16} color={c.ink2} stroke={2.4} />
                    </View>
                  </View>
                  <View style={{ flexDirection: 'row', gap: 18, marginTop: 6 }}>
                    <NText style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>
                      In: <NText style={{ fontFamily: font.bold, color: c.ink2 }}>{money(g.moneyIn)}</NText>
                    </NText>
                    <NText style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>
                      Out: <NText style={{ fontFamily: font.bold, color: c.ink2 }}>{money(g.moneyOut)}</NText>
                    </NText>
                  </View>
                </Pressable>
                <Pressable
                  onPress={() => router.push({ pathname: '/analysis', params: { month: g.key } })}
                  accessibilityRole="button"
                  accessibilityLabel={`Spending analysis for ${g.label}`}
                  style={({ pressed }) => ({ paddingHorizontal: 18, paddingVertical: 10, borderRadius: 999, backgroundColor: c.brand, opacity: pressed ? 0.85 : 1 })}
                >
                  <Text style={{ fontSize: 13.5, fontFamily: font.bold, color: c.inkOnBrand }}>Analysis</Text>
                </Pressable>
              </View>

              {!shut && (
                <View style={{ marginTop: 6, borderTopWidth: 1, borderTopColor: c.line }}>
                  {g.rows.map((x, i) => (
                    <TxnRow
                      key={x.id}
                      txn={{ ...x, detail: x.ts ? txnDate(x.ts) : x.detail }}
                      last={i === g.rows.length - 1}
                      onPress={() => router.push({ pathname: '/txndetail', params: { type: x.type, amount: String(x.amount), status: x.status, dir: x.dir, detail: x.detail, reference: x.reference, icon: x.icon, narration: x.narration ?? '' } })}
                    />
                  ))}
                </View>
              )}
            </Card>
          );
        })
      )}

      <PickerSheet
        open={picker === 'cat'}
        onClose={() => setPicker(null)}
        title="Filter by category"
        options={CATEGORIES}
        value={cat}
        onPick={setCat}
      />
      <PickerSheet
        open={picker === 'status'}
        onClose={() => setPicker(null)}
        title="Filter by status"
        options={STATUSES}
        value={status}
        onPick={setStatus}
      />
    </Screen>
  );
};

export default History;
