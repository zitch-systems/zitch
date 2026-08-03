import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { apiJson, newIdempotencyKey } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Sheet, PinPad, money } from '@/components/design/ui';
import { Label, ConfirmSheet, BalanceHint, AmountField } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

type Step = null | 'confirm' | 'pin';

const Remita = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();

  const [rrr, setRrr] = useState('');
  const [validated, setValidated] = useState(false);
  const [payerName, setPayerName] = useState('');
  // A validated RRR may carry a FIXED amount (most government bills do). When it
  // does, the amount field locks to it; an open-amount RRR leaves it editable.
  const [fixedAmt, setFixedAmt] = useState('');
  const [amt, setAmt] = useState('');
  const [validating, setValidating] = useState(false);
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false); // rail-pending: bill may still be settling
  const [pinError, setPinError] = useState('');
  const idemKey = useRef(''); // stable across retries of one payment attempt

  // Editing the RRR invalidates the previous lookup.
  useEffect(() => {
    const timer = setTimeout(() => {
      setValidated(false);
      setPayerName('');
      setFixedAmt('');
    }, 0);
    return () => clearTimeout(timer);
  }, [rrr]);

  // Any edit to the payment details is a new spend — drop the retained key so a
  // stale one can't replay the PRIOR payment for the edited one (mirrors sendmoney).
  useEffect(() => { idemKey.current = ''; }, [rrr, amt]);

  const amount = Number((fixedAmt || amt) || 0);
  const valid = validated && amount >= 100 && amount <= balance;

  const validate = async () => {
    setValidating(true);
    try {
      const res = await apiJson('/api/utility/validate_rrr/', { rrr });
      if (res.success) {
        setValidated(true);
        setPayerName(res.name || '');
        if (res.amount) { setFixedAmt(String(res.amount)); setAmt(''); }
      } else {
        notify('Not found', res.message || 'Could not validate this RRR.');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setValidating(false);
    }
  };

  const pay = async (pin: string) => {
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      const res = await apiJson('/api/utility/payremita/', {
        rrr,
        amount: String(amount),
        transaction_pin: pin,
        idempotency_key: idemKey.current,
      });
      // `pending` = the rail accepted but hasn't confirmed: the money is debited
      // and the bill may still settle — it is NOT auto-refunded (a maybe-paid
      // government bill must never be double-spent), so the receipt must say
      // "processing", never promise a refund. `duplicate` = idempotent replay of
      // a completed attempt — the payment DID go through.
      if (res.success || res.pending || res.duplicate) {
        idemKey.current = '';
        setPending(!res.success && !!res.pending && !res.duplicate);
        setStep(null);
        setDone(true);
        reload();
      } else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') {
        setPinError(res.message || 'Incorrect PIN'); // keep key: no debit happened
      } else {
        // Only a definitive backend rejection mints a new key; on a connectivity
        // failure (`offline`) the request may have been delivered, so keep it and
        // let a retry replay server-side instead of paying twice.
        if (!res.offline) idemKey.current = '';
        notify('Error', res.message || 'Payment could not be completed.');
        setStep(null);
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
      setStep(null);
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Payment processing' : 'Bill paid'}
          message={pending
            ? `Your Remita payment of ${money(amount)} is processing and will be confirmed shortly. Keep your RRR — the biller will reflect it once settled.`
            : `Your Remita payment of ${money(amount)} was successful.`}
          rows={[['Type', 'Remita bill'], ['RRR', rrr], ...(payerName ? ([['Payer', payerName]] as [string, string][]) : []), ['Amount', money(amount)], ['Fee', '₦0'], ['Total', money(amount), true]]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Pay with Remita" sub="Government bills, school fees & more" onBack={() => router.back()} />

      <Label>Remita Retrieval Reference (RRR)</Label>
      <Field
        value={rrr}
        onChangeText={(v) => setRrr(v.replace(/\D/g, '').slice(0, 20))}
        keyboardType="number-pad"
        placeholder="Enter the RRR on your bill"
        prefix={<ZIcon name="bills" size={18} color={c.ink3} />}
      />
      <View style={{ height: 10 }} />
      {validated ? (
        <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 12.5, marginBottom: 8 }}>
          ✓ {payerName || 'RRR verified'}{fixedAmt ? ` · ${money(Number(fixedAmt))}` : ''}
        </Text>
      ) : (
        <Btn label={validating ? 'Checking…' : 'Verify RRR'} variant="outline" size="sm" full={false} disabled={validating || rrr.length < 10} onPress={validate} />
      )}

      <View style={{ height: 14 }} />
      <Label>Amount</Label>
      <AmountField
        value={fixedAmt ? String(fixedAmt) : amt}
        onChangeText={setAmt}
        editable={!fixedAmt}
        placeholder={validated ? 'Enter amount' : 'Verify the RRR first'}
      />
      {fixedAmt ? (
        <Text style={{ color: c.ink3, fontFamily: font.regular, fontSize: 12, marginTop: 6 }}>
          This bill has a fixed amount set by the biller.
        </Text>
      ) : null}
      <View style={{ height: 6 }} />
      <BalanceHint amount={amount} balance={balance} />

      <Btn label={amount > 0 ? `Pay · ${money(amount)}` : 'Pay'} disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm Remita payment"
        total={amount}
        balance={balance}
        rows={[['RRR', rrr], ...(payerName ? ([['Payer', payerName]] as [string, string][]) : []), ['Amount', money(amount)]]}
        onPay={() => { setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => pay(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default Remita;
