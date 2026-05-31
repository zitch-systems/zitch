import React, { useEffect, useState } from 'react';
import { View, Text, Pressable, Alert } from 'react-native';
import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import { Screen, Header, Field, Btn, Sheet, PinPad, money } from '@/components/design/ui';
import { Label, QuickAmounts, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import { Hero } from '@/components/design/widgets';
import Receipt from '@/components/design/Receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const AMOUNTS = [5000, 10000, 20000, 50000, 100000, 200000];
const PERIODS = [30, 90, 180, 365];
// Mirrors backend FixedSave.RATES; the quote endpoint is the source of truth.
const RATES: Record<number, number> = { 30: 0.12, 90: 0.15, 180: 0.18, 365: 0.22 };
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

const FixedSave = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const [token, setToken] = useState('');
  const [amt, setAmt] = useState('');
  const [days, setDays] = useState(90);
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => { getToken().then((t) => t && setToken(t)); }, []);

  const amount = Number(amt || 0);
  const rate = RATES[days];
  const interest = Math.round(amount * rate * (days / 365));
  const maturity = amount + interest;
  const valid = amount >= 1000 && amount <= balance;

  const create = async (pin: string) => {
    setBusy(true);
    try {
      const res = await fetch(`${baseUrl}/api/savings/create/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token, amount: amt, days, transaction_pin: pin }),
      }).then((r) => r.json());
      if (res.success) {
        setStep(null);
        setDone(true);
        reload();
      } else {
        Alert.alert('Error', res.message || 'Could not lock savings');
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
          title="Savings locked 🔒"
          message={`${money(amount)} locked for ${days} days at ${(rate * 100).toFixed(0)}% p.a. You can't withdraw until maturity.`}
          rows={[['Principal', money(amount)], ['Rate', `${(rate * 100).toFixed(0)}% p.a`], ['Duration', `${days} days`], ['Interest earned', money(interest)], ['Maturity value', money(maturity), true]]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Fixed Save" sub="Lock funds, earn up to 22% p.a" onBack={() => router.back()} />

      <Hero style={{ marginBottom: 18 }}>
        <Text style={{ fontSize: 13, color: 'rgba(255,255,255,.85)', fontFamily: font.regular }}>You could earn</Text>
        <Text style={{ fontSize: 32, fontFamily: font.extrabold, color: '#fff', marginTop: 4, fontVariant: ['tabular-nums'] }}>{money(interest)}</Text>
        <Text style={{ fontSize: 12.5, color: 'rgba(255,255,255,.85)', marginTop: 6, fontFamily: font.regular }}>
          on {amount > 0 ? money(amount) : '₦0'} in {days} days · {(rate * 100).toFixed(0)}% p.a
        </Text>
      </Hero>

      <Label>How much to lock?</Label>
      <QuickAmounts amounts={AMOUNTS} value={amt} onPick={setAmt} />
      <Field
        value={amt}
        onChangeText={(v) => setAmt(v.replace(/\D/g, ''))}
        keyboardType="number-pad"
        placeholder="Enter amount (min ₦1,000)"
        prefix={<Text style={{ fontFamily: font.extrabold, color: c.ink2, fontSize: 16 }}>₦</Text>}
      />
      <View style={{ height: 6 }} />
      <BalanceHint amount={amount} balance={balance} />

      <Label>Lock period</Label>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -5, marginBottom: 18 }}>
        {PERIODS.map((d) => {
          const on = days === d;
          return (
            <View key={d} style={{ width: '50%', padding: 5 }}>
              <Pressable
                onPress={() => setDays(d)}
                style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 14, paddingHorizontal: 16, borderRadius: 14, backgroundColor: on ? c.brand : c.surface, borderWidth: 1.5, borderColor: on ? c.brand : c.line }}
              >
                <Text style={{ fontFamily: font.bold, color: on ? '#fff' : c.ink1 }}>{d} days</Text>
                <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: on ? 'rgba(255,255,255,.85)' : c.brand }}>{(RATES[d] * 100).toFixed(0)}%</Text>
              </Pressable>
            </View>
          );
        })}
      </View>

      <View style={{ borderRadius: 16, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, paddingHorizontal: 16, paddingBottom: 4, marginBottom: 18 }}>
        <Row2 k="Interest" v={money(interest)} />
        <Row2 k="Matures in" v={`${days} days`} />
        <Row2 k="You get back" v={money(maturity)} strong />
      </View>

      <Btn label="Continue" disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm Fixed Save"
        total={amount}
        balance={balance}
        rows={[['Principal', money(amount)], ['Duration', `${days} days`], ['Rate', `${(rate * 100).toFixed(0)}% p.a`], ['Maturity value', money(maturity)]]}
        onPay={() => { setStep(null); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Locking…' : `Lock ${money(amount)} for ${days} days`}
        </Text>
        <PinPad onComplete={(p) => create(p)} />
      </Sheet>
    </Screen>
  );
};

export default FixedSave;
