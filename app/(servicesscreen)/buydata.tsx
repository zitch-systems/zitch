import React, { useEffect, useState } from 'react';
import { View, Text } from 'react-native';
import { Loading } from '@/components/design/Loading';
import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { apiPost } from '@/lib/api';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import { EP } from '@/lib/endpoints';
import { Screen, Header, Field, Btn, Sheet, PinPad, money } from '@/components/design/ui';
import { Label, ProviderGrid, Segmented, PlanList, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import { notify } from '@/components/design/Notify';
import Receipt from '@/components/design/Receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const NETWORKS = [
  { id: '1', name: 'MTN', color: '#FFCC00', logo: require('@/assets/images/providers/mtn.png') },
  { id: '2', name: 'GLO', color: '#2BB24C', logo: require('@/assets/images/providers/glo.png') },
  { id: '3', name: 'Airtel', color: '#E40000', logo: require('@/assets/images/providers/airtel.png') },
  { id: '4', name: '9mobile', color: '#0A8A3D', logo: require('@/assets/images/providers/9mobile.png') },
];
const PLAN_TYPES = [
  { v: '1', label: 'SME' },
  { v: '2', label: 'SME2' },
  { v: '3', label: 'Gifting' },
  { v: '4', label: 'Corporate' },
];

type Step = null | 'confirm' | 'pin';

const BuyData = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const [net, setNet] = useState('1');
  const [planType, setPlanType] = useState('1');
  const [plan, setPlan] = useState('');
  const [phone, setPhone] = useState('');
  const [price, setPrice] = useState('');
  const [priceFor, setPriceFor] = useState('');
  const [plans, setPlans] = useState<{ id: string; label: string; sub?: string; price: number }[]>([]);
  const [plansFor, setPlansFor] = useState('');
  const [loadingPlans, setLoadingPlans] = useState(false);
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState('');
  const [recovered, setRecovered] = useState(false);
  const [txnRef, setTxnRef] = useState('');
  const [pinError, setPinError] = useState('');

  // Fetch plans whenever network + plan type are chosen.
  useEffect(() => {
    if (!net || !planType) return;
    const requestedFor = `${net}|${planType}`;
    let current = true;
    setLoadingPlans(true);
    setPlan('');
    setPlans([]);
    setPlansFor('');
    fetch(`${baseUrl}/api/utility/get_data_plans/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ datanetwork: net, selectedPlanType: planType }),
    })
      .then((r) => r.json())
      .then((res) => {
        if (current && res?.data_plans) {
          setPlans(res.data_plans.map((p: any) => ({
            id: String(p.plan_code),
            label: p.name,
            sub: p.validity,
            price: Number(p.price ?? 0),
          })));
          setPlansFor(requestedFor);
        }
      })
      .catch(() => {})
      .finally(() => { if (current) setLoadingPlans(false); });
    return () => { current = false; };
  }, [net, planType]);

  // Fetch authoritative price for the chosen plan.
  useEffect(() => {
    setPrice('');
    setPriceFor('');
    if (!plan) return;
    const requestedFor = `${net}|${planType}|${plan}`;
    let current = true;
    fetch(`${baseUrl}/api/utility/get_data_plans_price/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selectedDataPlan: plan }),
    })
      .then((r) => r.json())
      .then((res) => {
        if (current && res?.price != null) {
          setPrice(String(res.price));
          setPriceFor(requestedFor);
        }
      })
      .catch(() => {});
    return () => { current = false; };
  }, [net, planType, plan]);

  const network = NETWORKS.find((n) => n.id === net)!;
  const currentPlanContext = `${net}|${planType}`;
  const currentPlans = plansFor === currentPlanContext ? plans : [];
  const planObj = currentPlans.find((p) => p.id === plan);
  const currentPrice = priceFor === `${currentPlanContext}|${plan}` ? price : '';
  const amount = Number(currentPrice || 0);
  const hasAuthoritativePrice = currentPrice !== '' && Number.isFinite(amount) && amount > 0;
  const valid = phone.length >= 10 && !!planObj && hasAuthoritativePrice && amount <= balance;
  const purchase = async (enteredPin: string) => {
    const fingerprint = [net, plan, phone.trim()].join('|');
    let deliveryStarted = false;
    setBusy(true);
    try {
      const requestKey = await acquireSpendAttempt('data', fingerprint);
      deliveryStarted = true;
      const response = await apiPost(EP.utility.buyData, {
        phone,
        datanetwork: net,
        selectedDataPlan: plan,
        transaction_pin: enteredPin,
        idempotency_key: requestKey,
      });
      const result = await response.json();
      const outcome = classifySpendResponse(result, response.status);
      if (outcome === 'success') {
        await clearSpendAttempt('data', fingerprint, requestKey);
        setRecovered(isRecoveredSpendResponse(result, response.status));
        setTxnRef(String(result.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (outcome === 'pending' || outcome === 'unknown') {
        setPending(true);
        setPendingMessage(outcome === 'pending'
          ? (result.message || 'Your data purchase is processing. Its final status will update only after provider confirmation.')
          : 'We could not confirm this data purchase. Check History before trying again.');
        setTxnRef(String(result.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (result.code === 'pin_incorrect' || result.code === 'pin_locked') {
        setPinError(result.message || 'Incorrect PIN');  // keep key: no debit happened
      } else {
        await clearSpendAttempt('data', fingerprint, requestKey);
        notify('Error', result.message || 'Transaction failed');
        setStep(null);
      }
    } catch {
      if (deliveryStarted) {
        setPending(true);
        setPendingMessage('We could not confirm this data purchase. Check History before trying again.');
        setStep(null);
        setDone(true);
        reload();
      } else {
        notify('Unable to start purchase', 'Could not safely prepare this request. Please try again.');
      }
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Purchase processing' : recovered ? 'Earlier attempt confirmed' : 'Successful'}
          message={pending
            ? pendingMessage
            : recovered
              ? 'This confirms your earlier data purchase. No new bundle was bought. Start a new purchase to buy again.'
            : `Your ${planObj?.label || 'data'} purchase to ${phone} was successful.`}
          rows={[['Type', 'Data bundle'], ['Network', network.name], ['Phone', phone], ['Plan', planObj?.label || '—'], ['Total', money(amount), true]]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Data" onBack={() => router.back()} />

      <Label>Select network</Label>
      <ProviderGrid items={NETWORKS} value={net} onPick={setNet} />

      <Label>Plan type</Label>
      <Segmented options={PLAN_TYPES} value={planType} onChange={setPlanType} />

      <Label>Select a data plan</Label>
      {loadingPlans ? (
        <Loading full={false} />
      ) : currentPlans.length === 0 ? (
        <Text style={{ color: c.ink3, fontFamily: font.regular, marginBottom: 12 }}>No plans available for this selection.</Text>
      ) : (
        <PlanList plans={currentPlans} value={plan} onPick={setPlan} />
      )}
      <View style={{ height: 16 }} />

      <Field
        label="Phone number"
        value={phone}
        onChangeText={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))}
        keyboardType="number-pad"
        placeholder="0801 234 5678"
      />
      <View style={{ height: 10 }} />
      <BalanceHint amount={amount} balance={balance} />

      <Btn label={amount > 0 ? `Continue · ${money(amount)}` : 'Continue'} disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm data"
        total={amount}
        balance={balance}
        rows={[['Network', network.name], ['Phone', phone], ['Plan', planObj?.label || '—']]}
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

export default BuyData;
