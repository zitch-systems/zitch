import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import { apiJson, newIdempotencyKey } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, Naira } from '@/components/design/ui';
import { Label, ProviderGrid, QuickAmounts, ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
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
  const [pinError, setPinError] = useState('');
  const idemKey = useRef('');  // stable across retries of one funding attempt

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
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      const res = await apiJson('/api/betting/fund/', { platform: selected, user_id: userId, amount: amt, transaction_pin: pin, idempotency_key: idemKey.current });
      if (res.success) {
        idemKey.current = '';
        setStep(null);
        setDone(true);
        reload();
      } else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') {
        setPinError(res.message || 'Incorrect PIN');
      } else {
        idemKey.current = '';  // definitive server failure — a retry is a fresh attempt
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
        prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />}
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
        onPay={() => { setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => fund(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default Betting;
