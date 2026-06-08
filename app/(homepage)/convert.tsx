import React, { useCallback, useRef, useState } from 'react';
import { View, Text, Alert } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import { apiJson, apiPost, newIdempotencyKey } from '@/lib/api';
import { Screen, Card, Field, Btn, Sheet, PinPad, Money, money } from '@/components/design/ui';
import { Label, ProviderGrid, QuickAmounts, QUICK_AMOUNTS } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const NETWORKS = [
  { id: '1', name: 'MTN', color: '#FFCC00' },
  { id: '2', name: 'GLO', color: '#2BB24C' },
  { id: '3', name: 'Airtel', color: '#E40000' },
  { id: '4', name: '9mobile', color: '#0A8A3D' },
];

const DEFAULT_RATES: Record<string, number> = { '1': 0.8, '2': 0.75, '3': 0.8, '4': 0.75 };

type Step = null | 'confirm' | 'pin';

const Convert = () => {
  const { c } = useTheme();
  const { reload } = useWallet();
  const [rates, setRates] = useState<Record<string, number>>(DEFAULT_RATES);
  const [net, setNet] = useState('1');
  const [phone, setPhone] = useState('');
  const [amt, setAmt] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [payoutDone, setPayoutDone] = useState(0);
  const [pinError, setPinError] = useState('');
  const idemKey = useRef('');

  // Pull live rates so the payout preview matches what the server will credit.
  useFocusEffect(
    useCallback(() => {
      (async () => {
        try {
          const res = await apiJson('/api/convert/rates/');
          if (Array.isArray(res?.rates)) {
            const map: Record<string, number> = {};
            res.rates.forEach((r: any) => { map[String(r.network)] = Number(r.rate); });
            if (Object.keys(map).length) setRates(map);
          }
        } catch {
          // keep defaults
        }
      })();
    }, [])
  );

  const network = NETWORKS.find((n) => n.id === net)!;
  const amount = Number(amt || 0);
  const rate = rates[net] ?? 0.75;
  const payout = Math.floor(amount * rate * 100) / 100;
  const valid = phone.length >= 10 && amount >= 100;

  const convert = async (enteredPin: string) => {
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      const response = await apiPost('/api/convert/airtime/', {
        network: net,
        phone,
        amount: amt,
        transaction_pin: enteredPin,
        idempotency_key: idemKey.current,
      });
      const result = await response.json();
      if (response.ok && result.success) {
        idemKey.current = '';
        setPayoutDone(Number(result.payout ?? payout));
        setStep(null);
        setDone(true);
        reload();
      } else if (result.code === 'pin_incorrect' || result.code === 'pin_locked') {
        setPinError(result.message || 'Incorrect PIN');
      } else {
        idemKey.current = '';
        Alert.alert('Error', result.message || 'Conversion failed');
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
          title="Successful"
          message={`Your airtime was converted to wallet cash.`}
          rows={[
            ['Type', 'Airtime → Cash'],
            ['Network', network.name],
            ['From', phone],
            ['Airtime', money(amount)],
            ['Rate', `${Math.round(rate * 100)}%`],
            ['You received', money(payoutDone), true],
          ]}
          onDone={() => { setDone(false); setAmt(''); setPhone(''); router.replace('/home'); }}
        />
      </Screen>
    );
  }

  return (
    <Screen pad={false} tab>
      <Text style={{ paddingHorizontal: 20, paddingTop: 6, fontSize: 26, fontFamily: font.extrabold, color: c.ink1 }}>Convert</Text>
      <Text style={{ paddingHorizontal: 20, marginTop: 2, fontSize: 13.5, color: c.ink3, fontFamily: font.regular }}>
        Turn unused airtime into wallet cash
      </Text>

      <View style={{ paddingHorizontal: 20, paddingTop: 18 }}>
        <Label>Select network</Label>
        <ProviderGrid items={NETWORKS} value={net} onPick={setNet} />

        <Field
          label="Airtime phone number"
          value={phone}
          onChangeText={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))}
          keyboardType="number-pad"
          placeholder="0801 234 5678"
        />
        <View style={{ height: 16 }} />

        <Label>Airtime amount</Label>
        <QuickAmounts amounts={QUICK_AMOUNTS} value={amt} onPick={setAmt} />
        <Field
          label="Or enter amount"
          value={amt}
          onChangeText={(v) => setAmt(v.replace(/\D/g, ''))}
          keyboardType="number-pad"
          placeholder="0.00"
          prefix={<Text style={{ fontFamily: font.extrabold, color: c.ink2, fontSize: 16 }}>₦</Text>}
        />

        {/* payout preview */}
        <Card style={{ marginTop: 16, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>You receive ({Math.round(rate * 100)}%)</Text>
            <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.brand, marginTop: 2, fontVariant: ['tabular-nums'] }}>
              {money(payout)}
            </Text>
          </View>
          <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ color: c.brand, fontFamily: font.extrabold, fontSize: 18 }}>₦</Text>
          </View>
        </Card>

        <View style={{ height: 18 }} />
        <Btn label="Convert" disabled={!valid} onPress={() => { setPinError(''); setStep('confirm'); }} />
      </View>

      {/* confirm sheet */}
      <Sheet open={step === 'confirm'} onClose={() => setStep(null)} title="Confirm conversion">
        <View style={{ alignItems: 'center', marginBottom: 18, marginTop: -4 }}>
          <Text style={{ fontSize: 13, fontFamily: font.semibold, color: c.ink3 }}>You'll receive</Text>
          <Money amount={payout} size={34} />
        </View>
        <View style={{ borderRadius: 14, backgroundColor: c.surface2, borderWidth: 1, borderColor: c.line, padding: 14, marginBottom: 18, gap: 10 }}>
          {[['Network', network.name], ['Phone', phone], ['Airtime', money(amount)], ['Rate', `${Math.round(rate * 100)}%`]].map((r) => (
            <View key={r[0]} style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
              <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular }}>{r[0]}</Text>
              <Text style={{ fontSize: 13.5, color: c.ink1, fontFamily: font.semibold }}>{r[1]}</Text>
            </View>
          ))}
        </View>
        <Btn label="Continue" onPress={() => { setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }} />
      </Sheet>

      {/* pin sheet */}
      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Processing…' : `Confirm conversion of ${money(amount)} airtime`}
        </Text>
        <PinPad onComplete={(p) => convert(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default Convert;
