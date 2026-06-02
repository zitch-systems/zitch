import React, { useEffect, useState } from 'react';
import { View, Text, Pressable, Alert } from 'react-native';
import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import { apiJson } from '@/lib/api';
import { Screen, Header, Field, Btn, Sheet, PinPad, money } from '@/components/design/ui';
import { Label, Monogram, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const EXAM_COLORS: Record<string, string> = {
  waec: '#0B7A3B', neco: '#1E5BB8', jamb: '#7A1FA2', nabteb: '#C0392B',
};

type Exam = { code: string; name: string; description: string; price: string };
type Step = null | 'confirm' | 'pin';

const Exams = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const [token, setToken] = useState('');
  const [exams, setExams] = useState<Exam[]>([]);
  const [selected, setSelected] = useState('');
  const [qty, setQty] = useState(1);
  const [phone, setPhone] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => { getToken().then((t) => t && setToken(t)); }, []);
  useEffect(() => {
    fetch(`${baseUrl}/api/exams/list/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then((r) => r.json())
      .then((res) => { if (res.exams) { setExams(res.exams); if (res.exams[0]) setSelected(res.exams[0].code); } })
      .catch(() => {});
  }, []);

  const exam = exams.find((e) => e.code === selected);
  const amount = exam ? Number(exam.price) * qty : 0;
  const valid = !!exam && phone.length >= 10;

  const purchase = async (pin: string) => {
    setBusy(true);
    try {
      const res = await apiJson('/api/exams/buy/', { exam: selected, quantity: qty, phone, transaction_pin: pin });
      if (res.success) {
        setStep(null);
        setDone(true);
        reload();
      } else {
        Alert.alert('Error', res.message || 'Transaction failed');
        setStep(null);
      }
    } catch {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
      setStep(null);
    } finally {
      setBusy(false);
    }
  };

  if (done && exam) {
    return (
      <Screen scroll={false}>
        <Receipt
          title="PIN purchased"
          message={`Your ${exam.name} ${exam.description} (${qty}) was sent to ${phone}.`}
          rows={[['Exam', exam.name], ['Item', exam.description], ['Quantity', String(qty)], ['Phone', phone], ['Total', money(amount), true]]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Exams · JAMB / WAEC" onBack={() => router.back()} />

      <Label>Select exam</Label>
      <View style={{ gap: 10, marginBottom: 16 }}>
        {exams.map((e) => {
          const on = selected === e.code;
          return (
            <Pressable
              key={e.code}
              onPress={() => setSelected(e.code)}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 13, paddingHorizontal: 14, borderRadius: 15, backgroundColor: c.surface, borderWidth: 2, borderColor: on ? c.brand : c.line }}
            >
              <Monogram text={e.name.slice(0, 2)} color={EXAM_COLORS[e.code] || c.brand} />
              <View style={{ flex: 1 }}>
                <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>{e.name}</Text>
                <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>{e.description}</Text>
              </View>
              <Text style={{ fontFamily: font.bold, color: on ? c.brand : c.ink1, fontVariant: ['tabular-nums'] }}>₦{Number(e.price).toLocaleString()}</Text>
            </Pressable>
          );
        })}
      </View>

      <Label>Quantity</Label>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 16, marginBottom: 16 }}>
        <Pressable onPress={() => setQty((q) => Math.max(1, q - 1))} style={{ width: 46, height: 46, borderRadius: 13, borderWidth: 1.5, borderColor: c.line, backgroundColor: c.surface, alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ fontSize: 22, fontFamily: font.bold, color: c.ink1 }}>−</Text>
        </Pressable>
        <Text style={{ fontSize: 20, fontFamily: font.extrabold, color: c.ink1, minWidth: 28, textAlign: 'center' }}>{qty}</Text>
        <Pressable onPress={() => setQty((q) => Math.min(10, q + 1))} style={{ width: 46, height: 46, borderRadius: 13, borderWidth: 1.5, borderColor: c.line, backgroundColor: c.surface, alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ fontSize: 22, fontFamily: font.bold, color: c.ink1 }}>+</Text>
        </Pressable>
      </View>

      <Field
        label="Phone number (PIN delivery)"
        value={phone}
        onChangeText={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))}
        keyboardType="number-pad"
        placeholder="0801 234 5678"
      />
      <View style={{ height: 6 }} />
      <BalanceHint amount={amount} balance={balance} />

      <Btn label={amount > 0 ? `Continue · ${money(amount)}` : 'Continue'} disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm purchase"
        total={amount}
        balance={balance}
        rows={exam ? [['Exam', exam.name], ['Item', exam.description], ['Quantity', String(qty)], ['Phone', phone]] : []}
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

export default Exams;
