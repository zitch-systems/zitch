import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router } from 'expo-router';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import { savingsService } from '@/lib/services/savings';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, Naira, NText } from '@/components/design/ui';
import { Label, QuickAmounts, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import { notify } from '@/components/design/Notify';
import { Hero } from '@/components/design/widgets';
import ZIcon from '@/components/design/ZIcon';
import Receipt from '@/components/design/Receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { Loading } from '@/components/design/Loading';

const AMOUNTS = [5000, 10000, 20000, 50000, 100000, 200000];
type Step = null | 'confirm' | 'pin';
type ConfirmedPlan = { principal: number; interest: number; maturity: number; rate: number; days: number };

const confirmedPlanFrom = (value: unknown): ConfirmedPlan | null => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const plan = value as Record<string, unknown>;
  const fields = ['principal', 'interest', 'maturity_value', 'rate', 'duration_days'];
  if (fields.some((key) => (typeof plan[key] !== 'number' && typeof plan[key] !== 'string')
    || String(plan[key]).trim() === '' || !Number.isFinite(Number(plan[key])))) return null;
  const result = { principal: Number(plan.principal), interest: Number(plan.interest), maturity: Number(plan.maturity_value), rate: Number(plan.rate), days: Number(plan.duration_days) };
  if (result.principal <= 0 || result.interest < 0 || result.rate < 0
    || !Number.isInteger(result.days) || result.days <= 0
    || Math.abs(result.maturity - result.principal - result.interest) > 0.005) return null;
  return result;
};

const annualRate = (rate: number): string => `${Number((rate * 100).toFixed(4))}% p.a`;

const Row2 = ({ k, v, strong }: { k: string; v: string; strong?: boolean }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 11, borderTopWidth: 1, borderTopColor: c.line }}>
      <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>{k}</Text>
      <Text style={{ fontSize: strong ? 16 : 14, fontFamily: strong ? font.extrabold : font.semibold, color: c.ink1, fontVariant: ['tabular-nums'] }}>{v}</Text>
    </View>
  );
};

const FixedSave = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const [amt, setAmt] = useState('');
  const [days, setDays] = useState(90);
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState('');
  const [recovered, setRecovered] = useState(false);
  const [txnRef, setTxnRef] = useState('');
  const [confirmedPlan, setConfirmedPlan] = useState<ConfirmedPlan | null>(null);
  const [pinError, setPinError] = useState('');
  const [rates, setRates] = useState<Record<number, number>>({});
  const [periods, setPeriods] = useState<number[]>([]);
  const [minAmt, setMinAmt] = useState(1000);
  const [availabilityLoading, setAvailabilityLoading] = useState(true);
  const [availabilityError, setAvailabilityError] = useState('');
  const [productAvailable, setProductAvailable] = useState(false);
  const [unavailableMessage, setUnavailableMessage] = useState('Fixed savings is not available right now.');
  const createInFlight = useRef(false);
  const availabilityInFlight = useRef(false);

  // Fail closed until the backend explicitly enables the product and supplies
  // rates. Showing bundled rates could invite a real wallet debit for a product
  // whose funds are not held by a live savings provider.
  const loadAvailability = useCallback(async () => {
    if (availabilityInFlight.current) return;
    availabilityInFlight.current = true;
    setAvailabilityLoading(true);
    setProductAvailable(false);
    try {
      const res = await savingsService.getRates();
      if (res?.success !== true || typeof res.product_available !== 'boolean') {
        throw new Error('Savings availability unknown');
      }
      if (res.product_available) {
        if (!Array.isArray(res.rates) || !res.rates.length
          || res.rates.some((x) => !Number.isInteger(Number(x.days)) || Number(x.days) <= 0
            || !Number.isFinite(Number(x.rate)) || Number(x.rate) <= 0)
          || res.min == null || !Number.isFinite(Number(res.min)) || Number(res.min) <= 0) {
          throw new Error('Savings terms unavailable');
        }
        const map: Record<number, number> = {};
        res.rates.forEach((x) => { map[Number(x.days)] = Number(x.rate); });
        const durations = Object.keys(map).map(Number).sort((a, b) => a - b);
        setRates(map);
        setPeriods(durations);
        setDays(durations[0]);
        setMinAmt(Number(res.min));
      }
      setProductAvailable(res.product_available);
      setUnavailableMessage(res.unavailable_message || 'Fixed savings is not available right now.');
      setAvailabilityError('');
    } catch {
      setAvailabilityError('We could not check fixed savings availability. Please try again.');
    } finally {
      availabilityInFlight.current = false;
      setAvailabilityLoading(false);
    }
  }, []);

  useEffect(() => { void loadAvailability(); }, [loadAvailability]);

  const amount = Number(amt || 0);
  const rate = rates[days] ?? 0;
  const interest = Math.round(amount * rate * (days / 365) * 100) / 100;
  const maturity = amount + interest;
  const valid = productAvailable && amount >= minAmt && amount <= balance && rate > 0;

  const create = async (pin: string) => {
    if (!valid || done || createInFlight.current) return;
    createInFlight.current = true;
    const fingerprint = [String(amount), String(days)].join('|');
    let deliveryStarted = false;
    setBusy(true);
    try {
      const requestKey = await acquireSpendAttempt('fixed-save', fingerprint);
      deliveryStarted = true;
      const res = await savingsService.create(amt, days, pin, requestKey);
      const outcome = classifySpendResponse(res);
      if (outcome === 'success') {
        await clearSpendAttempt('fixed-save', fingerprint, requestKey);
        setRecovered(isRecoveredSpendResponse(res));
        setConfirmedPlan(confirmedPlanFrom('plan' in res ? res.plan : undefined));
        setTxnRef(String(res.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (outcome === 'pending' || outcome === 'unknown') {
        setPending(true);
        setPendingMessage(outcome === 'pending'
          ? (res.message || 'Your savings request is processing. Its final status will update only after provider confirmation.')
          : 'We could not confirm this savings request. Check History before trying again.');
        setTxnRef(String(res.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') {
        setPinError(res.message || 'Incorrect PIN');  // keep key: no debit happened
      } else {
        await clearSpendAttempt('fixed-save', fingerprint, requestKey);
        notify('Error', res.message || 'Could not lock savings');
        setStep(null);
      }
    } catch {
      if (deliveryStarted) {
        setPending(true);
        setPendingMessage('We could not confirm this savings request. Check History before trying again.');
        setStep(null);
        setDone(true);
        reload();
      } else {
        notify('Unable to start savings', 'Could not safely prepare this request. Please try again.');
      }
    } finally {
      createInFlight.current = false;
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Savings request processing' : recovered ? 'Earlier attempt confirmed' : 'Savings locked 🔒'}
          message={pending
            ? pendingMessage
            : recovered
              ? 'This confirms your earlier savings request. No new funds were locked. Open My Fixed Saves to view the confirmed terms.'
              : confirmedPlan
                ? `${money(confirmedPlan.principal)} locked for ${confirmedPlan.days} days at ${annualRate(confirmedPlan.rate)}. You can't withdraw until maturity.`
                : 'Your savings request was confirmed. Open My Fixed Saves to view the confirmed amounts and terms.'}
          rows={pending
            ? [['Requested principal', money(amount)], ['Requested duration', `${days} days`]]
            : confirmedPlan
              ? [['Principal', money(confirmedPlan.principal)], ['Rate', annualRate(confirmedPlan.rate)], ['Duration', `${confirmedPlan.days} days`], ['Interest at maturity', money(confirmedPlan.interest)], ['Maturity value', money(confirmedPlan.maturity), true]]
              : [['Confirmed terms', 'View My Fixed Saves']]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          onDone={() => router.replace('/savings')}
        />
      </Screen>
    );
  }

  if (availabilityLoading) {
    return (
      <Screen scroll={false}>
        <Header title="Fixed Save" onBack={() => router.back()} />
        <Loading label="Checking availability…" />
      </Screen>
    );
  }

  if (availabilityError) {
    return (
      <Screen>
        <Header title="Fixed Save" onBack={() => router.back()} />
        <View style={{ paddingVertical: 36 }}>
          <Text accessibilityRole="alert" style={{ fontFamily: font.bold, color: c.ink1, fontSize: 18 }}>Unable to check savings availability</Text>
          <Text style={{ color: c.ink3, fontFamily: font.regular, lineHeight: 20, marginTop: 8, marginBottom: 20 }}>{availabilityError}</Text>
          <Btn label="Retry" onPress={() => { void loadAvailability(); }} />
          <View style={{ marginTop: 12 }}><Btn label="View existing saves" variant="outline" onPress={() => router.replace('/savings')} /></View>
        </View>
      </Screen>
    );
  }

  if (!productAvailable) {
    return (
      <Screen>
        <Header title="Fixed Save" onBack={() => router.back()} />
        <View style={{ alignItems: 'center', paddingHorizontal: 18, paddingTop: 46 }}>
          <View style={{ width: 72, height: 72, borderRadius: 24, backgroundColor: c.surface3, alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="fixed" size={30} color={c.ink3} />
          </View>
          <Text style={{ fontSize: 18, fontFamily: font.bold, color: c.ink1, marginTop: 18 }}>Fixed savings unavailable</Text>
          <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular, lineHeight: 20, textAlign: 'center', marginTop: 8 }}>{unavailableMessage}</Text>
          <View style={{ width: '100%', marginTop: 22 }}>
            <Btn label="View existing saves" variant="outline" onPress={() => router.replace('/savings')} />
          </View>
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <Header
        title="Fixed Save"
        sub="Choose an amount and lock period"
        onBack={() => router.back()}
        right={
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="My saves"
            onPress={() => router.push('/savings')}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 6, height: 42, paddingHorizontal: 14, borderRadius: 13, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line }}
          >
            <ZIcon name="fixed" size={16} color={c.brand} />
            <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.ink1 }}>My saves</Text>
          </Pressable>
        }
      />

      <Hero style={{ marginBottom: 18 }}>
        <Text style={{ fontSize: 13, color: 'rgba(255,255,255,.85)', fontFamily: font.regular }}>Estimated interest at maturity</Text>
        <NText style={{ fontSize: 32, fontFamily: font.extrabold, color: '#fff', marginTop: 4, fontVariant: ['tabular-nums'] }}>{money(interest)}</NText>
        <NText style={{ fontSize: 12.5, color: 'rgba(255,255,255,.85)', marginTop: 6, fontFamily: font.regular }}>
          on {amount > 0 ? money(amount) : '₦0'} in {days} days · {annualRate(rate)}
        </NText>
      </Hero>

      <Label>How much to lock?</Label>
      <QuickAmounts amounts={AMOUNTS} value={amt} onPick={setAmt} />
      <Field
        value={amt}
        onChangeText={(v) => setAmt(v.replace(/\D/g, ''))}
        keyboardType="number-pad"
        placeholder={`Enter amount (min ${money(minAmt)})`}
        prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />}
      />
      <View style={{ height: 6 }} />
      <BalanceHint amount={amount} balance={balance} />

      <Label>Lock period</Label>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -5, marginBottom: 18 }}>
        {periods.map((d) => {
          const on = days === d;
          return (
            <View key={d} style={{ width: '50%', padding: 5 }}>
              <Pressable
                accessibilityRole="radio"
                accessibilityLabel={`${d} days at ${annualRate(rates[d] ?? 0)}`}
                accessibilityState={{ selected: on }}
                onPress={() => setDays(d)}
                style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 14, paddingHorizontal: 16, borderRadius: 14, backgroundColor: on ? c.brand : c.surface, borderWidth: 1.5, borderColor: on ? c.brand : c.line }}
              >
                <Text style={{ fontFamily: font.bold, color: on ? '#fff' : c.ink1 }}>{d} days</Text>
                <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: on ? 'rgba(255,255,255,.85)' : c.brand }}>{annualRate(rates[d] ?? 0)}</Text>
              </Pressable>
            </View>
          );
        })}
      </View>

      <View style={{ borderRadius: 16, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, paddingHorizontal: 16, paddingBottom: 4, marginBottom: 18 }}>
        <Row2 k="Estimated interest" v={money(interest)} />
        <Row2 k="Matures in" v={`${days} days`} />
        <Row2 k="Estimated maturity value" v={money(maturity)} strong />
      </View>

      <Btn label="Continue" disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm Fixed Save"
        total={amount}
        balance={balance}
        rows={[['Principal', money(amount)], ['Duration', `${days} days`], ['Rate', annualRate(rate)], ['Estimated maturity value', money(maturity)]]}
        onPay={() => { setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Locking…' : `Lock ${money(amount)} for ${days} days`}
        </Text>
        <PinPad onComplete={(p) => create(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default FixedSave;
