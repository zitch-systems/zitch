import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Share } from 'react-native';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { notify, flash, notifyError } from '@/components/design/Notify';
import { Screen, Header, Btn, money } from '@/components/design/ui';
import { ReceiptTicket, ReceiptActionRow } from '@/components/design/Receipt';
import ReceiptExport, { type ExportAction } from '@/components/design/ReceiptExport';
import { receiptHtml, receiptStamp, senderRows, type ReceiptRow } from '@/lib/receipt';
import { useWallet } from '@/lib/wallet';
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

/** When the transaction happened: the row's epoch ms, else the server's "2026-11-14 18:40". */
export const receiptDate = (ts: string | undefined, detail: string): Date | null => {
  let t = Number(ts);
  if (!Number.isFinite(t) || t <= 0) {
    const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(detail || '');
    if (!m) return null;
    t = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]).getTime();
  }
  return new Date(t);
};

/** "14 Nov 2026, 6:40 PM" from epoch ms, or from the server's "2026-11-14 18:40". */
export const receiptWhen = (ts: string | undefined, detail: string): string => {
  const d = receiptDate(ts, detail);
  if (!d) return detail;
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

const TxnDetail = () => {
  const { c } = useTheme();
  const card = useRef<View>(null);
  const [action, setAction] = useState<ExportAction | null>(null);
  const [busy, setBusy] = useState(false);
  const { accountName, firstName, accountNumber, bankName } = useWallet();
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
  const electricityToken = state === 'success' && !underReview ? String(liveTxn?.token || '').trim() : '';
  const electricityRows: ReceiptRow[] = electricityToken ? ([
    // "Token" gets the ticket's highlighted box: the one value typed into the meter.
    ['Token', electricityToken],
    ['Meter', liveTxn?.meter], ['Meter type', liveTxn?.meter_type],
    ['Customer', liveTxn?.customer_name], ['Address', liveTxn?.customer_address],
    ['Units', liveTxn?.electricity_units],
  ] as [string, unknown][]).filter((row) => row[1]).map(([k, v]) => [k, String(v)] as ReceiptRow) : [];

  // The receipt from history is the same document as the one after a payment
  // (review, 17 Nov 2026): the amount as the headline, the status, when it
  // happened (the transaction's own time, not now), who it went to and who it
  // came from, then the details and the reference.
  const recipientName = String(liveTxn?.recipient_name ?? p.recipientName ?? '').trim();
  const recipientAccount = String(liveTxn?.recipient_account ?? p.recipientAccount ?? '').trim();
  const recipientBank = String(liveTxn?.recipient_bank ?? p.recipientBank ?? '').trim();
  const feeValue = Number(liveTxn?.fee ?? p.fee ?? '');
  const fee = Number.isFinite(feeValue) && feeValue > 0 ? money(feeValue) : '';
  const at = receiptDate(p.ts, detail);
  const stamp = at ? receiptStamp(at) : null;
  // Money out left this wallet, so the customer is the sender (their Zitch
  // account, masked on the ticket). Money in names no sender: we don't have one.
  const from = inflow ? [] : senderRows({
    name: accountName || firstName,
    account: accountNumber,
    bank: accountNumber ? (bankName || 'Zitch account') : undefined,
  });
  const rows: ReceiptRow[] = [
    ['Amount', absAmount || formattedAmount, true],
    ...(recipientName && !inflow ? ([
      ['Recipient', recipientName],
      ...(recipientBank ? [['Bank', recipientBank]] : []),
      ...(recipientAccount ? [['Account', recipientAccount]] : []),
    ] as ReceiptRow[]) : ([['Description', type]] as ReceiptRow[])),
    ...from,
    ...(narration && !/^transfer to /i.test(narration) ? ([['Note', narration]] as ReceiptRow[]) : []),
    ...electricityRows,
    ...(fee && Number.isFinite(amount) ? ([['Fee', fee], ['Total debited', money(Math.abs(amount) + feeValue)]] as ReceiptRow[]) : []),
    ...(stamp ? ([['Date', stamp.date], ['Time', stamp.time]] as ReceiptRow[]) : detail ? ([['Date', detail]] as ReceiptRow[]) : []),
    ...(reference ? ([['Reference', reference]] as ReceiptRow[]) : []),
  ];

  const onCopyRef = async () => {
    try {
      await Clipboard.setStringAsync(reference || [headline, subline, '', ...rows.map(([k, v]) => `${k}: ${v}`), '', 'Zitch'].join('\n'));
      flash('Copied', reference ? 'Reference copied' : 'Receipt copied');
    } catch { notifyError('Could not copy', 'Please try again.'); }
  };

  return (
    <Screen>
      <Header title="Transaction receipt" onBack={() => router.back()} />

      {/* The same receipt as after a payment. Rounded on screen by this wrapper;
          the captured view inside is square, as a JPEG has no transparency. */}
      <View style={{ marginTop: 4, borderRadius: 24, overflow: 'hidden' }}>
        <ReceiptTicket ref={card} title={headline} message={subline} rows={rows} status={rawStatus} />
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
          <ReceiptActionRow onSave={() => setAction('save')} onShare={() => setAction('share')} onCopy={() => void onCopyRef()} busy={busy} />
        )}
      </View>

      <ReceiptExport
        action={action}
        onClose={() => setAction(null)}
        onBusy={setBusy}
        source={() => ({
          capture: async () => {
            const { captureRef } = await import('react-native-view-shot');
            if (!card.current) throw new Error('Receipt is not ready to capture');
            return captureRef(card.current, { format: 'jpg', quality: 0.95, result: 'tmpfile' });
          },
          html: receiptHtml({ title: headline, message: subline, rows, status: rawStatus }),
          reference,
        })}
      />
    </Screen>
  );
};

export default TxnDetail;
