import React from 'react';
import { View, Text } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Btn, money } from '@/components/design/ui';
import { Monogram } from '@/components/design/flowkit';
import { useTheme, font } from '@/lib/theme';

const Row2 = ({ k, v }: { k: string; v: string }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 11, borderTopWidth: 1, borderTopColor: c.line }}>
      <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>{k}</Text>
      <Text style={{ fontSize: 14, fontFamily: font.semibold, color: c.ink1, maxWidth: '62%', textAlign: 'right' }}>{v}</Text>
    </View>
  );
};

const TxnDetail = () => {
  const { c } = useTheme();
  const p = useLocalSearchParams<{
    type?: string; amount?: string; status?: string; dir?: string; detail?: string; reference?: string; icon?: string;
  }>();

  const inflow = p.dir === 'in';
  const amount = Number(p.amount || 0);
  const mono = (p.type || 'TX').split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  // Status badge reflects the real status — not always "success".
  const status = p.status || 'Successful';
  const sl = status.toLowerCase();
  const statusColor = sl === 'failed' ? c.red : sl === 'pending' ? c.amber : c.lime;
  const statusIcon = sl === 'failed' ? 'x' : sl === 'pending' ? 'history' : 'check';

  return (
    <Screen>
      <Header title="Transaction details" onBack={() => router.back()} />

      <View style={{ alignItems: 'center', paddingTop: 16 }}>
        <Monogram text={mono} color={inflow ? c.lime : c.brand} size={64} />
        <Text style={{ fontSize: 32, fontFamily: font.extrabold, color: inflow ? c.lime : c.ink1, marginTop: 14, fontVariant: ['tabular-nums'] }}>
          {(inflow ? '+' : '-') + money(Math.abs(amount))}
        </Text>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 8, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999, backgroundColor: `${statusColor}1F` }}>
          <ZIcon name={statusIcon} size={13} color={statusColor} />
          <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: statusColor }}>{status}</Text>
        </View>
      </View>

      <View style={{ marginTop: 22, borderRadius: 18, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, paddingHorizontal: 16, paddingBottom: 8 }}>
        <Row2 k="Description" v={p.type || 'Transaction'} />
        {p.detail ? <Row2 k="Date" v={p.detail} /> : null}
        <Row2 k="Reference" v={p.reference || '—'} />
        <Row2 k="Channel" v="Zitch Wallet" />
      </View>

      <View style={{ marginTop: 16 }}>
        <Btn label="Share receipt" icon="share" variant="outline" onPress={() => {}} />
      </View>
    </Screen>
  );
};

export default TxnDetail;
