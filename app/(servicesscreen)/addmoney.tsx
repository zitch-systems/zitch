import React, { useEffect, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as WebBrowser from 'expo-web-browser';
import * as Clipboard from 'expo-clipboard';
import { notify } from '@/components/design/Notify';
import { router } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { apiJson } from '@/lib/api';
import { Screen, Header, Field, Btn, money, Naira } from '@/components/design/ui';
import { Label, QuickAmounts } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const FUND_AMOUNTS = [1000, 2000, 5000, 10000, 20000, 50000];

type DediAccount = { account_number: string; account_name: string; bank_name: string };

const AddMoney = () => {
  const { c } = useTheme();
  const { reload } = useWallet();
  const [token, setToken] = useState('');
  const [amt, setAmt] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [funded, setFunded] = useState('0');
  const [account, setAccount] = useState<DediAccount | null>(null);

  useEffect(() => { getToken().then((t) => t && setToken(t)); }, []);

  // A user's dedicated Zitch account number (Monnify reserved account) — funding
  // it by bank transfer credits the wallet automatically via webhook, no checkout.
  // Only present once KYC (BVN/NIN) is done; otherwise we just show the card flow.
  useEffect(() => {
    apiJson('/api/wallet/account/')
      .then((r) => { if (r?.success && r.account_number) setAccount(r as DediAccount); })
      .catch(() => {});
  }, []);

  const copyAccount = async () => {
    if (!account) return;
    await Clipboard.setStringAsync(account.account_number);
    notify('Copied', 'Account number copied to clipboard');
  };

  const amount = Number(amt || 0);

  const fund = async () => {
    if (amount < 100) {
      notify('Error', 'Minimum funding amount is ₦100');
      return;
    }
    setBusy(true);
    try {
      // 1. initialize -> get reference + checkout url
      const initRes = await apiJson('/api/fund/initialize/', { amount: amt });

      if (!initRes.success) {
        notify('Error', initRes.message || 'Could not start payment');
        return;
      }

      // 2. open Monnify checkout (skipped in mock mode where url is mock://)
      const url = initRes.authorization_url || '';
      if (url && url.startsWith('http')) {
        await WebBrowser.openBrowserAsync(url);
      }

      // 3. verify -> credits the wallet once confirmed
      const verifyRes = await apiJson('/api/fund/verify/', { reference: initRes.reference });

      if (verifyRes.success) {
        setFunded(verifyRes.wallet ?? '0');
        setDone(true);
        reload();
      } else {
        notify('Payment not confirmed', verifyRes.message || 'We could not confirm your payment yet.');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
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

      {account && (
        <View style={{ marginBottom: 22 }}>
          <Label>Fund by bank transfer</Label>
          <View style={{ backgroundColor: c.surface, borderRadius: 16, borderWidth: 1, borderColor: c.line, padding: 16 }}>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>
              Transfer to this account from any bank — your wallet is credited automatically.
            </Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 12 }}>
              <View>
                <Text style={{ fontSize: 22, color: c.ink1, fontFamily: font.bold, letterSpacing: 1 }}>
                  {account.account_number}
                </Text>
                <Text style={{ fontSize: 13, color: c.ink2, fontFamily: font.regular, marginTop: 2 }}>
                  {account.bank_name}{account.account_name ? ` · ${account.account_name}` : ''}
                </Text>
              </View>
              <Pressable onPress={copyAccount} hitSlop={10} style={{ backgroundColor: c.surface2, borderRadius: 10, paddingVertical: 8, paddingHorizontal: 14, borderWidth: 1, borderColor: c.line }}>
                <Text style={{ fontSize: 13, color: c.brand, fontFamily: font.bold }}>Copy</Text>
              </Pressable>
            </View>
          </View>
          <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 14, textAlign: 'center', fontFamily: font.regular }}>
            Or fund instantly with your card below
          </Text>
        </View>
      )}

      <Label>Choose amount</Label>
      <QuickAmounts amounts={FUND_AMOUNTS} value={amt} onPick={setAmt} />
      <Field
        label="Or enter amount"
        value={amt}
        onChangeText={(v) => setAmt(v.replace(/\D/g, ''))}
        keyboardType="number-pad"
        placeholder="0.00"
        prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />}
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
