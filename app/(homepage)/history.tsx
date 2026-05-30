import React, { useState } from 'react';
import { View, Text, Pressable, ScrollView } from 'react-native';
import { router } from 'expo-router';
import { Screen, Header, TxnRow } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const FILTERS = ['All', 'Money in', 'Money out', 'Airtime', 'Bills', 'Transfers'];

const History = () => {
  const { c } = useTheme();
  const { txns } = useWallet();
  const [active, setActive] = useState('All');

  const filtered = txns.filter((t) => {
    if (active === 'All') return true;
    if (active === 'Money in') return t.dir === 'in';
    if (active === 'Money out') return t.dir === 'out';
    if (active === 'Airtime') return t.icon === 'airtime' || t.icon === 'data';
    if (active === 'Bills') return t.icon === 'bills' || t.icon === 'tv';
    if (active === 'Transfers') return t.icon === 'send';
    return true;
  });

  return (
    <Screen tab>
      <Header title="Transaction History" onBack={() => router.back()} />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingBottom: 14 }}>
        {FILTERS.map((f) => {
          const on = f === active;
          return (
            <Pressable
              key={f}
              onPress={() => setActive(f)}
              style={{ paddingVertical: 8, paddingHorizontal: 16, borderRadius: 999, backgroundColor: on ? c.brand : c.surface, borderWidth: 1.5, borderColor: on ? c.brand : c.line }}
            >
              <Text style={{ fontSize: 13, fontFamily: font.semibold, color: on ? '#fff' : c.ink2 }}>{f}</Text>
            </Pressable>
          );
        })}
      </ScrollView>
      {filtered.length === 0 ? (
        <Text style={{ color: c.ink3, fontFamily: font.regular, paddingVertical: 12 }}>No transactions</Text>
      ) : (
        <View>{filtered.map((x, i) => <TxnRow key={x.id} txn={x} last={i === filtered.length - 1} />)}</View>
      )}
    </Screen>
  );
};

export default History;
