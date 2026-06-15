import React, { useEffect, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { apiPost, apiJson } from '@/lib/api';
import { Screen, Header, Btn, Sheet, PinPad, money, Naira } from '@/components/design/ui';
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
  const [pinError, setPinError] = useState('');

  useEffect(() => {
    getToken().then((t) => {
      if (!t) return;
      setToken(t);
      apiPost('/api/loans/status/')
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
    setBusy(true);
    try {
      const res = await apiJson('/api/loans/request/', { amount: String(amount), tenure_days: tenure, transaction_pin: pin });
      if (res.success) {
        setStep(null);
        setDone(true);
        reload();
      } else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') {
        setPinError(res.message || 'Incorrect PIN');
      } else {
        notify('Error', res.message || 'Loan request failed');
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
          title="Loan disbursed"
          message={`${money(amount)} has been added to your wallet. Repay by the due date to boost your limit.`}
          rows={[['Loan amount', money(amount)], ['Interest', money(interest)], ['Tenure', `${tenure} days`], ['Repayment', money(repay), true]]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Get Loan" sub="Instant, no paperwork" onBack={() => router.back()} />

      <Hero style={{ marginBottom: 18 }}>
        <Text style={{ fontSize: 13, color: 'rgba(255,255,255,.85)', fontFamily: font.regular }}>You're eligible for up to</Text>
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

      <Btn label={`Get ${money(amount)}`} disabled={overLimit} onPress={() => setStep('confirm')} />
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
