import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Share } from 'react-native';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { notify } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Btn, money } from '@/components/design/ui';
import { Monogram } from '@/components/design/flowkit';
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
};

type TransactionStatusResult = {
  success?: boolean;
  transaction?: TransactionStatusRow;
  message?: string;
};

const Row2 = ({ k, v }: { k: string; v: string }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 11, borderTopWidth: 1, borderTopColor: c.line }}>
      <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>{k}</Text>
      <Text selectable style={{ fontSize: 14, fontFamily: font.semibold, color: c.ink1, maxWidth: '62%', textAlign: 'right' }}>{v}</Text>
    </View>
  );
};

const TxnDetail = () => {
  const { c } = useTheme();
  const p = useLocalSearchParams<{
    type?: string; amount?: string; status?: string; dir?: string; detail?: string; reference?: string; icon?: string;
    underReview?: string; statusMessage?: string; reviewKind?: string;
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
  const electricityToken = state === 'success' && !underReview ? String(liveTxn?.token || '').trim() : '';
  const electricityRows = electricityToken ? [
    ['Electricity token', electricityToken],
    ['Meter', liveTxn?.meter], ['Meter type', liveTxn?.meter_type],
    ['Customer', liveTxn?.customer_name], ['Address', liveTxn?.customer_address],
    ['Units', liveTxn?.electricity_units],
  ].filter((row) => row[1]) : [];

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
        {electricityRows.map(([label, value]) => <Row2 key={label} k={String(label)} v={String(value)} />)}
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
                ...electricityRows.map(([label, value]) => `${label}: ${value}`),
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
