import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { apiJson, newIdempotencyKey, publicJson } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Field, Btn, Sheet, PinPad, money } from '@/components/design/ui';
import { Label, ProviderGrid, QuickAmounts, ConfirmSheet, BalanceHint, AmountField } from '@/components/design/flowkit';
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
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [selected, setSelected] = useState('');
  const [userId, setUserId] = useState('');
  const [amt, setAmt] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  // The ledger reference the server minted for this transaction — shown on the
  // receipt and carried into the saved/shared file, so a support ticket can name it.
  const [txnRef, setTxnRef] = useState('');
  const [pending, setPending] = useState(false);  // provider-pending: held, confirmed later
  const [pinError, setPinError] = useState('');
  const idemKey = useRef('');  // stable across retries of one funding attempt

  // Any edit to the funding details is a new spend — drop the retained key so a
  // stale one can't replay the PRIOR attempt for the edited one (mirrors sendmoney).
  useEffect(() => { idemKey.current = ''; }, [selected, userId, amt]);

  useEffect(() => {
    let active = true;
    publicJson('/api/betting/list/')
      .then((res) => { if (active && Array.isArray(res.platforms)) { setPlatforms(res.platforms); if (res.platforms[0]) setSelected(res.platforms[0].code); } })
      .catch(() => {})
      .finally(() => { if (active) setLoadingList(false); });
    return () => { active = false; };
  }, []);

  const platform = platforms.find((p) => p.code === selected);
  const amount = Number(amt || 0);
  const valid = !!platform && userId.length >= 4 && amount >= 100;

  const fund = async (pin: string) => {
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      const res = await apiJson('/api/betting/fund/', { platform: selected, user_id: userId, amount: amt, transaction_pin: pin, idempotency_key: idemKey.current });
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

  if (done && platform) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Processing' : 'Wallet funded'}
          message={pending
            ? `${money(amount)} to your ${platform.name} account ${userId} is processing and will be confirmed shortly. If it can't be completed, you'll be refunded automatically.`
            : `${money(amount)} added to your ${platform.name} account ${userId}.`}
          rows={[['Platform', platform.name], ['User ID', userId], ['Fee', '₦0'], ['Total', money(amount), true]]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Betting" sub="Fund your betting wallet instantly" onBack={() => router.back()} />

      <Label>Select platform</Label>
      {loadingList ? (
        <View style={{ marginBottom: 16 }}><Loading full={false} /></View>
      ) : platforms.length === 0 ? (
        <Text style={{ color: c.ink3, fontFamily: font.regular, marginBottom: 16 }}>No betting platforms available right now. Please try again later.</Text>
      ) : (
        <ProviderGrid items={platforms.map((p) => ({ id: p.code, name: p.name, color: p.color }))} value={selected} onPick={setSelected} cols={3} />
      )}

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
      <AmountField value={amt} onChangeText={setAmt} />
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
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => fund(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default Betting;

