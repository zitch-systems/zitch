import React, { useCallback, useState } from 'react';
import { View, Text, Pressable, ScrollView, ActivityIndicator } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import { apiJson } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Card, money } from '@/components/design/ui';
import { SectionLabel } from '@/components/design/widgets';
import { useTheme, font } from '@/lib/theme';

// A linked external bank account (Mono open-banking), as returned by
// POST /api/banklink/list/. Balance may be null until a refresh succeeds.
type Linked = {
  id: number;
  bank_name: string;
  account_number: string; // masked
  account_name: string;
  balance: string | null;
  balance_updated: string | null;
  status: 'active' | 'reauth' | string;
};

// Two-letter monogram on a brand-tinted tile (no raster bank logos), matching
// the design v2 LinkedBankCard.
const Monogram = ({ name, color }: { name: string; color: string }) => {
  const init = (name || 'BK').trim().slice(0, 2).toUpperCase();
  return (
    <View style={{ width: 34, height: 34, borderRadius: 11, backgroundColor: color + '22', alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ color, fontFamily: font.extrabold, fontSize: 12.5 }}>{init}</Text>
    </View>
  );
};

const LinkedBankCard = ({ a, onRefresh, refreshing }: { a: Linked; onRefresh: () => void; refreshing: boolean }) => {
  const { c } = useTheme();
  const reauth = a.status === 'reauth';
  return (
    <Card pad={0} style={{ width: 280, padding: 14, marginRight: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        <Monogram name={a.bank_name} color={c.brand} />
        <View style={{ flex: 1 }}>
          <Text numberOfLines={1} style={{ fontSize: 13.5, fontFamily: font.bold, color: c.ink1 }}>{a.bank_name || 'Bank'}</Text>
          <Text numberOfLines={1} style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.medium, fontVariant: ['tabular-nums'] }}>{a.account_number}</Text>
        </View>
        <Pressable onPress={onRefresh} hitSlop={8} style={{ width: 28, height: 28, borderRadius: 9, backgroundColor: c.surface3, alignItems: 'center', justifyContent: 'center' }}>
          {refreshing ? <ActivityIndicator size="small" color={c.ink3} /> : <ZIcon name="refresh" size={15} color={c.ink2} />}
        </Pressable>
      </View>

      {reauth ? (
        <Pressable onPress={() => router.push('/linkbank')} style={{ marginTop: 12, paddingVertical: 9, borderRadius: 12, backgroundColor: 'rgba(245,166,35,.16)', alignItems: 'center' }}>
          <Text style={{ color: '#B27400', fontFamily: font.bold, fontSize: 12.5 }}>Reconnect to view</Text>
        </Pressable>
      ) : (
        <Text style={{ marginTop: 12, fontSize: 15, fontFamily: font.extrabold, color: c.ink1, fontVariant: ['tabular-nums'] }}>
          {a.balance != null ? money(Number(a.balance)) : '—'}
        </Text>
      )}
    </Card>
  );
};

const ConnectTile = () => {
  const { c } = useTheme();
  return (
    <Pressable onPress={() => router.push('/linkbank')} style={{ width: 132, marginRight: 12 }}>
      <View style={{ flex: 1, minHeight: 96, borderRadius: 18, borderWidth: 1.5, borderColor: c.line, borderStyle: 'dashed', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: c.surface2 }}>
        <View style={{ width: 34, height: 34, borderRadius: 11, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="plus" size={18} color={c.brand} stroke={2.4} />
        </View>
        <Text style={{ fontSize: 12, fontFamily: font.bold, color: c.ink2 }}>Connect a bank</Text>
      </View>
    </Pressable>
  );
};

// Horizontal snap strip of linked external bank accounts + a connect tile.
// Reads /api/banklink/list/ on focus; refresh hits /api/banklink/refresh/.
export const ConnectedAccounts = () => {
  const { c } = useTheme();
  const [accts, setAccts] = useState<Linked[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiJson<{ accounts?: Linked[] }>('/api/banklink/list/');
      setAccts(Array.isArray(res.accounts) ? res.accounts : []);
    } catch {
      // leave last-known list; surfaced elsewhere
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const refresh = async (id: number) => {
    setBusyId(id);
    try {
      const res = await apiJson<{ account?: Linked }>('/api/banklink/refresh/', { linked_id: id });
      if (res.account) setAccts((prev) => prev.map((a) => (a.id === id ? { ...a, ...res.account } : a)));
    } catch {
      // keep prior balance
    } finally {
      setBusyId(null);
    }
  };

  if (loading) return null;

  return (
    <View style={{ paddingTop: 22 }}>
      <View style={{ paddingHorizontal: 18 }}>
        <SectionLabel action={accts.length ? 'Add' : undefined} onAction={accts.length ? () => router.push('/linkbank') : undefined}>
          Connected accounts
        </SectionLabel>
      </View>
      {accts.length === 0 ? (
        <Pressable onPress={() => router.push('/linkbank')} style={{ marginHorizontal: 16 }}>
          <Card style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
            <View style={{ width: 42, height: 42, borderRadius: 13, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name="bank" size={22} color={c.brand} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>Connect a bank</Text>
              <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>See balances & move money in — via Mono</Text>
            </View>
            <ZIcon name="right" size={18} color={c.ink3} />
          </Card>
        </Pressable>
      ) : (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 16 }}>
          {accts.map((a) => (
            <LinkedBankCard key={a.id} a={a} onRefresh={() => refresh(a.id)} refreshing={busyId === a.id} />
          ))}
          <ConnectTile />
        </ScrollView>
      )}
    </View>
  );
};

export default ConnectedAccounts;
