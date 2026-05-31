import React, { useEffect, useState } from 'react';
import { View, Text, Alert } from 'react-native';
import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Sheet, PinPad, money } from '@/components/design/ui';
import { Label, Segmented, QuickAmounts, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const AMOUNTS = [1000, 2000, 5000, 10000, 20000, 50000];

type Step = null | 'confirm' | 'pin';

const SendMoney = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const [token, setToken] = useState('');
  const [mode, setMode] = useState('zitch'); // 'zitch' | 'bank'
  const [identifier, setIdentifier] = useState('');
  const [resolvedName, setResolvedName] = useState('');
  const [resolving, setResolving] = useState(false);
  const [amt, setAmt] = useState('');
  const [note, setNote] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => { getToken().then((t) => t && setToken(t)); }, []);
  useEffect(() => { setResolvedName(''); }, [identifier, mode]);

  const amount = Number(amt || 0);
  const valid = mode === 'zitch' && !!resolvedName && amount >= 10;

  const resolve = async () => {
    if (identifier.trim().length < 4) {
      Alert.alert('Error', 'Enter the recipient phone number.');
      return;
    }
    setResolving(true);
    try {
      const res = await fetch(`${baseUrl}/api/transfer/resolve/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token, identifier }),
      }).then((r) => r.json());
      if (res.success) setResolvedName(res.name);
      else Alert.alert('Not found', res.message || 'No Zitch user with that detail.');
    } catch {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setResolving(false);
    }
  };

  const send = async (pin: string) => {
    setBusy(true);
    try {
      const res = await fetch(`${baseUrl}/api/transfer/send/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token, identifier, amount: amt, transaction_pin: pin, note }),
      }).then((r) => r.json());
      if (res.success) {
        setStep(null);
        setDone(true);
        reload();
      } else {
        Alert.alert('Error', res.message || 'Transfer failed');
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
          title="Money sent"
          message={`${money(amount)} sent to ${resolvedName}.`}
          rows={[['Recipient', resolvedName], ['Phone', identifier], ...(note ? ([['Note', note]] as [string, string][]) : []), ['Fee', '₦0'], ['Total', money(amount), true]]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Send money" onBack={() => router.back()} />

      <Segmented
        options={[{ v: 'zitch', label: 'To Zitch' }, { v: 'bank', label: 'To Bank' }]}
        value={mode}
        onChange={setMode}
      />

      {mode === 'bank' ? (
        <View style={{ alignItems: 'center', paddingTop: 40 }}>
          <View style={{ width: 88, height: 88, borderRadius: 28, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="bank" size={40} color={c.brand} />
          </View>
          <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1, marginTop: 20 }}>Bank transfers — coming soon</Text>
          <Text style={{ fontSize: 14, color: c.ink3, marginTop: 8, textAlign: 'center', maxWidth: 280, fontFamily: font.regular }}>
            Send to any Nigerian bank account. For now, transfer instantly to other Zitch users.
          </Text>
        </View>
      ) : (
        <>
          <Field
            label="Recipient phone (Zitch)"
            value={identifier}
            onChangeText={(v) => setIdentifier(v.replace(/[^\d@a-zA-Z]/g, '').slice(0, 15))}
            keyboardType="number-pad"
            placeholder="0801 234 5678"
            prefix={<ZIcon name="user" size={18} color={c.ink3} />}
          />
          <View style={{ marginTop: 8, marginBottom: 8 }}>
            {resolvedName ? (
              <Text style={{ color: c.brandDeep, fontFamily: font.semibold, fontSize: 12.5 }}>✓ {resolvedName}</Text>
            ) : (
              <Btn label="Confirm recipient" variant="outline" size="sm" full={false} onPress={resolve} disabled={resolving} />
            )}
          </View>

          <Label>Amount</Label>
          <QuickAmounts amounts={AMOUNTS} value={amt} onPick={setAmt} />
          <Field
            value={amt}
            onChangeText={(v) => setAmt(v.replace(/\D/g, ''))}
            keyboardType="number-pad"
            placeholder="Enter amount"
            prefix={<Text style={{ fontFamily: font.extrabold, color: c.ink2, fontSize: 16 }}>₦</Text>}
          />
          <View style={{ height: 6 }} />
          <BalanceHint amount={amount} balance={balance} />

          <Field label="Narration (optional)" value={note} onChangeText={setNote} placeholder="What's it for?" />
          <View style={{ height: 20 }} />

          <Btn label="Continue" disabled={!valid} onPress={() => setStep('confirm')} />
        </>
      )}

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm transfer"
        total={amount}
        balance={balance}
        rows={[['To', resolvedName], ['Phone', identifier], ...(note ? ([['Note', note]] as [string, string][]) : [])]}
        onPay={() => { setStep(null); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Sending…' : `Confirm transfer of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => send(p)} />
      </Sheet>
    </Screen>
  );
};

export default SendMoney;
