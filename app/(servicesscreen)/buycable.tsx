import React, { useEffect, useState } from 'react';
import { View, Text, Alert, ActivityIndicator } from 'react-native';
import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import { Screen, Header, Field, Btn, Sheet, PinPad, money } from '@/components/design/ui';
import { Label, ProviderGrid, PlanList, ConfirmSheet } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const PROVIDERS = [
  { id: '1', name: 'GoTV', color: '#92C020' },
  { id: '2', name: 'DSTV', color: '#0A66C2' },
  { id: '3', name: 'StarTimes', color: '#F47B20' },
];

type Step = null | 'confirm' | 'pin';

const BuyCable = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const [token, setToken] = useState('');
  const [prov, setProv] = useState('1');
  const [iuc, setIuc] = useState('');
  const [plan, setPlan] = useState('');
  const [price, setPrice] = useState('');
  const [plans, setPlans] = useState<{ id: string; label: string; sub?: string; price: number }[]>([]);
  const [loadingPlans, setLoadingPlans] = useState(false);
  const [validatedName, setValidatedName] = useState('');
  const [validating, setValidating] = useState(false);
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => { getToken().then((t) => t && setToken(t)); }, []);

  // Fetch bouquets for the chosen provider.
  useEffect(() => {
    if (!prov) return;
    setLoadingPlans(true);
    setPlan('');
    setPlans([]);
    setValidatedName('');
    fetch(`${baseUrl}/api/utility/get_cable_plans/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cablenetwork: prov }),
    })
      .then((r) => r.json())
      .then((res) => {
        if (res?.cable_plans) {
          setPlans(res.cable_plans.map((p: any) => ({
            id: String(p.cable_plan_code),
            label: p.name,
            sub: p.validity,
            price: Number(p.price ?? 0),
          })));
        }
      })
      .catch(() => {})
      .finally(() => setLoadingPlans(false));
  }, [prov]);

  // Authoritative price for the chosen bouquet.
  useEffect(() => {
    if (!plan) { setPrice(''); return; }
    fetch(`${baseUrl}/api/utility/get_cable_plans_price/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cable_plan_code: plan }),
    })
      .then((r) => r.json())
      .then((res) => { if (res?.cable_plans_price != null) setPrice(String(res.cable_plans_price)); })
      .catch(() => {});
  }, [plan]);

  const provider = PROVIDERS.find((p) => p.id === prov)!;
  const planObj = plans.find((p) => p.id === plan);
  const amount = Number(price || planObj?.price || 0);
  const valid = iuc.length >= 8 && !!plan;

  const validateIuc = async () => {
    if (iuc.trim().length < 8) { Alert.alert('Error', 'Enter a valid IUC / smartcard number.'); return; }
    setValidating(true);
    try {
      const response = await fetch(`${baseUrl}/api/utility/validate_iuc/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token, iuc, cablenetwork: prov }),
      });
      const result = await response.json();
      if (response.ok) {
        setValidatedName(result.customer_name || result.name || 'Verified');
      } else {
        Alert.alert('Error', result.message || 'Could not verify this IUC number.');
      }
    } catch {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setValidating(false);
    }
  };

  const purchase = async (enteredPin: string) => {
    setBusy(true);
    try {
      const response = await fetch(`${baseUrl}/api/utility/buycable/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          iuc,
          cablenetwork: prov,
          selectedcablePlan: plan,
          access_token: token,
          transaction_pin: enteredPin,
        }),
      });
      const result = await response.json();
      if (response.ok) {
        setStep(null);
        setDone(true);
        reload();
      } else {
        Alert.alert('Error', result.message || 'Transaction failed');
        setStep(null);
      }
    } catch {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
      setStep(null);
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen scroll={false}>
        <Receipt
          title="Subscription active"
          message={`${provider.name} ${planObj?.label || ''} on ${iuc} is now active.`}
          rows={[['Provider', provider.name], ['Smartcard / IUC', iuc], ['Plan', planObj?.label || '—'], ['Total', money(amount), true]]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Cable TV" onBack={() => router.back()} />

      <Label>Select provider</Label>
      <ProviderGrid items={PROVIDERS} value={prov} onPick={setProv} cols={3} />

      <Field
        label="Smartcard / IUC number"
        value={iuc}
        onChangeText={(v) => { setIuc(v.replace(/\D/g, '').slice(0, 12)); setValidatedName(''); }}
        keyboardType="number-pad"
        placeholder="1234 5678 90"
      />
      <View style={{ marginTop: 8, marginBottom: 8 }}>
        {validatedName ? (
          <Text style={{ color: c.brandDeep, fontFamily: font.semibold, fontSize: 12.5 }}>✓ {validatedName}</Text>
        ) : (
          <Btn label="Validate IUC" variant="outline" size="sm" full={false} onPress={validateIuc} disabled={validating} />
        )}
      </View>

      <Label>Choose a bouquet</Label>
      {loadingPlans ? (
        <ActivityIndicator color={c.brand} style={{ marginVertical: 20 }} />
      ) : plans.length === 0 ? (
        <Text style={{ color: c.ink3, fontFamily: font.regular, marginBottom: 12 }}>No bouquets available.</Text>
      ) : (
        <PlanList plans={plans} value={plan} onPick={setPlan} />
      )}
      <View style={{ height: 18 }} />

      <Btn label={amount > 0 ? `Continue · ${money(amount)}` : 'Continue'} disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm subscription"
        total={amount}
        balance={balance}
        rows={[['Provider', provider.name], ['Smartcard', iuc], ['Plan', planObj?.label || '—']]}
        onPay={() => { setStep(null); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => purchase(p)} />
      </Sheet>
    </Screen>
  );
};

export default BuyCable;
