import React, { useCallback, useState } from 'react';
import { View, Text, Share } from 'react-native';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Btn, money } from '@/components/design/ui';
import { Monogram } from '@/components/design/flowkit';
import { useTheme, font } from '@/lib/theme';
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';

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

  const [liveTxn, setLiveTxn] = useState<any | null>(null);

  useFocusEffect(useCallback(() => {
    let alive = true;
    const reference = String(p.reference || '').trim();
    if (!reference) return () => { alive = false; };
    apiJson<any>(EP.wallet.transactionStatus, { reference })
      .then((res) => {
        if (alive && res?.success && res.transaction) setLiveTxn(res.transaction);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [p.reference]));

  const inflow = (liveTxn?.direction || p.dir) === 'in';
  const amount = Number(liveTxn?.amount ?? p.amount ?? 0);
  const type = String(liveTxn?.service ?? p.type ?? 'Transaction');
  const detail = String(liveTxn?.date ?? p.detail ?? '');
  const reference = String(liveTxn?.reference ?? p.reference ?? '');
  const mono = type.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  // Status badge reflects the latest backend status — not just the route param.
  const status = String(liveTxn?.transaction_status ?? p.status ?? 'Successful');
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
        <Row2 k="Description" v={type} />
        {detail ? <Row2 k="Date" v={detail} /> : null}
        <Row2 k="Reference" v={reference || '—'} />
        <Row2 k="Channel" v="Zitch Wallet" />
      </View>

      <View style={{ marginTop: 16 }}>
        <Btn
          label="Share receipt"
          icon="share"
          variant="outline"
          onPress={() => {
            const sign = inflow ? '+' : '-';
            Share.share({
              message: [
                type,
                `Amount: ${sign}${money(Math.abs(amount))}`,
                `Status: ${status}`,
                detail ? `Date: ${detail}` : '',
                `Reference: ${reference || '—'}`,
                '',
                'Sent with Zitch',
              ].filter(Boolean).join('\n'),
            }).catch(() => {});
          }}
        />
      </View>
    </Screen>
  );
};

export default TxnDetail;
