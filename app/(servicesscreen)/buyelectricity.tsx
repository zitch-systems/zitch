import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { apiPost, newIdempotencyKey } from '@/lib/api';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, Naira } from '@/components/design/ui';
import { Label, ProviderGrid, Segmented, QuickAmounts, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

// Disco id mapping follows the backend's numeric convention.
const DISCOS = [
  { id: '1', name: 'Ikeja (IKEDC)', color: '#E08A00' },
  { id: '2', name: 'Eko (EKEDC)', color: '#1E5BB8' },
  { id: '3', name: 'Abuja (AEDC)', color: '#0B7A3B' },
  { id: '4', name: 'Kano (KEDCO)', color: '#7A1FA2' },
  { id: '5', name: 'P/H (PHED)', color: '#C0392B' },
  { id: '6', name: 'Jos (JED)', color: '#16667E' },
  { id: '7', name: 'Kaduna', color: '#0B4DA2' },
  { id: '8', name: 'Enugu (EEDC)', color: '#1A8E5F' },
  { id: '9', name: 'Ibadan (IBEDC)', color: '#6C2FB3' },
];
const ELEC_AMOUNTS = [1000, 2000, 5000, 10000, 20000, 50000];

type Step = null | 'confirm' | 'pin';

const BuyElectricity = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const [token, setToken] = useState('');
  const [disco, setDisco] = useState('1');
  const [meterType, setMeterType] = useState('prepaid');
  const [meter, setMeter] = useState('');
  const [amt, setAmt] = useState('');
  const [customerName, setCustomerName] = useState('');
  const [validating, setValidating] = useState(false);
  const [purchasedToken, setPurchasedToken] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pinError, setPinError] = useState('');

  useEffect(() => { getToken().then((t) => t && setToken(t)); }, []);
  useEffect(() => { setCustomerName(''); }, [disco, meterType, meter]);

  const provider = DISCOS.find((d) => d.id === disco)!;
  const amount = Number(amt || 0);
  const valid = meter.length >= 8 && amount >= 500;

  const validateMeter = async () => {
    if (meter.trim().length < 8) { notify('Error', 'Enter a valid meter number.'); return; }
    setValidating(true);
    try {
      const response = await apiPost('/api/utility/validate_meter/', { meter, disco, meter_type: meterType });
      const result = await response.json();
      if (response.ok) {
        setCustomerName(result.customer_name || result.name || 'Verified');
      } else {
        notify('Error', result.message || 'Could not verify meter number.');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setValidating(false);
    }
  };

  const idemKey = useRef('');  // stable across retries of one purchase attempt

  const purchase = async (enteredPin: string) => {
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      const response = await apiPost('/api/utility/buyelectricity/', {
        disco,
        meter,
        meter_type: meterType,
        amount: amt,
        transaction_pin: enteredPin,
        idempotency_key: idemKey.current,
      });
      const result = await response.json();
      if (response.ok) {
        idemKey.current = '';
        if (result.token) setPurchasedToken(String(result.token));
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
          title={purchasedToken ? 'Token generated' : 'Payment successful'}
          message={`Your ${provider.name} ${meterType} purchase was successful.`}
          rows={[
            ['Disco', provider.name],
            ['Meter', meter],
            ['Type', meterType],
            ...(purchasedToken ? ([['Token', purchasedToken]] as [string, string][]) : []),
            ['Total', money(amount), true],
          ]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Electricity" onBack={() => router.back()} />

      <Label>Select disco</Label>
      <ProviderGrid items={DISCOS} value={disco} onPick={setDisco} cols={3} />

      <Segmented
        options={[{ v: 'prepaid', label: 'Prepaid' }, { v: 'postpaid', label: 'Postpaid' }]}
        value={meterType}
        onChange={setMeterType}
      />

      <Field
        label="Meter number"
        value={meter}
        onChangeText={(v) => setMeter(v.replace(/\D/g, '').slice(0, 13))}
        keyboardType="number-pad"
        placeholder="01234567890"
      />
      <View style={{ marginTop: 8, marginBottom: 8 }}>
        {customerName ? (
          <Text style={{ color: c.brandDeep, fontFamily: font.semibold, fontSize: 12.5 }}>✓ {customerName}</Text>
        ) : (
          <Btn label="Validate meter" variant="outline" size="sm" full={false} onPress={validateMeter} disabled={validating} />
        )}
      </View>

      <Label>Amount</Label>
      <QuickAmounts amounts={ELEC_AMOUNTS} value={amt} onPick={setAmt} />
      <Field
        value={amt}
        onChangeText={(v) => setAmt(v.replace(/\D/g, ''))}
        keyboardType="number-pad"
        placeholder="Enter amount (min 500)"
        prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />}
      />
      <View style={{ height: 6 }} />
      <BalanceHint amount={amount} balance={balance} />

      <Btn label="Continue" disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm payment"
        total={amount}
        balance={balance}
        rows={[['Disco', provider.name], ['Meter', meter], ['Type', meterType]]}
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

export default BuyElectricity;
