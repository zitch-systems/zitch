import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, Share } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { notify, flash, notifyError } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Btn, money } from '@/components/design/ui';
import { NText } from '@/components/design/Naira';
import { receiptHtml, shareReceipt, outcomeMessage, type ReceiptFormat } from '@/lib/receipt';
import { useTheme, font } from '@/lib/theme';
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import { clearSpendAttempt } from '@/lib/pendingSpend';
import {
  shouldContinueTransactionPolling,
  transactionStatusPresentation,
  txnState,
} from '@/lib/transactionStatus';

const STATUS_POLL_INTERVAL_MS = 4000;
const MAX_STATUS_POLLS = 5;
const TRANSFER_SPEND_SCOPES = new Set(['bank-transfer', 'zitch-transfer']);

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** The name a sent receipt's subline uses; none for money received. */
const recipientNameForLine = (name: unknown, inflow: boolean) => (inflow ? '' : String(name ?? '').trim());

/** "14 Nov 2026, 6:40 PM" from epoch ms, or from the server's "2026-11-14 18:40". */
export const receiptWhen = (ts: string | undefined, detail: string): string => {
  let t = Number(ts);
  if (!Number.isFinite(t) || t <= 0) {
    const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(detail || '');
    if (!m) return detail;
    t = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]).getTime();
  }
  const d = new Date(t);
  const h = d.getHours();
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${h % 12 || 12}:${String(d.getMinutes()).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
};

type TransactionStatusRow = {
  service?: string;
  amount?: string | number;
  transaction_status?: string;
  under_review?: boolean;
  review_kind?: string;
  status_message?: string;
  date?: string;
  reference?: string;
  direction?: string;
  token?: string;
  meter?: string;
  meter_type?: string;
  customer_name?: string;
  customer_address?: string;
  electricity_units?: string;
  recipient_name?: string;
  recipient_account?: string;
  recipient_bank?: string;
  fee?: string;
};

type TransactionStatusResult = {
  success?: boolean;
  transaction?: TransactionStatusRow;
  message?: string;
};

// The receipt card is drawn on fixed colours: it is shared as an image, and a
// shared receipt must read the same whatever theme the sender's phone was in.
const R = { card: '#FFFFFF', ink: '#06231F', muted: '#5E7470', line: '#EAF2F0', brand: '#0FA295', foot: '#7A8B87' };

const Row2 = ({ k, v, strong, last }: { k: string; v: string; strong?: boolean; last?: boolean }) => (
  <View style={{ flexDirection: 'row', gap: 14, justifyContent: 'space-between', alignItems: 'center', paddingVertical: 13, borderBottomWidth: last ? 0 : 1, borderBottomColor: R.line }}>
    <Text style={{ fontSize: 14, color: R.muted, fontFamily: font.regular }}>{k}</Text>
    <NText selectable style={{ flex: 1, fontSize: strong ? 16 : 14.5, fontFamily: strong ? font.extrabold : font.semibold, color: R.ink, textAlign: 'right', fontVariant: ['tabular-nums'] }}>{v}</NText>
  </View>
);

const TxnDetail = () => {
  const { c } = useTheme();
  const card = useRef<View>(null);
  const [sharing, setSharing] = useState<ReceiptFormat | null>(null);
  const p = useLocalSearchParams<{
    type?: string; amount?: string; status?: string; dir?: string; detail?: string; reference?: string; icon?: string;
    narration?: string; underReview?: string; statusMessage?: string; reviewKind?: string;
    recipientName?: string; recipientAccount?: string; recipientBank?: string; fee?: string; ts?: string;
    spendScope?: string; spendFingerprint?: string; spendKey?: string;
  }>();

  const [liveTxn, setLiveTxn] = useState<TransactionStatusRow | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState('');
  const refreshInFlight = useRef(false);
  const requestGeneration = useRef(0);
  const appliedGeneration = useRef(0);
  const displayedState = useRef(txnState(p.status));

  const referenceParam = String(p.reference || '').trim();
  const currentReference = useRef(referenceParam);
  currentReference.current = referenceParam;
  const spendAttempt = {
    scope: String(p.spendScope || ''),
    fingerprint: String(p.spendFingerprint || ''),
    key: String(p.spendKey || ''),
  };

  useEffect(() => {
    requestGeneration.current += 1;
    appliedGeneration.current = 0;
    displayedState.current = txnState(p.status);
    setLiveTxn(null);
    setRefreshError('');
  }, [p.status, referenceParam]);

  const clearSettledAttempt = useCallback(async (transaction: TransactionStatusRow) => {
    const returnedReference = String(transaction.reference || '').trim();
    if (!referenceParam || returnedReference !== referenceParam
      || transaction.under_review === true
      || txnState(transaction.transaction_status) === 'pending'
      || !TRANSFER_SPEND_SCOPES.has(spendAttempt.scope)
      || !spendAttempt.scope || !spendAttempt.fingerprint || !spendAttempt.key) return;
    await clearSpendAttempt(spendAttempt.scope, spendAttempt.fingerprint, spendAttempt.key);
  }, [referenceParam, spendAttempt.fingerprint, spendAttempt.key, spendAttempt.scope]);

  const requestStatus = useCallback(async (isActive: () => boolean = () => true) => {
    if (!referenceParam) return null;
    const generation = ++requestGeneration.current;
    const res = await apiJson<TransactionStatusResult>(EP.wallet.transactionStatus, { reference: referenceParam });
    if (!isActive() || currentReference.current !== referenceParam) return null;
    const transaction = res?.success ? res.transaction : undefined;
    if (!transaction) return null;
    if (String(transaction.reference || '').trim() !== referenceParam) return null;

    const nextState = txnState(transaction.under_review ? 'Under review' : transaction.transaction_status);
    if (generation < appliedGeneration.current) return displayedState.current;
    // A terminal outcome is monotonic in the customer UI. An older pending
    // response must never overwrite a newer success/failure response.
    if (generation >= appliedGeneration.current
      && (nextState !== 'pending' || displayedState.current === 'pending' || transaction.under_review === true)) {
      await clearSettledAttempt(transaction);
      if (!isActive() || currentReference.current !== referenceParam || generation < appliedGeneration.current) return null;
      displayedState.current = nextState;
      appliedGeneration.current = Math.max(appliedGeneration.current, generation);
      setLiveTxn(transaction);
    }
    return displayedState.current;
  }, [clearSettledAttempt, referenceParam]);

  useFocusEffect(useCallback(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let completedPolls = 0;
    if (!referenceParam) return () => { alive = false; };

    const poll = async () => {
      let nextStatus: unknown = p.status;
      try {
        const next = await requestStatus(() => alive);
        if (!alive) return;
        if (next) nextStatus = next;
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
  }, [p.status, referenceParam, requestStatus]));

  const refreshStatus = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    setRefreshing(true);
    setRefreshError('');
    try {
      const next = await requestStatus();
      if (!next) setRefreshError('We could not verify this transaction status. Please try again.');
    } catch {
      setRefreshError('Could not refresh the status. Check your connection and try again.');
    } finally {
      refreshInFlight.current = false;
      setRefreshing(false);
    }
  }, [requestStatus]);

  const inflow = (liveTxn?.direction || p.dir) === 'in';
  const rawAmount = liveTxn?.amount ?? p.amount;
  const amount = rawAmount == null || String(rawAmount).trim() === '' ? NaN : Number(rawAmount);
  const formattedAmount = Number.isFinite(amount) ? (inflow ? '+' : '-') + money(Math.abs(amount)) : 'Amount unavailable';
  const type = String(liveTxn?.service ?? p.type ?? 'Transaction');
  const detail = String(liveTxn?.date ?? p.detail ?? '');
  const reference = String(liveTxn?.reference ?? p.reference ?? '');
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
  const { state, label: status } = transactionStatusPresentation(rawStatus);
  const narration = String(p.narration || '').trim();
  const isTransfer = /transfer|send|withdraw/i.test(type);
  const absAmount = Number.isFinite(amount) ? money(Math.abs(amount)) : '';
  const headline = state === 'failed' ? 'Transaction failed'
    : state === 'pending' ? (underReview ? 'Under review' : 'Processing')
      : inflow ? 'Money received' : isTransfer ? 'Money sent' : 'Payment successful';
  const subline = absAmount
    ? (recipientNameForLine(liveTxn?.recipient_name ?? p.recipientName, inflow)
      ? `${absAmount} ${state === 'failed' ? 'not ' : ''}sent to ${recipientNameForLine(liveTxn?.recipient_name ?? p.recipientName, inflow)}.`
      : `${absAmount} ${state === 'failed' ? 'not ' : ''}${inflow ? 'received' : isTransfer ? 'sent' : 'paid'} · ${type}`)
    : type;
  // "14 Nov 2026, 6:40 PM" from the row's own time, never the raw server string.
  const when = receiptWhen(p.ts, detail);
  const badge = state === 'failed' ? { bg: '#E5484D', icon: 'x' } : state === 'pending' ? { bg: '#E8A33D', icon: 'history' } : { bg: '#00B51D', icon: 'check' };
  const electricityToken = state === 'success' && !underReview ? String(liveTxn?.token || '').trim() : '';
  const electricityRows = electricityToken ? [
    ['Electricity token', electricityToken],
    ['Meter', liveTxn?.meter], ['Meter type', liveTxn?.meter_type],
    ['Customer', liveTxn?.customer_name], ['Address', liveTxn?.customer_address],
    ['Units', liveTxn?.electricity_units],
  ].filter((row) => row[1]) : [];

  // A bank transfer reads as the design's receipt: who, which account and bank,
  // the reference, the fee and the total. Anything else keeps its description.
  const recipientName = String(liveTxn?.recipient_name ?? p.recipientName ?? '').trim();
  const recipientAccount = String(liveTxn?.recipient_account ?? p.recipientAccount ?? '').trim();
  const recipientBank = String(liveTxn?.recipient_bank ?? p.recipientBank ?? '').trim();
  const feeValue = Number(liveTxn?.fee ?? p.fee ?? '');
  const fee = Number.isFinite(feeValue) && feeValue > 0 ? money(feeValue) : '';
  const total = Number.isFinite(amount) ? money(Math.abs(amount) + (fee ? feeValue : 0)) : formattedAmount;
  const rows: [string, string, boolean?][] = recipientName && !inflow ? [
    ['Recipient', recipientName],
    ...(recipientAccount ? ([['Account', recipientAccount]] as [string, string][]) : []),
    ...(recipientBank ? ([['Bank', recipientBank]] as [string, string][]) : []),
    ...(narration && !/^transfer to /i.test(narration) ? ([['Note', narration]] as [string, string][]) : []),
    ['Reference', reference || '—'],
    ...(fee ? ([['Fee', fee]] as [string, string][]) : []),
    ['Total', total, true],
  ] : [
    ['Description', type],
    ...(narration ? ([['Note', narration]] as [string, string][]) : []),
    ['Reference', reference || '—'],
    ...electricityRows.map(([label, value]) => [String(label), String(value)] as [string, string]),
    ['Total', formattedAmount.replace(/^[+-]/, ''), true],
  ];

  const share = async (format: ReceiptFormat) => {
    if (sharing) return;
    setSharing(format);
    try {
      const outcome = await shareReceipt(format, {
        capture: async () => {
          const { captureRef } = await import('react-native-view-shot');
          if (!card.current) throw new Error('Receipt is not ready to capture');
          return captureRef(card.current, { format: 'jpg', quality: 0.95, result: 'tmpfile' });
        },
        html: receiptHtml({ title: headline, message: subline, rows, status }),
        reference,
      });
      const line = outcomeMessage(outcome, format);
      if (!line) return;
      if (outcome === 'shared' || outcome === 'saved') flash('Ready', line);
      else notifyError('Receipt', line);
    } catch {
      notifyError('Receipt', 'Could not prepare the receipt. Please try again.');
    } finally {
      setSharing(null);
    }
  };

  return (
    <Screen>
      <Header title="Transaction receipt" onBack={() => router.back()} />

      {/* Everything in this view is what "Share as image" captures. */}
      <View ref={card} collapsable={false} style={{ marginTop: 4, borderRadius: 24, backgroundColor: R.card, overflow: 'hidden', shadowColor: '#063731', shadowOpacity: 0.1, shadowRadius: 14, shadowOffset: { width: 0, height: 12 }, elevation: 4 }}>
        <LinearGradient
          colors={['#14A899', '#0A8C80', '#047C71']}
          locations={[0, 0.55, 1]}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={{ borderRadius: 24, alignItems: 'center', paddingTop: 26, paddingHorizontal: 20, paddingBottom: 30 }}
        >
          <View style={{ width: 64, height: 64, borderRadius: 32, backgroundColor: 'rgba(255,255,255,0.2)', alignItems: 'center', justifyContent: 'center' }}>
            <View style={{ width: 46, height: 46, borderRadius: 23, backgroundColor: badge.bg, alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name={badge.icon} size={24} color="#fff" stroke={3} />
            </View>
          </View>
          <Text style={{ marginTop: 14, fontSize: 24, fontFamily: font.extrabold, color: '#fff', letterSpacing: -0.3 }}>{headline}</Text>
          <NText style={{ marginTop: 6, fontSize: 14, fontFamily: font.medium, color: 'rgba(255,255,255,0.9)', textAlign: 'center' }}>{subline}</NText>
          {state !== 'success' ? (
            <View style={{ marginTop: 10, paddingHorizontal: 12, paddingVertical: 4, borderRadius: 999, backgroundColor: 'rgba(255,255,255,0.18)' }}>
              <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: '#fff' }}>{status}</Text>
            </View>
          ) : null}
        </LinearGradient>
        <View style={{ paddingTop: 4, paddingHorizontal: 20 }}>
          {rows.map(([k, v, strong], i) => <Row2 key={k} k={k} v={v} strong={strong} last={i === rows.length - 1} />)}
        </View>
        <View style={{ alignItems: 'center', paddingTop: 16, paddingHorizontal: 20, paddingBottom: 20 }}>
          <Text style={{ fontSize: 19, fontFamily: font.extrabold, color: R.brand, letterSpacing: -0.2 }}>Zitch</Text>
          <Text style={{ marginTop: 3, fontSize: 12, fontFamily: font.regular, color: R.foot }}>Transaction receipt{when ? ` · ${when}` : ''}</Text>
        </View>
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
        {electricityToken ? <View style={{ marginBottom: 10 }}><Btn label="Copy electricity token" icon="copy" onPress={() => {
          void Clipboard.setStringAsync(electricityToken).then(() => notify('Copied', 'Electricity token copied.')).catch(() => notify('Could not copy', 'Please try again.'));
        }} /></View> : null}
        {state === 'pending' && referenceParam ? (
          <View style={{ marginBottom: 10 }}>
            <Btn
              label={refreshing ? 'Refreshing status…' : 'Refresh status'}
              icon="history"
              disabled={refreshing}
              onPress={() => void refreshStatus()}
            />
            {refreshError ? (
              <Text
                accessibilityRole="alert"
                accessibilityLiveRegion="polite"
                style={{ color: c.red, fontFamily: font.semibold, fontSize: 12.5, lineHeight: 18, marginTop: 8 }}
              >
                {refreshError}
              </Text>
            ) : null}
          </View>
        ) : null}
        {/* A transaction still in flight is shared as a status, never as a
            receipt: a forwarded receipt reads as proof of payment. */}
        {state === 'pending' ? (
          <Btn
            label="Share status"
            icon="share"
            variant="outline"
            onPress={() => {
              Share.share({
                message: [
                  type,
                  `Amount: ${formattedAmount}`,
                  `Status: ${status}`,
                  detail ? `Date: ${detail}` : '',
                  `Reference: ${reference || '—'}`,
                  '',
                  'Sent with Zitch',
                ].filter(Boolean).join('\n'),
              }).catch(() => notify('Could not share', 'Please try again.'));
            }}
          />
        ) : (
        <View style={{ flexDirection: 'row', gap: 10 }}>
          {([['jpeg', 'image', 'Share as image'], ['pdf', 'file', 'Share as PDF']] as [ReceiptFormat, string, string][]).map(([fmt, icon, label]) => (
            <Pressable
              key={fmt}
              onPress={() => void share(fmt)}
              disabled={sharing !== null}
              accessibilityRole="button"
              accessibilityLabel={label}
              accessibilityState={{ disabled: sharing !== null }}
              style={({ pressed }) => ({ flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, minHeight: 54, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, opacity: pressed || sharing === fmt ? 0.7 : 1 })}
            >
              <ZIcon name={icon} size={19} color={c.brand} />
              <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>{label}</Text>
            </Pressable>
          ))}
        </View>
        )}
      </View>
    </Screen>
  );
};

export default TxnDetail;
