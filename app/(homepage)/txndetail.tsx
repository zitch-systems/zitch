import React, { useCallback, useState } from 'react';
import { View, Text, Share } from 'react-native';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Btn, money } from '@/components/design/ui';
import { Monogram } from '@/components/design/flowkit';
import { useTheme, font } from '@/lib/theme';
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import {
  shouldContinueTransactionPolling,
  transactionStatusPresentation,
} from '@/lib/transactionStatus';

const STATUS_POLL_INTERVAL_MS = 4000;
const MAX_STATUS_POLLS = 5;

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
    underReview?: string; statusMessage?: string; reviewKind?: string;
  }>();

  const [liveTxn, setLiveTxn] = useState<any | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const referenceParam = String(p.reference || '').trim();

  const refreshStatus = useCallback(async () => {
    if (!referenceParam) return null;
    const res = await apiJson<any>(EP.wallet.transactionStatus, { reference: referenceParam });
    if (res?.success && res.transaction) {
      setLiveTxn(res.transaction);
      return res.transaction;
    }
    return null;
  }, [referenceParam]);

  useFocusEffect(useCallback(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let completedPolls = 0;
    if (!referenceParam) return () => { alive = false; };

    const poll = async () => {
      let nextStatus: unknown = p.status;
      try {
        const res = await apiJson<any>(EP.wallet.transactionStatus, { reference: referenceParam });
        if (!alive) return;
        if (res?.success && res.transaction) {
          setLiveTxn(res.transaction);
          nextStatus = res.transaction.transaction_status;
        }
      } catch {
        if (!alive) return;
      }
      completedPolls += 1;
      if (alive && shouldContinueTransactionPolling(nextStatus, completedPolls, MAX_STATUS_POLLS)) {
        timer = setTimeout(poll, STATUS_POLL_INTERVAL_MS);
      }
    };

    void poll();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [p.status, referenceParam]));

  const inflow = (liveTxn?.direction || p.dir) === 'in';
  const amount = Number(liveTxn?.amount ?? p.amount ?? 0);
  const type = String(liveTxn?.service ?? p.type ?? 'Transaction');
  const detail = String(liveTxn?.date ?? p.detail ?? '');
  const reference = String(liveTxn?.reference ?? p.reference ?? '');
  const mono = type.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  // Status badge reflects the latest backend status — not just the route param.
  const underReview = liveTxn
    ? liveTxn.under_review === true
    : String(p.underReview || '') === '1';
  const reviewMessage = String(
    liveTxn
      ? (liveTxn.status_message || '')
      : (p.statusMessage || ''),
  ).trim();
  const rawStatus = underReview
    ? 'Under review'
    : String(liveTxn?.transaction_status ?? p.status ?? '').trim();
  const { state, label: status, icon: statusIcon } = transactionStatusPresentation(rawStatus);
  const statusColor = state === 'failed' ? c.red : state === 'pending' ? c.amber : c.lime;

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

      {underReview ? (
        <View
          accessibilityRole="alert"
          style={{ marginTop: 16, borderRadius: 16, borderWidth: 1, borderColor: `${c.amber}55`, backgroundColor: `${c.amber}14`, padding: 14, gap: 5 }}
        >
          <Text selectable style={{ color: c.amber, fontFamily: font.bold, fontSize: 14 }}>Under review</Text>
          <Text selectable style={{ color: c.ink3, fontFamily: font.regular, fontSize: 13.5, lineHeight: 19 }}>
            {reviewMessage || "We are confirming the provider's final outcome. Do not retry this transaction."}
          </Text>
        </View>
      ) : null}

      <View style={{ marginTop: 16 }}>
        {state === 'pending' && referenceParam ? (
          <View style={{ marginBottom: 10 }}>
            <Btn
              label={refreshing ? 'Refreshing status…' : 'Refresh status'}
              icon="history"
              disabled={refreshing}
              onPress={() => {
                setRefreshing(true);
                refreshStatus()
                  .catch(() => {})
                  .finally(() => setRefreshing(false));
              }}
            />
          </View>
        ) : null}
        <Btn
          label={state === 'pending' ? 'Share status' : 'Share receipt'}
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
