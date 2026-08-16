import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { Loading } from '@/components/design/Loading';
import { router } from 'expo-router';
import { apiPost, newIdempotencyKey, publicJson } from '@/lib/api';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, HeaderLink } from '@/components/design/ui';
import { Label, ProviderGrid, PlanList, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const PROVIDERS = [
  { id: '1', name: 'GoTV', color: '#92C020', logo: require('@/assets/images/providers/gotv.png') },
  { id: '2', name: 'DSTV', color: '#0A66C2', logo: require('@/assets/images/providers/dstv.png') },
  { id: '3', name: 'StarTimes', color: '#F47B20', logo: require('@/assets/images/providers/startimes.png') },
];

type Step = null | 'confirm' | 'pin';

const BuyCable = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
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
  // The ledger reference the server minted for this transaction — shown on the
  // receipt and carried into the saved/shared file, so a support ticket can name it.
  const [txnRef, setTxnRef] = useState('');
  const [pinError, setPinError] = useState('');

  // Fetch bouquets for the chosen provider.
  useEffect(() => {
    if (!prov) return;
    let active = true;
    const timer = setTimeout(() => {
      if (!active) return;
      setLoadingPlans(true);
      setPlan('');
      setPlans([]);
      setValidatedName('');
      publicJson('/api/utility/get_cable_plans/', { cablenetwork: prov })
      .then((res) => {
        if (active && res?.cable_plans) {
          setPlans(res.cable_plans.map((p: any) => ({
            id: String(p.cable_plan_code),
            label: p.name,
            sub: p.validity,
            price: Number(p.price ?? 0),
          })));
        }
      })
      .catch(() => {})
      .finally(() => { if (active) setLoadingPlans(false); });
    }, 0);
    return () => { active = false; clearTimeout(timer); };
  }, [prov]);

  // Authoritative price for the chosen bouquet.
  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      if (!active) return;
      if (!plan) { setPrice(''); return; }
      publicJson('/api/utility/get_cable_plans_price/', { cable_plan_code: plan })
      .then((res) => { if (active && res?.cable_plans_price != null) setPrice(String(res.cable_plans_price)); })
      .catch(() => {});
    }, 0);
    return () => { active = false; clearTimeout(timer); };
  }, [plan]);

  const provider = PROVIDERS.find((p) => p.id === prov)!;
  const planObj = plans.find((p) => p.id === plan);
  const amount = Number(price || planObj?.price || 0);
  const valid = iuc.length >= 8 && !!plan && amount > 0;

  const validateIuc = async () => {
    if (iuc.trim().length < 8) { notify('Error', 'Enter a valid IUC / smartcard number.'); return; }
    setValidating(true);
    try {
      const response = await apiPost('/api/utility/validate_iuc/', { iuc, cablenetwork: prov });
      const result = await response.json();
      if (response.ok) {
        setValidatedName(result.customer_name || result.name || 'Verified');
      } else {
        notify('Error', result.message || 'Could not verify this IUC number.');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setValidating(false);
    }
  };

  const [pending, setPending] = useState(false);  // provider-pending: held, confirmed later
  const idemKey = useRef('');  // stable across retries of one purchase attempt

  // Any edit to the purchase details is a new spend — drop the retained key so a
  // stale one can't replay the PRIOR purchase for the edited one (mirrors sendmoney).
  useEffect(() => { idemKey.current = ''; }, [prov, iuc, plan]);

  const purchase = async (enteredPin: string) => {
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      const response = await apiPost('/api/utility/buycable/', {
        iuc,
        cablenetwork: prov,
        selectedcablePlan: plan,
        transaction_pin: enteredPin,
        idempotency_key: idemKey.current,
      });
      const result = await response.json();
      if (response.ok) {
        idemKey.current = '';
        // `pending` = provider timeout: held while reconciliation confirms or
        // refunds — the receipt must say "processing", not claim delivery.
        setTxnRef(String(result.reference || ''));
        setPending(!!result.pending);
        setStep(null);
        setDone(true);
        reload();
      } else if (result.code === 'pin_incorrect' || result.code === 'pin_locked') {
        setPinError(result.message || 'Incorrect PIN');  // keep key: no debit happened
      } else {
        idemKey.current = '';  // definitive server failure — retry is a fresh attempt
        notify('Error', result.message || 'Transaction failed');
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
          title={pending ? 'Processing' : 'Subscription active'}
          message={pending
            ? `Your ${provider.name} ${planObj?.label || ''} subscription on ${iuc} is processing and will be confirmed shortly. If it can't be completed, you'll be refunded automatically.`
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
      <Header title="Cable TV" onBack={() => router.back()} right={<HeaderLink label="History" onPress={() => router.push('/history')} />} />

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
        <Loading full={false} />
      ) : plans.length === 0 ? (
        <Text style={{ color: c.ink3, fontFamily: font.regular, marginBottom: 12 }}>No bouquets available.</Text>
      ) : (
        <PlanList plans={plans} value={plan} onPick={setPlan} />
      )}
      <View style={{ height: 14 }} />
      {/* Balance / insufficient-funds signal, consistent with the other bill
          screens (buydata, buyelectricity, betting, exams). */}
      <BalanceHint amount={amount} balance={balance} />

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

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN" protectScreen>
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => purchase(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default BuyCable;
