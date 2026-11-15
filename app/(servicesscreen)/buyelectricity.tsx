import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { apiPost } from '@/lib/api';
import { getSessionGeneration } from '@/lib/secureStore';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import { EP } from '@/lib/endpoints';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, Naira } from '@/components/design/ui';
import { Label, Segmented, QuickAmounts, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import { DiscoRow, RecentField, ResolvedAccount } from '@/components/design/billkit';
import { useBillRecents, refreshBillRecents } from '@/lib/recents';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { normalizeAmountInput, MIN_ELECTRICITY } from '@/lib/amounts';

// Disco id mapping follows the backend's numeric convention.
const DISCOS = [
  { id: '1', name: 'Ikeja (IKEDC)', color: '#E08A00' },
  { id: '2', name: 'Eko (EKEDC)', color: '#1E5BB8' },
  { id: '3', name: 'Abuja (AEDC)', color: '#0B7A3B' },
  { id: '4', name: 'Kano (KEDCO)', color: '#7A1FA2' },
  { id: '5', name: 'P/H (PHED)', color: '#C0392B' },
  { id: '6', name: 'Jos (JED)', color: '#16667E' },
  { id: '7', name: 'Kaduna', color: '#0B4DA2' },
  { id: '8', name: 'Enugu (EEDC)', color: '#1A8E5F' },
  { id: '9', name: 'Ibadan (IBEDC)', color: '#6C2FB3' },
];
const discoInitials = (name: string) =>
  name.replace(/[^A-Za-z ]/g, '').split(' ').map((w) => w[0] || '').join('').slice(0, 2).toUpperCase();
const ELEC_AMOUNTS = [1000, 2000, 5000, 10000, 20000, 50000];

type Step = null | 'confirm' | 'pin';

/** The disco's initials on its colour, beside each recent meter. */
const DiscoTile = ({ disco }: { disco: string }) => {
  const d = DISCOS.find((x) => x.id === disco);
  if (!d) return null;
  return (
    <View style={{ width: 34, height: 34, borderRadius: 11, backgroundColor: d.color, alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
      <Text style={{ color: '#fff', fontFamily: font.extrabold, fontSize: 12 }}>{discoInitials(d.name)}</Text>
    </View>
  );
};

const BuyElectricity = () => {
  const { c } = useTheme();
  const sessionGeneration = useRef(getSessionGeneration());
  const isCurrentSession = () => sessionGeneration.current === getSessionGeneration();
  const { balance, reload, billPaymentsAvailable } = useWallet();
  const [disco, setDisco] = useState('1');
  const [meterType, setMeterType] = useState('prepaid');
  const [meter, setMeter] = useState('');
  const [amt, setAmt] = useState('');
  const [customerName, setCustomerName] = useState('');
  const [customerAddress, setCustomerAddress] = useState('');
  const [validating, setValidating] = useState(false);
  const [validatedFor, setValidatedFor] = useState('');
  // The meter check that last failed, so "Try again" shows only for that one.
  const [checkFailedFor, setCheckFailedFor] = useState('');
  const [purchasedToken, setPurchasedToken] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState('');
  const [recovered, setRecovered] = useState(false);
  const [txnRef, setTxnRef] = useState('');
  const [pinError, setPinError] = useState('');
  const validationGeneration = useRef(0);
  const purchaseInFlight = useRef(false);
  const reviewedPayment = useRef<string | null>(null);

  const provider = DISCOS.find((d) => d.id === disco)!;
  const amount = Number(amt || 0);
  const validationKey = `${disco}|${meterType}|${meter.trim()}`;
  const verifiedCustomer = validatedFor === validationKey ? customerName : '';
  const valid = billPaymentsAvailable === true && meter.length >= 8 && !!verifiedCustomer && amount >= MIN_ELECTRICITY && amount <= balance;

  const validateMeter = async () => {
    if (!isCurrentSession()) return;
    if (meter.trim().length < 8) { notify('Error', 'Enter a valid meter number.'); return; }
    const requestedMeter = meter.trim();
    const requestedDisco = disco;
    const requestedType = meterType;
    const requestedFor = `${requestedDisco}|${requestedType}|${requestedMeter}`;
    const generation = ++validationGeneration.current;
    setValidating(true);
    setCheckFailedFor('');
    try {
      const response = await apiPost(EP.utility.validateMeter, {
        meter: requestedMeter,
        disco: requestedDisco,
        meter_type: requestedType,
      });
      const result = await response.json();
      if (!isCurrentSession() || generation !== validationGeneration.current) return;
      if (response.ok && result.success === true) {
        setCustomerName(result.customer_name || result.name || 'Verified');
        setCustomerAddress(String(result.customer_address || result.address || ''));
        setValidatedFor(requestedFor);
      } else {
        setCheckFailedFor(requestedFor);
        notify('Error', result.message || 'Could not verify meter number.');
      }
    } catch {
      if (isCurrentSession() && generation === validationGeneration.current) {
        setCheckFailedFor(requestedFor);
        notify('Error', 'Something went wrong. Please try again later.');
      }
    } finally {
      if (isCurrentSession() && generation === validationGeneration.current) setValidating(false);
    }
  };

  const invalidateMeter = () => {
    validationGeneration.current += 1;
    setValidating(false);
    setCustomerName('');
    setCustomerAddress('');
    setValidatedFor('');
    setCheckFailedFor('');
  };

  // The screen opens on the disco of the last meter paid, and the meter field
  // with the last meter paid on that disco, listing the others; the name and
  // address resolve as soon as the number is complete.
  const recents = useBillRecents();
  const meterEdited = useRef(false);
  const discoChosen = useRef(false);
  const meters = recents.meters.filter((r) => r.disco === disco);
  useEffect(() => {
    if (meterEdited.current) return;
    if (!discoChosen.current && recents.meters.length) {
      discoChosen.current = true;
      const lastDisco = recents.meters[0].disco;
      if (lastDisco !== disco && DISCOS.some((d) => d.id === lastDisco)) {
        // The effect runs again for the new disco and fills its last meter.
        setDisco(lastDisco);
        return;
      }
    }
    const last = recents.meters.find((r) => r.disco === disco);
    invalidateMeter();
    setMeter(last ? last.meter : '');
    if (last) setMeterType(last.meter_type);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recents, disco]);
  const attempted = useRef('');
  useEffect(() => {
    if (meter.length >= 10 && !verifiedCustomer && !validating && attempted.current !== validationKey) {
      attempted.current = validationKey;
      validateMeter();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [validationKey, verifiedCustomer, validating]);

  const changeDisco = (value: string) => {
    discoChosen.current = true;
    invalidateMeter();
    meterEdited.current = false;
    setDisco(value);
  };

  const changeMeterType = (value: string) => {
    invalidateMeter();
    setMeterType(value);
  };

  const changeMeter = (value: string) => {
    invalidateMeter();
    meterEdited.current = true;
    setMeter(value.replace(/\D/g, '').slice(0, 13));
  };

  const purchase = async (enteredPin: string) => {
    if (!isCurrentSession() || !valid || done || purchaseInFlight.current) return;
    const fingerprint = [disco, meterType, meter.trim(), String(amount)].join('|');
    if (reviewedPayment.current !== null && reviewedPayment.current !== JSON.stringify([fingerprint, amount])) {
      setStep('confirm');
      notify('Review payment again', 'The payment details changed. Check them before entering your PIN.');
      return;
    }
    purchaseInFlight.current = true;
    let deliveryStarted = false;
    setBusy(true);
    try {
      const requestKey = await acquireSpendAttempt('electricity', fingerprint);
      if (!isCurrentSession()) return;
      deliveryStarted = true;
      const response = await apiPost(EP.utility.buyElectricity, {
        disco,
        meter,
        meter_type: meterType,
        amount: amt,
        transaction_pin: enteredPin,
        idempotency_key: requestKey,
      });
      const result = await response.json();
      if (!isCurrentSession()) return;
      const outcome = classifySpendResponse(result, response.status);
      if (outcome === 'success') {
        await clearSpendAttempt('electricity', fingerprint, requestKey);
        if (!isCurrentSession()) return;
        setRecovered(isRecoveredSpendResponse(result, response.status));
        setTxnRef(String(result.reference || ''));
        if (result.token) setPurchasedToken(String(result.token));
        setStep(null);
        setDone(true);
        refreshBillRecents();
        reload();
      } else if (outcome === 'pending' || outcome === 'unknown') {
        setPending(true);
        setPendingMessage(outcome === 'pending'
          ? (result.message || 'Your electricity payment is processing. Its final status will update only after provider confirmation.')
          : 'We could not confirm this electricity payment. Check History before trying again.');
        setTxnRef(String(result.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (result.code === 'pin_incorrect' || result.code === 'pin_locked') {
        setPinError(result.message || 'Incorrect PIN');  // keep key: no debit happened
      } else {
        await clearSpendAttempt('electricity', fingerprint, requestKey);
        if (!isCurrentSession()) return;
        notify('Error', result.message || 'Transaction failed');
        setStep(null);
      }
    } catch {
      if (!isCurrentSession()) return;
      if (deliveryStarted) {
        setPending(true);
        setPendingMessage('We could not confirm this electricity payment. Check History before trying again.');
        setStep(null);
        setDone(true);
        reload();
      } else {
        notify('Unable to start payment', 'Could not safely prepare this request. Please try again.');
      }
    } finally {
      purchaseInFlight.current = false;
      if (isCurrentSession()) setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Payment processing' : recovered ? 'Earlier attempt confirmed' : purchasedToken ? 'Token generated' : 'Payment successful'}
          message={pending
            ? pendingMessage
            : recovered
              ? 'This confirms your earlier electricity payment. No new payment was made. Start a new purchase to pay again.'
            : `Your ${provider.name} ${meterType} purchase was successful.`}
          rows={[
            ['Customer', verifiedCustomer],
            ['Disco', provider.name],
            ['Meter', meter],
            ['Type', meterType],
            ...(purchasedToken ? ([['Token', purchasedToken]] as [string, string][]) : []),
            ['Total', money(amount), true],
          ]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          footer={pending ? <Btn label="Check transaction history" variant="outline" onPress={() => router.push('/history')} /> : undefined}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Electricity" onBack={() => router.back()} />

      <Label>Select disco</Label>
      <DiscoRow items={DISCOS} value={disco} onPick={changeDisco} />

      <Segmented
        options={[{ v: 'prepaid', label: 'Prepaid' }, { v: 'postpaid', label: 'Postpaid' }]}
        value={meterType}
        onChange={changeMeterType}
      />

      <RecentField
        label="Meter number"
        value={meter}
        onChangeText={changeMeter}
        onPick={(it) => {
          const r = meters.find((m) => m.meter === it.value);
          changeMeter(it.value);
          if (r) setMeterType(r.meter_type);
        }}
        items={meters.slice(0, 5).map((r) => ({
          value: r.meter,
          leading: <DiscoTile disco={r.disco} />,
          sub: [DISCOS.find((d) => d.id === r.disco)?.name.replace(/ \(.*\)$/, '') + ' Electric', r.name].filter(Boolean).join(' · '),
        }))}
        menuTitle="Recent meter numbers"
        anotherLabel="Use another meter number"
        tag={meter && meters.some((r) => r.meter === meter) ? 'Last used' : undefined}
        placeholder="01234567890"
        maxLength={13}
      />
      <View style={{ marginBottom: 8 }}>
        {verifiedCustomer ? (
          <ResolvedAccount name={verifiedCustomer} detail={customerAddress} />
        ) : validating ? (
          // The meter resolves on its own once the number is complete.
          <Text style={{ marginTop: 8, color: c.ink3, fontFamily: font.regular, fontSize: 12.5 }}>Checking…</Text>
        ) : checkFailedFor === validationKey ? (
          <View style={{ marginTop: 4, alignSelf: 'flex-start' }}>
            <Btn label="Try again" variant="ghost" size="sm" full={false} onPress={validateMeter} style={{ backgroundColor: 'transparent', paddingHorizontal: 0 }} />
          </View>
        ) : meter.length >= 8 && meter.length < 10 ? (
          // Shorter meters are valid but not checked on their own (the auto
          // check waits for 10 digits so it does not fire mid-typing).
          <View style={{ marginTop: 4, alignSelf: 'flex-start' }}>
            <Btn label="Check meter" variant="ghost" size="sm" full={false} onPress={validateMeter} style={{ backgroundColor: 'transparent', paddingHorizontal: 0 }} />
          </View>
        ) : null}
      </View>

      <Label>Amount</Label>
      <QuickAmounts amounts={ELEC_AMOUNTS} value={amt} onPick={setAmt} />
      <Field
        tint
        value={amt}
        onChangeText={(v) => { const normalized = normalizeAmountInput(v); if (normalized !== null) setAmt(normalized); }}
        keyboardType="decimal-pad"
        placeholder="Enter amount (min 500)"
        prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />}
      />
      <View style={{ height: 6 }} />
      <BalanceHint amount={amount} balance={balance} min={MIN_ELECTRICITY} />
      {billPaymentsAvailable !== true ? <Text style={{ color: c.ink3, fontFamily: font.regular, marginBottom: 12 }}>Bill payments are currently unavailable. Refresh your wallet or try again later.</Text> : null}

      <Btn label="Continue" disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm payment"
        total={amount}
        balance={balance}
        rows={[['Customer', verifiedCustomer], ['Disco', provider.name], ['Meter', meter], ['Type', meterType]]}
        onPay={() => { reviewedPayment.current = JSON.stringify([[disco, meterType, meter.trim(), String(amount)].join('|'), amount]); setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet protectScreen open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, lineHeight: 20, color: c.ink3, marginBottom: 16, marginTop: 0, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => purchase(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default BuyElectricity;
