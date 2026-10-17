import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router } from 'expo-router';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import { loansService } from '@/lib/services/loans';
import { Screen, Header, Btn, Sheet, PinPad, Field, money, Naira } from '@/components/design/ui';
import { Label, ConfirmSheet } from '@/components/design/flowkit';
import { Hero } from '@/components/design/widgets';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const PRESETS = [20000, 50000, 100000, 200000, 350000, 500000];
const TENURES = [15, 30, 60];
type Step = null | 'confirm' | 'pin';
type ConfirmedLoan = { principal: number; interest: number; repayment: number; days: number };

const confirmedLoanFrom = (value: unknown): ConfirmedLoan | null => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const loan = value as Record<string, unknown>;
  const fields = ['principal', 'interest', 'total_repayment', 'tenure_days'];
  if (fields.some((key) => (typeof loan[key] !== 'number' && typeof loan[key] !== 'string')
    || String(loan[key]).trim() === '' || !Number.isFinite(Number(loan[key])))) return null;
  const result = { principal: Number(loan.principal), interest: Number(loan.interest), repayment: Number(loan.total_repayment), days: Number(loan.tenure_days) };
  if (result.principal <= 0 || result.interest < 0 || !Number.isInteger(result.days) || result.days <= 0
    || Math.abs(result.repayment - result.principal - result.interest) > 0.005) return null;
  return result;
};

const Row2 = ({ k, v, strong }: { k: string; v: string; strong?: boolean }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 11, borderTopWidth: 1, borderTopColor: c.line }}>
      <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>{k}</Text>
      <Text style={{ fontSize: strong ? 16 : 14, fontFamily: strong ? font.extrabold : font.semibold, color: c.ink1, fontVariant: ['tabular-nums'] }}>{v}</Text>
    </View>
  );
};

const GetLoan = () => {
  const { c } = useTheme();
  const { reload } = useWallet();
  const [available, setAvailable] = useState(0);
  const [amount, setAmount] = useState(100000);
  const [tenure, setTenure] = useState(30);
  const [rate, setRate] = useState(0);
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState('');
  const [recovered, setRecovered] = useState(false);
  const [txnRef, setTxnRef] = useState('');
  const [confirmedLoan, setConfirmedLoan] = useState<ConfirmedLoan | null>(null);
  const [pinError, setPinError] = useState('');
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [statusError, setStatusError] = useState('');
  const [productAvailable, setProductAvailable] = useState(false);
  const [unavailableMessage, setUnavailableMessage] = useState('Loans are not available right now.');
  const [hasActiveLoan, setHasActiveLoan] = useState(false);
  const requestInFlight = useRef(false);
  const statusInFlight = useRef(false);
  const loadStatus = useCallback(async () => {
    if (statusInFlight.current) return;
    statusInFlight.current = true;
    setLoadingStatus(true);
    setProductAvailable(false);
    try {
      const res = await loansService.getStatus();
      if (res?.success !== true || typeof res.product_available !== 'boolean') {
        throw new Error('Loan availability unknown');
      }
      if (res.product_available && (res.quote_rate == null
        || !Number.isFinite(Number(res.quote_rate)) || Number(res.quote_rate) < 0
        || res.available == null || !Number.isFinite(Number(res.available)) || Number(res.available) < 0)) {
        throw new Error('Loan terms unavailable');
      }
      setProductAvailable(res.product_available && Number(res.available) > 0);
      setUnavailableMessage(res.unavailable_message || (res.product_available
        ? 'You do not have an available credit limit right now.'
        : 'Loans are not available right now.'));
      if (res.available != null) setAvailable(Number(res.available));
      if (res.quote_rate != null) setRate(Number(res.quote_rate));
      setHasActiveLoan(Boolean(res.active_loan));
      setStatusError('');
    } catch {
      setStatusError('We could not check your loan status. Please try again.');
    } finally {
      statusInFlight.current = false;
      setLoadingStatus(false);
    }
  }, []);

  useEffect(() => { void loadStatus(); }, [loadStatus]);

  const interest = Math.round(amount * rate * (tenure / 30) * 100) / 100;
  const repay = amount + interest;
  const overLimit = amount > available;
  const valid = productAvailable && !hasActiveLoan && Number.isFinite(amount) && amount >= 10000 && !overLimit;

  const request = async (pin: string) => {
    if (!valid || done || requestInFlight.current) return;
    requestInFlight.current = true;
    const fingerprint = [String(amount), String(tenure)].join('|');
    let deliveryStarted = false;
    setBusy(true);
    try {
      const requestKey = await acquireSpendAttempt('loan-request', fingerprint);
      deliveryStarted = true;
      const res = await loansService.request(amount, tenure, pin, requestKey);
      const outcome = classifySpendResponse(res);
      if (outcome === 'success') {
        await clearSpendAttempt('loan-request', fingerprint, requestKey);
        setRecovered(isRecoveredSpendResponse(res));
        setConfirmedLoan(confirmedLoanFrom('loan' in res ? res.loan : undefined));
        setTxnRef(String(res.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (outcome === 'pending' || outcome === 'unknown') {
        setPending(true);
        setPendingMessage(outcome === 'pending'
          ? (res.message || 'Your loan request is processing. Its final status will update only after provider confirmation.')
          : 'We could not confirm this loan request. Check your Loans page before trying again.');
        setTxnRef(String(res.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') {
        setPinError(res.message || 'Incorrect PIN');
      } else {
        await clearSpendAttempt('loan-request', fingerprint, requestKey);
        notify('Error', res.message || 'Loan request failed');
        setStep(null);
      }
    } catch {
      if (deliveryStarted) {
        setPending(true);
        setPendingMessage('We could not confirm this loan request. Check your Loans page before trying again.');
        setStep(null);
        setDone(true);
        reload();
      } else {
        notify('Unable to start loan request', 'Could not safely prepare this request. Please try again.');
      }
    } finally {
      requestInFlight.current = false;
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Loan request processing' : recovered ? 'Earlier attempt confirmed' : 'Loan disbursed'}
          message={pending
            ? pendingMessage
            : recovered
              ? 'This confirms your earlier loan request. No new loan was issued. Open Loans to view the confirmed terms and current balance.'
              : confirmedLoan
                ? `${money(confirmedLoan.principal)} has been added to your wallet. Open Loans to view the due date and current balance.`
                : 'Your loan request was confirmed. Open Loans to view the confirmed amounts and repayment terms.'}
          rows={pending
            ? [['Requested loan amount', money(amount)], ['Requested tenure', `${tenure} days`]]
            : confirmedLoan
              ? [['Loan amount', money(confirmedLoan.principal)], ['Interest', money(confirmedLoan.interest)], ['Tenure', `${confirmedLoan.days} days`], ['Total repayment', money(confirmedLoan.repayment), true]]
              : [['Confirmed terms', 'View Loans']]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          onDone={() => router.replace('/loan')}
        />
      </Screen>
    );
  }

  if (loadingStatus) {
    return (
      <Screen scroll={false}>
        <Header title="Get Loan" onBack={() => router.back()} />
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ color: c.ink3, fontFamily: font.regular }}>Checking loan availability…</Text>
        </View>
      </Screen>
    );
  }

  if (statusError) {
    return (
      <Screen>
        <Header title="Get Loan" onBack={() => router.back()} />
        <View style={{ paddingVertical: 36 }}>
          <Text accessibilityRole="alert" style={{ fontFamily: font.bold, color: c.ink1, fontSize: 18 }}>Unable to check loan status</Text>
          <Text style={{ color: c.ink3, fontFamily: font.regular, lineHeight: 20, marginTop: 8, marginBottom: 20 }}>{statusError}</Text>
          <Btn label="Retry" onPress={() => { void loadStatus(); }} />
          <View style={{ marginTop: 12 }}><Btn label="View loans" variant="outline" onPress={() => router.replace('/loan')} /></View>
        </View>
      </Screen>
    );
  }

  if (!productAvailable || hasActiveLoan) {
    return (
      <Screen>
        <Header title="Get Loan" onBack={() => router.back()} />
        <View style={{ alignItems: 'center', paddingHorizontal: 18, paddingTop: 46 }}>
          <View style={{ width: 72, height: 72, borderRadius: 24, backgroundColor: c.surface3, alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ fontSize: 28 }}>₦</Text>
          </View>
          <Text style={{ fontSize: 18, fontFamily: font.bold, color: c.ink1, marginTop: 18 }}>{hasActiveLoan ? 'You already have an active loan' : 'New loans unavailable'}</Text>
          <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular, lineHeight: 20, textAlign: 'center', marginTop: 8 }}>
            {hasActiveLoan ? 'View your loan status and repayment options from the Loans tab.' : unavailableMessage}
          </Text>
          <View style={{ width: '100%', marginTop: 22 }}>
            <Btn label="View loans" variant="outline" onPress={() => router.replace('/loan')} />
          </View>
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Get Loan" sub="Instant, no paperwork" onBack={() => router.back()} />

      <Hero style={{ marginBottom: 18 }}>
        <Text style={{ fontSize: 13, color: 'rgba(255,255,255,.85)', fontFamily: font.regular }}>You&apos;re eligible for up to</Text>
        <Text style={{ fontSize: 34, fontFamily: font.extrabold, color: '#fff', marginTop: 4, fontVariant: ['tabular-nums'] }}>{money(available)}</Text>
        <Text style={{ fontSize: 12.5, color: 'rgba(255,255,255,.85)', marginTop: 6, fontFamily: font.regular }}>Based on your Zitch activity & repayment history</Text>
      </Hero>

      <Label>How much do you need?</Label>
      <Text style={{ fontSize: 32, fontFamily: font.extrabold, color: c.brand, textAlign: 'center', marginBottom: 12, fontVariant: ['tabular-nums'] }}>{money(amount)}</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -5, marginBottom: 12 }}>
        {PRESETS.map((p) => {
          const on = amount === p;
          const disabled = p > available;
          return (
            <View key={p} style={{ width: '33.33%', padding: 5 }}>
              <Pressable
                accessibilityRole="radio"
                accessibilityLabel={money(p)}
                accessibilityState={{ selected: on, disabled }}
                disabled={disabled}
                onPress={() => !disabled && setAmount(p)}
                style={{ alignItems: 'center', paddingVertical: 13, borderRadius: 13, backgroundColor: on ? c.brand : c.surface, borderWidth: 1.5, borderColor: on ? c.brand : c.line, opacity: disabled ? 0.4 : 1 }}
              >
                <Text style={{ fontSize: 14, fontFamily: font.bold, color: on ? '#fff' : c.ink1, fontVariant: ['tabular-nums'] }}><Naira />{(p / 1000)}k</Text>
              </Pressable>
            </View>
          );
        })}
      </View>

      {/* Design uses a range slider (min 10000, max 500000, step 5000). The native
          @react-native-community/slider package is NOT installed and we must not
          add a new native dependency here, so the documented fallback is used:
          the quick-amount chips above + this free-text amount field, both honoring
          the same 10000–available range. Swap to <Slider/> once the dep is added. */}
      <Field
        label={`Or enter an amount (up to ${money(available)})`}
        value={amount ? String(amount) : ''}
        onChangeText={(v) => setAmount(Number(v.replace(/\D/g, '')) || 0)}
        keyboardType="number-pad"
        placeholder="e.g. 75000"
        prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontFamily: font.bold }} />}
      />
      {amount > 0 && amount < 10000 ? (
        <Text style={{ fontSize: 12, color: c.amber, fontFamily: font.medium, marginTop: 6 }}>Minimum loan is {money(10000)}.</Text>
      ) : null}
      <View style={{ height: 18 }} />

      <Label>Repayment period</Label>
      <View style={{ flexDirection: 'row', gap: 10, marginBottom: 18 }}>
        {TENURES.map((t) => {
          const on = tenure === t;
          return (
            <Pressable key={t} onPress={() => setTenure(t)} accessibilityRole="radio" accessibilityLabel={`${t} days`} accessibilityState={{ selected: on }} style={{ flex: 1, alignItems: 'center', paddingVertical: 14, borderRadius: 14, backgroundColor: on ? c.brand : c.surface, borderWidth: 1.5, borderColor: on ? c.brand : c.line }}>
              <Text style={{ fontFamily: font.bold, color: on ? '#fff' : c.ink1 }}>{t} days</Text>
            </Pressable>
          );
        })}
      </View>

      <View style={{ borderRadius: 16, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, paddingHorizontal: 16, paddingBottom: 4, marginBottom: 18 }}>
        <Row2 k="Estimated interest" v={money(interest)} />
        <Row2 k="Tenure" v={`${tenure} days`} />
        <Row2 k="Estimated repayment" v={money(repay)} strong />
      </View>

      <Btn label={`Get ${money(amount)}`} disabled={!valid} onPress={() => setStep('confirm')} />
      {overLimit && (
        <Text style={{ fontSize: 12.5, color: c.red, marginTop: 10, textAlign: 'center', fontFamily: font.semibold }}>
          Amount exceeds your available credit
        </Text>
      )}

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm loan"
        total={amount}
        balance={available}
        rows={[['Amount', money(amount)], ['Estimated interest', money(interest)], ['Tenure', `${tenure} days`], ['Estimated repayment', money(repay)]]}
        onPay={() => { setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Processing…' : `Authorize loan of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => request(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default GetLoan;
