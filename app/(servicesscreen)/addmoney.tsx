import React, { useEffect, useState } from 'react';
import { View, Text, Alert } from 'react-native';
import * as WebBrowser from 'expo-web-browser';
import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import { Screen, Header, Field, Btn, money } from '@/components/design/ui';
import { Label, QuickAmounts } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const FUND_AMOUNTS = [1000, 2000, 5000, 10000, 20000, 50000];

const AddMoney = () => {
  const { c } = useTheme();
  const { reload } = useWallet();
  const [token, setToken] = useState('');
  const [amt, setAmt] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [funded, setFunded] = useState('0');

  useEffect(() => { getToken().then((t) => t && setToken(t)); }, []);

  const amount = Number(amt || 0);

  const fund = async () => {
    if (amount < 100) {
      Alert.alert('Error', 'Minimum funding amount is ₦100');
      return;
    }
    setBusy(true);
    try {
      // 1. initialize -> get reference + checkout url
      const initRes = await fetch(`${baseUrl}/api/fund/initialize/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token, amount: amt }),
      }).then((r) => r.json());

      if (!initRes.success) {
        Alert.alert('Error', initRes.message || 'Could not start payment');
        return;
      }

      // 2. open Monnify checkout (skipped in mock mode where url is mock://)
      const url = initRes.authorization_url || '';
      if (url && url.startsWith('http')) {
        await WebBrowser.openBrowserAsync(url);
      }

      // 3. verify -> credits the wallet once confirmed
      const verifyRes = await fetch(`${baseUrl}/api/fund/verify/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token, reference: initRes.reference }),
      }).then((r) => r.json());

      if (verifyRes.success) {
        setFunded(verifyRes.wallet ?? '0');
        setDone(true);
        reload();
      } else {
        Alert.alert('Payment not confirmed', verifyRes.message || 'We could not confirm your payment yet.');
      }
    } catch {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen scroll={false}>
        <Receipt
          title="Wallet funded"
          message={`${money(amount)} has been added to your Zitch wallet.`}
          rows={[['Amount', money(amount)], ['Method', 'Card / Bank'], ['New balance', money(Number(funded)), true]]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Add money" onBack={() => router.back()} />
      <Label>Choose amount</Label>
      <QuickAmounts amounts={FUND_AMOUNTS} value={amt} onPick={setAmt} />
      <Field
        label="Or enter amount"
        value={amt}
        onChangeText={(v) => setAmt(v.replace(/\D/g, ''))}
        keyboardType="number-pad"
        placeholder="0.00"
        prefix={<Text style={{ fontFamily: font.extrabold, color: c.ink2, fontSize: 16 }}>₦</Text>}
      />
      <View style={{ height: 24 }} />
      <Btn label={amount > 0 ? `Fund ${money(amount)}` : 'Fund wallet'} icon="plus" disabled={busy || amount < 100} onPress={fund} />
      <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 14, textAlign: 'center', fontFamily: font.regular }}>
        Secured by Monnify · cards & bank transfer
      </Text>
    </Screen>
  );
};

export default AddMoney;
