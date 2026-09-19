import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { Loading } from '@/components/design/Loading';
import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { apiPost } from '@/lib/api';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import { EP } from '@/lib/endpoints';
import { Screen, Header, Field, Btn, Sheet, PinPad, money } from '@/components/design/ui';
import { Label, ProviderGrid, PlanList, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const PROVIDERS = [
  { id: '1', name: 'GoTV', color: '#92C020', logo: require('@/assets/images/providers/gotv.png') },
  { id: '2', name: 'DSTV', color: '#0A66C2', logo: require('@/assets/images/providers/dstv.png') },
  { id: '3', name: 'StarTimes', color: '#F47B20', logo: require('@/assets/images/providers/startimes.png') },
  // Showmax has no raster logo asset yet; ProviderGrid renders an initials tile
  // in its brand colour as a fallback. id '4' follows the sequential cablenetwork
  // codes used by the backend (1=GoTV, 2=DSTV, 3=StarTimes).
  { id: '4', name: 'Showmax', color: '#1A1A2E' },
];

type Step = null | 'confirm' | 'pin';

const BuyCable = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const [prov, setProv] = useState('1');
  const [iuc, setIuc] = useState('');
  const [plan, setPlan] = useState('');
  const [price, setPrice] = useState('');
  const [priceFor, setPriceFor] = useState('');
  const [plans, setPlans] = useState<{ id: string; label: string; sub?: string; price: number }[]>([]);
  const [plansFor, setPlansFor] = useState('');
  const [loadingPlans, setLoadingPlans] = useState(false);
  const [validatedName, setValidatedName] = useState('');
  const [validatedFor, setValidatedFor] = useState('');
  const [validating, setValidating] = useState(false);
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState('');
  const [recovered, setRecovered] = useState(false);
  const [txnRef, setTxnRef] = useState('');
  const [pinError, setPinError] = useState('');
  const validationGeneration = useRef(0);

  // Fetch bouquets for the chosen provider.
  useEffect(() => {
    if (!prov) return;
    const requestedProvider = prov;
    let current = true;
    setLoadingPlans(true);
    setPlan('');
    setPlans([]);
    setPlansFor('');
    fetch(`${baseUrl}/api/utility/get_cable_plans/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cablenetwork: prov }),
    })
      .then((r) => r.json())
      .then((res) => {
        if (current && res?.cable_plans) {
          setPlans(res.cable_plans.map((p: any) => ({
            id: String(p.cable_plan_code),
            label: p.name,
            sub: p.validity,
            price: Number(p.price ?? 0),
          })));
          setPlansFor(requestedProvider);
        }
      })
      .catch(() => {})
      .finally(() => { if (current) setLoadingPlans(false); });
    return () => { current = false; };
  }, [prov]);

  // Authoritative price for the chosen bouquet.
  useEffect(() => {
    setPrice('');
    setPriceFor('');
    if (!plan) return;
    const requestedFor = `${prov}|${plan}`;
    let current = true;
    fetch(`${baseUrl}/api/utility/get_cable_plans_price/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cable_plan_code: plan }),
    })
      .then((r) => r.json())
      .then((res) => {
        if (current && res?.cable_plans_price != null) {
          setPrice(String(res.cable_plans_price));
          setPriceFor(requestedFor);
        }
      })
      .catch(() => {});
    return () => { current = false; };
  }, [prov, plan]);

  const provider = PROVIDERS.find((p) => p.id === prov)!;
  const currentPlans = plansFor === prov ? plans : [];
  const planObj = currentPlans.find((p) => p.id === plan);
  const currentPrice = priceFor === `${prov}|${plan}` ? price : '';
  const amount = Number(currentPrice || 0);
  const hasAuthoritativePrice = currentPrice !== '' && Number.isFinite(amount) && amount > 0;
  const validationKey = `${prov}|${iuc.trim()}`;
  const verifiedName = validatedFor === validationKey ? validatedName : '';
  const valid = iuc.length >= 8 && !!planObj && !!verifiedName && hasAuthoritativePrice && amount <= balance;

  // Auto-resolve the customer name once the smartcard reaches a plausible length
  // (most NUBAN-style IUCs are 10-11 digits). The manual button stays as a
  // fallback. attemptedRef stops the effect from re-firing the API on every
  // keystroke or while a request is already in flight.
  const attemptedRef = useRef('');
  useEffect(() => {
    if (iuc.length >= 10 && !verifiedName && !validating && attemptedRef.current !== `${prov}:${iuc}`) {
      attemptedRef.current = `${prov}:${iuc}`;
      validateIuc();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [iuc, prov, verifiedName, validating]);

  const validateIuc = async () => {
    if (iuc.trim().length < 8) { notify('Error', 'Enter a valid IUC / smartcard number.'); return; }
    const requestedIuc = iuc.trim();
    const requestedProvider = prov;
    const requestedFor = `${requestedProvider}|${requestedIuc}`;
    const generation = ++validationGeneration.current;
    setValidating(true);
    try {
      const response = await apiPost(EP.utility.validateIuc, {
        iuc: requestedIuc,
        cablenetwork: requestedProvider,
      });
      const result = await response.json();
      if (generation !== validationGeneration.current) return;
      if (response.ok) {
        setValidatedName(result.customer_name || result.name || 'Verified');
        setValidatedFor(requestedFor);
      } else {
        notify('Error', result.message || 'Could not verify this IUC number.');
      }
    } catch {
      if (generation === validationGeneration.current) {
        notify('Error', 'Something went wrong. Please try again later.');
      }
    } finally {
      if (generation === validationGeneration.current) setValidating(false);
    }
  };

  const changeProvider = (value: string) => {
    validationGeneration.current += 1;
    setValidating(false);
    setValidatedName('');
    setValidatedFor('');
    setProv(value);
  };

  const changeIuc = (value: string) => {
    validationGeneration.current += 1;
    setValidating(false);
    setValidatedName('');
    setValidatedFor('');
    setIuc(value.replace(/\D/g, '').slice(0, 12));
  };

  const purchase = async (enteredPin: string) => {
    const fingerprint = [prov, plan, iuc.trim()].join('|');
    let deliveryStarted = false;
    setBusy(true);
    try {
      const requestKey = await acquireSpendAttempt('cable', fingerprint);
      deliveryStarted = true;
      const response = await apiPost(EP.utility.buyCable, {
        iuc,
        cablenetwork: prov,
        selectedcablePlan: plan,
        transaction_pin: enteredPin,
        idempotency_key: requestKey,
      });
      const result = await response.json();
      const outcome = classifySpendResponse(result, response.status);
      if (outcome === 'success') {
        await clearSpendAttempt('cable', fingerprint, requestKey);
        setRecovered(isRecoveredSpendResponse(result, response.status));
        setTxnRef(String(result.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (outcome === 'pending' || outcome === 'unknown') {
        setPending(true);
        setPendingMessage(outcome === 'pending'
          ? (result.message || 'Your subscription is processing. Its final status will update only after provider confirmation.')
          : 'We could not confirm this subscription. Check History before trying again.');
        setTxnRef(String(result.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (result.code === 'pin_incorrect' || result.code === 'pin_locked') {
        setPinError(result.message || 'Incorrect PIN');  // keep key: no debit happened
      } else {
        await clearSpendAttempt('cable', fingerprint, requestKey);
        notify('Error', result.message || 'Transaction failed');
        setStep(null);
      }
    } catch {
      if (deliveryStarted) {
        setPending(true);
        setPendingMessage('We could not confirm this subscription. Check History before trying again.');
        setStep(null);
        setDone(true);
        reload();
      } else {
        notify('Unable to start subscription', 'Could not safely prepare this request. Please try again.');
      }
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Subscription processing' : recovered ? 'Earlier attempt confirmed' : 'Subscription active'}
          message={pending
            ? pendingMessage
            : recovered
              ? 'This confirms your earlier subscription. No new subscription was purchased. Start a new purchase to subscribe again.'
            : `${provider.name} ${planObj?.label || ''} on ${iuc} is now active.`}
          rows={[['Provider', provider.name], ['Smartcard / IUC', iuc], ['Plan', planObj?.label || '—'], ['Total', money(amount), true]]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Cable TV" onBack={() => router.back()} />

      <Label>Select provider</Label>
      <ProviderGrid items={PROVIDERS} value={prov} onPick={changeProvider} cols={4} />

      <Field
        label="Smartcard / IUC number"
        value={iuc}
        onChangeText={changeIuc}
        keyboardType="number-pad"
        placeholder="1234 5678 90"
      />
      <View style={{ marginTop: 8, marginBottom: 8 }}>
        {verifiedName ? (
          <Text style={{ color: c.brandDeep, fontFamily: font.semibold, fontSize: 12.5 }}>✓ {verifiedName}</Text>
        ) : (
          <Btn label="Validate IUC" variant="outline" size="sm" full={false} onPress={validateIuc} disabled={validating} />
        )}
      </View>

      <Label>Choose a bouquet</Label>
      {loadingPlans ? (
        <Loading full={false} />
      ) : currentPlans.length === 0 ? (
        <Text style={{ color: c.ink3, fontFamily: font.regular, marginBottom: 12 }}>No bouquets available.</Text>
      ) : (
        <PlanList plans={currentPlans} value={plan} onPick={setPlan} />
      )}
      <View style={{ height: 14 }} />
      {amount > 0 ? <BalanceHint amount={amount} balance={balance} /> : null}

      <Btn label={amount > 0 ? `Continue · ${money(amount)}` : 'Continue'} disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm subscription"
        total={amount}
        balance={balance}
        rows={[['Provider', provider.name], ['Smartcard', iuc], ['Plan', planObj?.label || '—']]}
        onPay={() => { setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => purchase(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default BuyCable;
