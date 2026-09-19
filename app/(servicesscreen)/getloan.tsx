import React, { useEffect, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { apiPost } from '@/lib/api';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import { EP } from '@/lib/endpoints';
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
  const [token, setToken] = useState('');
  const [available, setAvailable] = useState(500000);
  const [amount, setAmount] = useState(100000);
  const [tenure, setTenure] = useState(30);
  const [rate, setRate] = useState(0.045);
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState('');
  const [recovered, setRecovered] = useState(false);
  const [txnRef, setTxnRef] = useState('');
  const [pinError, setPinError] = useState('');
  useEffect(() => {
    getToken().then((t) => {
      if (!t) return;
      setToken(t);
      apiPost(EP.loans.status)
        .then((r) => r.json())
        .then((res) => {
          if (res.available != null) setAvailable(Number(res.available));
          if (res.quote_rate) setRate(Number(res.quote_rate));
          if (res.active_loan) {
            notify('Active loan', 'You already have an active loan. Repay it from the Loans tab before taking another.');
          }
        })
        .catch(() => {});
    });
  }, []);

  const interest = Math.round(amount * rate * (tenure / 30));
  const repay = amount + interest;
  const overLimit = amount > available;

  const request = async (pin: string) => {
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
              ? 'This confirms your earlier loan request. No new loan was issued. Authorize a new request to borrow again.'
            : `${money(amount)} has been added to your wallet. Repay by the due date to boost your limit.`}
          rows={[['Loan amount', money(amount)], ['Interest', money(interest)], ['Tenure', `${tenure} days`], ['Repayment', money(repay), true]]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          onDone={() => router.replace('/home')}
        />
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
            <Pressable key={t} onPress={() => setTenure(t)} style={{ flex: 1, alignItems: 'center', paddingVertical: 14, borderRadius: 14, backgroundColor: on ? c.brand : c.surface, borderWidth: 1.5, borderColor: on ? c.brand : c.line }}>
              <Text style={{ fontFamily: font.bold, color: on ? '#fff' : c.ink1 }}>{t} days</Text>
            </Pressable>
          );
        })}
      </View>

      <View style={{ borderRadius: 16, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, paddingHorizontal: 16, paddingBottom: 4, marginBottom: 18 }}>
        <Row2 k="Interest" v={money(interest)} />
        <Row2 k="Tenure" v={`${tenure} days`} />
        <Row2 k="Total repayment" v={money(repay)} strong />
      </View>

      <Btn label={`Get ${money(amount)}`} disabled={overLimit || amount < 10000} onPress={() => setStep('confirm')} />
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
        rows={[['Amount', money(amount)], ['Interest', money(interest)], ['Tenure', `${tenure} days`], ['Repay', money(repay)]]}
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
