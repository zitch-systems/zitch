import React, { useEffect, useState } from 'react';
import { View, Text, Alert } from 'react-native';
import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import { apiJson } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Sheet, PinPad, money } from '@/components/design/ui';
import { Label, ProviderGrid, QuickAmounts, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const AMOUNTS = [200, 500, 1000, 2000, 5000, 10000];
type Platform = { code: string; name: string; color: string };
type Step = null | 'confirm' | 'pin';

const Betting = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const [token, setToken] = useState('');
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [selected, setSelected] = useState('');
  const [userId, setUserId] = useState('');
  const [amt, setAmt] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => { getToken().then((t) => t && setToken(t)); }, []);
  useEffect(() => {
    fetch(`${baseUrl}/api/betting/list/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then((r) => r.json())
      .then((res) => { if (res.platforms) { setPlatforms(res.platforms); if (res.platforms[0]) setSelected(res.platforms[0].code); } })
      .catch(() => {});
  }, []);

  const platform = platforms.find((p) => p.code === selected);
  const amount = Number(amt || 0);
  const valid = !!platform && userId.length >= 4 && amount >= 100;

  const fund = async (pin: string) => {
    setBusy(true);
    try {
      const res = await apiJson('/api/betting/fund/', { platform: selected, user_id: userId, amount: amt, transaction_pin: pin });
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

  if (done && platform) {
    return (
      <Screen scroll={false}>
        <Receipt
          title="Wallet funded"
          message={`${money(amount)} added to your ${platform.name} account ${userId}.`}
          rows={[['Platform', platform.name], ['User ID', userId], ['Fee', '₦0'], ['Total', money(amount), true]]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Betting" sub="Fund your betting wallet instantly" onBack={() => router.back()} />

      <Label>Select platform</Label>
      <ProviderGrid items={platforms.map((p) => ({ id: p.code, name: p.name, color: p.color }))} value={selected} onPick={setSelected} cols={3} />

      <Field
        label="User ID"
        value={userId}
        onChangeText={(v) => setUserId(v.replace(/\s/g, '').slice(0, 20))}
        placeholder="Enter betting ID"
        prefix={<ZIcon name="dice" size={18} color={c.ink3} />}
      />
      <View style={{ height: 16 }} />

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

      <Btn label={amount > 0 ? `Continue · ${money(amount)}` : 'Continue'} disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm funding"
        total={amount}
        balance={balance}
        rows={platform ? [['Platform', platform.name], ['User ID', userId]] : []}
        onPay={() => { setStep(null); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => fund(p)} />
      </Sheet>
    </Screen>
  );
};

export default Betting;
