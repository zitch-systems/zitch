import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router } from 'expo-router';
import { apiJson, newIdempotencyKey, publicJson } from '@/lib/api';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, Naira, HeaderLink } from '@/components/design/ui';
import { Label, Monogram, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import { notify } from '@/components/design/Notify';
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
  const [exams, setExams] = useState<Exam[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [selected, setSelected] = useState('');
  const [qty, setQty] = useState(1);
  const [phone, setPhone] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  // The ledger reference the server minted for this transaction — shown on the
  // receipt and carried into the saved/shared file, so a support ticket can name it.
  const [txnRef, setTxnRef] = useState('');
  const [pending, setPending] = useState(false);  // provider-pending: held, confirmed later
  const [pinError, setPinError] = useState('');
  const idemKey = useRef('');  // stable across retries of one purchase attempt

  // Any edit to the purchase details is a new spend — drop the retained key so a
  // stale one can't replay the PRIOR purchase for the edited one (mirrors sendmoney).
  useEffect(() => { idemKey.current = ''; }, [selected, qty, phone]);

  useEffect(() => {
    let active = true;
    publicJson('/api/exams/list/')
      .then((res) => { if (active && Array.isArray(res.exams)) { setExams(res.exams); if (res.exams[0]) setSelected(res.exams[0].code); } })
      .catch(() => {})
      .finally(() => { if (active) setLoadingList(false); });
    return () => { active = false; };
  }, []);

  const exam = exams.find((e) => e.code === selected);
  const amount = exam ? Number(exam.price) * qty : 0;
  const valid = !!exam && phone.length >= 10;

  const purchase = async (pin: string) => {
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      const res = await apiJson('/api/exams/buy/', { exam: selected, quantity: qty, phone, transaction_pin: pin, idempotency_key: idemKey.current });
      // `pending` = provider timeout: the money is DEBITED AND HELD while
      // reconciliation confirms or refunds it. It carries no `success` field, so
      // treating it as a failure (as before) told the user "Error", cleared the
      // key, and invited a retry that debited a SECOND time. `duplicate` = the
      // server replayed a completed attempt — also not a failure.
      if (res.success || res.pending || res.duplicate) {
        idemKey.current = '';
        setTxnRef(String(res.reference || ''));
        setPending(!res.success && !!res.pending && !res.duplicate);
        setStep(null);
        setDone(true);
        reload();
      } else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') {
        setPinError(res.message || 'Incorrect PIN');
      } else {
        // Only a definitive rejection mints a new key; a connectivity failure
        // (`offline`) keeps it so a retry replays server-side, never debits twice.
        if (!res.offline) idemKey.current = '';
        notify('Error', res.message || 'Transaction failed');
        setStep(null);
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
      setStep(null);
    } finally {
      setBusy(false);
    }
  };

  if (done && exam) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Processing' : 'PIN purchased'}
          message={pending
            ? `Your ${exam.name} ${exam.description} (${qty}) order is processing and will be confirmed shortly. If it can't be completed, you'll be refunded automatically.`
            : `Your ${exam.name} ${exam.description} (${qty}) was sent to ${phone}.`}
          rows={[['Exam', exam.name], ['Item', exam.description], ['Quantity', String(qty)], ['Phone', phone], ['Total', money(amount), true]]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Exams · JAMB / WAEC" onBack={() => router.back()} right={<HeaderLink label="History" onPress={() => router.push('/history')} />} />

      <Label>Select exam</Label>
      {loadingList ? (
        <View style={{ marginBottom: 16 }}><Loading full={false} /></View>
      ) : exams.length === 0 ? (
        <Text style={{ color: c.ink3, fontFamily: font.regular, marginBottom: 16 }}>No exams available right now. Please try again later.</Text>
      ) : (
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
              <Text style={{ fontFamily: font.bold, color: on ? c.brand : c.ink1, fontVariant: ['tabular-nums'] }}><Naira />{Number(e.price).toLocaleString()}</Text>
            </Pressable>
          );
        })}
      </View>
      )}

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
        onPay={() => { setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => purchase(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default Exams;

