import React from 'react';
import { View, Text, Pressable } from 'react-native';
import { router } from 'expo-router';
import { Screen, TxnRow, money } from '@/components/design/ui';
import { Hero, SectionLabel } from '@/components/design/widgets';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const Wallet = () => {
  const { c } = useTheme();
  const { balance, txns, showBal } = useWallet();

  const moneyIn = txns.filter((t) => t.dir === 'in').reduce((s, t) => s + Math.abs(t.amount), 0);
  const moneyOut = txns.filter((t) => t.dir === 'out').reduce((s, t) => s + Math.abs(t.amount), 0);

  return (
    <Screen pad={false} tab>
      <Text style={{ paddingHorizontal: 20, paddingTop: 6, fontSize: 26, fontFamily: font.extrabold, color: c.ink1 }}>Wallet</Text>

      <Hero style={{ margin: 16 }} watermark={140}>
        <Text style={{ fontSize: 13, color: 'rgba(255,255,255,.85)', fontFamily: font.regular }}>Total balance</Text>
        <Text style={{ fontSize: 32, fontFamily: font.extrabold, color: '#fff', marginTop: 4, fontVariant: ['tabular-nums'] }}>
          {showBal ? money(balance) : '₦ ••••••'}
        </Text>
        <View style={{ flexDirection: 'row', gap: 10, marginTop: 18 }}>
          <Pressable onPress={() => router.push('/addmoney')} style={{ flex: 1, paddingVertical: 12, borderRadius: 13, backgroundColor: '#fff', alignItems: 'center' }}>
            <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 14 }}>+ Add money</Text>
          </Pressable>
          <Pressable onPress={() => router.push('/comingsoon')} style={{ flex: 1, paddingVertical: 12, borderRadius: 13, backgroundColor: 'rgba(255,255,255,.18)', borderWidth: 1, borderColor: 'rgba(255,255,255,.25)', alignItems: 'center' }}>
            <Text style={{ color: '#fff', fontFamily: font.bold, fontSize: 14 }}>Send</Text>
          </Pressable>
        </View>
      </Hero>

      <View style={{ flexDirection: 'row', gap: 12, marginHorizontal: 16 }}>
        {[
          { k: 'Money in', v: moneyIn, color: c.lime, sign: '+' },
          { k: 'Money out', v: moneyOut, color: c.ink1, sign: '-' },
        ].map((s) => (
          <View key={s.k} style={{ flex: 1, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, padding: 16 }}>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>{s.k}</Text>
            <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: s.color, marginTop: 4, fontVariant: ['tabular-nums'] }}>
              {s.sign}{money(s.v)}
            </Text>
          </View>
        ))}
      </View>

      <View style={{ paddingHorizontal: 18, paddingTop: 22 }}>
        <SectionLabel action="Filter">Transactions</SectionLabel>
        {txns.length === 0 ? (
          <Text style={{ color: c.ink3, fontFamily: font.regular, paddingVertical: 8 }}>No transactions yet</Text>
        ) : (
          txns.map((x, i) => <TxnRow key={x.id} txn={x} last={i === txns.length - 1} />)
        )}
      </View>
    </Screen>
  );
};

export default Wallet;
