import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { apiPost, newIdempotencyKey } from '@/lib/api';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, Naira } from '@/components/design/ui';
import { Label, ProviderGrid, QuickAmounts, QUICK_AMOUNTS, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const NETWORKS = [
  { id: '1', name: 'MTN', color: '#FFCC00', logo: require('@/assets/images/providers/mtn.png') },
  { id: '2', name: 'GLO', color: '#2BB24C', logo: require('@/assets/images/providers/glo.png') },
  { id: '3', name: 'Airtel', color: '#E40000', logo: require('@/assets/images/providers/airtel.png') },
  { id: '4', name: '9mobile', color: '#0A8A3D', logo: require('@/assets/images/providers/9mobile.png') },
];

type Step = null | 'confirm' | 'pin';

const BuyAirtime = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const params = useLocalSearchParams<{ phone?: string }>();
  const [token, setToken] = useState('');
  const [net, setNet] = useState('1');
  const [phone, setPhone] = useState(params.phone ?? '');
  const [amt, setAmt] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pinError, setPinError] = useState('');
  const idemKey = useRef('');  // stable across retries of one purchase attempt

  useEffect(() => { getToken().then((t) => t && setToken(t)); }, []);

  const network = NETWORKS.find((n) => n.id === net)!;
  const amount = Number(amt || 0);
  const valid = phone.length >= 10 && amount >= 100 && amount <= balance;

  const purchase = async (enteredPin: string) => {
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      const response = await apiPost('/api/utility/buyairtime/', {
        network: net,
        phone,
        amount: amt,
        transaction_pin: enteredPin,
        idempotency_key: idemKey.current,
      });
      const result = await response.json();
      if (response.ok) {
        idemKey.current = '';
        setStep(null);
        setDone(true);
        reload();
      } else if (result.code === 'pin_incorrect' || result.code === 'pin_locked') {
        setPinError(result.message || 'Incorrect PIN');  // keep key: no debit happened
      } else {
        idemKey.current = '';  // definitive server failure — a retry is a fresh attempt
        notify('Error', result.message || 'Transaction failed');
        setStep(null);
      }
    } catch {
      // network/unknown outcome — keep the key so a retry replays, never double-debits
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
          title="Successful"
          message={`Your airtime purchase to ${phone} was successful.`}
          rows={[['Type', 'Airtime top-up'], ['Network', network.name], ['Phone', phone], ['Amount', money(amount)], ['Fee', '₦0'], ['Total', money(amount), true]]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Airtime" onBack={() => router.back()} />

      <Label>Select network</Label>
      <ProviderGrid items={NETWORKS} value={net} onPick={setNet} />

      <Field
        label="Phone number"
        value={phone}
        onChangeText={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))}
        keyboardType="number-pad"
        placeholder="0801 234 5678"
      />
      <View style={{ height: 16 }} />

      <Label>Choose amount</Label>
      <QuickAmounts amounts={QUICK_AMOUNTS} value={amt} onPick={setAmt} />
      <Field
        label="Or enter amount"
        value={amt}
        onChangeText={(v) => setAmt(v.replace(/\D/g, ''))}
        keyboardType="number-pad"
        placeholder="0.00"
        prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />}
      />
      <View style={{ height: 6 }} />
      <BalanceHint amount={amount} balance={balance} />

      <Btn label="Continue" disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm airtime"
        total={amount}
        balance={balance}
        rows={[['Network', network.name], ['Phone', phone], ['Amount', money(amount)]]}
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

export default BuyAirtime;
