import React, { useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { apiJson } from '@/lib/api';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, HeaderLink } from '@/components/design/ui';
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
  const [validatedFor, setValidatedFor] = useState('');
  const [payerName, setPayerName] = useState('');
  // A validated RRR may carry a FIXED amount (most government bills do). When it
  // does, the amount field locks to it; an open-amount RRR leaves it editable.
  const [fixedAmt, setFixedAmt] = useState('');
  const [amt, setAmt] = useState('');
  const [validating, setValidating] = useState(false);
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  // The ledger reference the server minted for this transaction — shown on the
  // receipt and carried into the saved/shared file, so a support ticket can name it.
  const [txnRef, setTxnRef] = useState('');
  const [pending, setPending] = useState(false); // rail-pending: bill may still be settling
  const [pendingMessage, setPendingMessage] = useState('');
  const [recovered, setRecovered] = useState(false);
  const [pinError, setPinError] = useState('');
  const validationGeneration = useRef(0);

  const normalizedRrr = rrr.trim();
  const validated = !!normalizedRrr && validatedFor === normalizedRrr;
  const amount = Number((fixedAmt || amt) || 0);
  const valid = validated && amount >= 100 && amount <= balance;

  const validate = async () => {
    const requestedRrr = normalizedRrr;
    const generation = ++validationGeneration.current;
    setValidating(true);
    try {
      const res = await apiJson('/api/utility/validate_rrr/', { rrr: requestedRrr });
      if (generation !== validationGeneration.current) return;
      if (res.success) {
        setValidatedFor(requestedRrr);
        setPayerName(res.name || '');
        setFixedAmt(res.amount ? String(res.amount) : '');
        if (res.amount) setAmt('');
      } else {
        notify('Not found', res.message || 'Could not validate this RRR.');
      }
    } catch {
      if (generation === validationGeneration.current) {
        notify('Error', 'Something went wrong. Please try again later.');
      }
    } finally {
      if (generation === validationGeneration.current) setValidating(false);
    }
  };

  const changeRrr = (value: string) => {
    validationGeneration.current += 1;
    setValidating(false);
    setValidatedFor('');
    setPayerName('');
    setFixedAmt('');
    setAmt('');
    setRrr(value.replace(/\D/g, '').slice(0, 20));
  };

  const pay = async (pin: string) => {
    const fingerprint = [rrr.trim(), String(amount)].join('|');
    let deliveryStarted = false;
    setBusy(true);
    try {
      // Always acquire by the CURRENT validated material. This both reuses an
      // unresolved retry after restart and prevents a changed biller amount from
      // inheriting the previous amount's in-memory key.
      const requestKey = await acquireSpendAttempt('remita', fingerprint);
      deliveryStarted = true;
      const res = await apiJson('/api/utility/payremita/', {
        rrr,
        amount: String(amount),
        transaction_pin: pin,
        idempotency_key: requestKey,
      });
      // `pending` = the rail accepted but hasn't confirmed: the money is debited
      // and the bill may still settle — it is NOT auto-refunded (a maybe-paid
      // government bill must never be double-spent), so the receipt must say
      // "processing", never promise a refund. `duplicate` says only that this
      // idempotency key was already submitted; the accompanying `success` or
      // `pending` flag remains the authoritative outcome. In particular, a
      // replay of a PENDING ledger row must keep the processing receipt.
      const outcome = classifySpendResponse(res);
      if (outcome === 'success' || outcome === 'pending') {
        if (outcome === 'success') {
          await clearSpendAttempt('remita', fingerprint, requestKey);
          setRecovered(isRecoveredSpendResponse(res));
        }
        setTxnRef(String(res.reference || ''));
        setPending(outcome === 'pending');
        setPendingMessage(res.message || 'Your Remita payment is processing. Its final status will update only after provider confirmation.');
        setStep(null);
        setDone(true);
        reload();
      } else if (outcome === 'unknown') {
        setPending(true);
        setPendingMessage('We could not confirm this Remita payment. Check History before trying again.');
        setStep(null);
        setDone(true);
        reload();
      } else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') {
        setPinError(res.message || 'Incorrect PIN'); // keep key: no debit happened
      } else {
        // Only a definitive backend rejection mints a new key; on a connectivity
        // failure (`offline`) the request may have been delivered, so keep it and
        // let a retry replay server-side instead of paying twice.
        if (!res.offline) {
          await clearSpendAttempt('remita', fingerprint, requestKey);
        }
        notify('Error', res.message || 'Payment could not be completed.');
        setStep(null);
      }
    } catch {
      if (deliveryStarted) {
        setPending(true);
        setPendingMessage('We could not confirm this Remita payment. Check History before trying again.');
        setStep(null);
        setDone(true);
        reload();
      } else {
        notify('Unable to start payment', 'Could not safely prepare this request. Please try again.');
      }
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Payment processing' : recovered ? 'Earlier attempt confirmed' : 'Bill paid'}
          message={pending
            ? pendingMessage
            : recovered
              ? 'This confirms your earlier Remita payment. No new payment was made. Start a new payment to pay again.'
            : `Your Remita payment of ${money(amount)} was successful.`}
          rows={[['Type', 'Remita bill'], ['RRR', rrr], ...(payerName ? ([['Payer', payerName]] as [string, string][]) : []), ['Amount', money(amount)], ['Fee', '₦0'], ['Total', money(amount), true]]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Pay with Remita" sub="Government bills, school fees & more" onBack={() => router.back()} right={<HeaderLink label="History" onPress={() => router.push('/history')} />} />

      <Label>Remita Retrieval Reference (RRR)</Label>
      <Field
        value={rrr}
        onChangeText={changeRrr}
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

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN" protectScreen>
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => pay(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default Remita;
