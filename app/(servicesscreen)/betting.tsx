import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { publicPost } from '@/lib/api';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import { bettingService } from '@/lib/services/bills';
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
  const { balance, reload, billPaymentsAvailable } = useWallet();
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [quoteRevision, setQuoteRevision] = useState(0);
  const [catalogueLoading, setCatalogueLoading] = useState(true);
  const [selected, setSelected] = useState('');
  const [userId, setUserId] = useState('');
  const [amt, setAmt] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const purchaseInFlight = useRef(false);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState('');
  const [recovered, setRecovered] = useState(false);
  const [txnRef, setTxnRef] = useState('');
  const [pinError, setPinError] = useState('');
  useEffect(() => {
    let current = true;
    setCatalogueLoading(true);
    publicPost('/api/betting/list/', {}, 15000)
      .then((r) => { if (r.ok === false) throw new Error('Catalogue unavailable'); return r.json(); })
      .then((res) => { if (current && Array.isArray(res.platforms)) { setPlatforms(res.platforms); if (res.platforms[0]) setSelected(res.platforms[0].code); } })
      .catch(() => {})
      .finally(() => { if (current) setCatalogueLoading(false); });
    return () => { current = false; };
  }, [quoteRevision]);

  const platform = platforms.find((p) => p.code === selected);
  const amount = Number(amt || 0);
  const valid = billPaymentsAvailable === true && !!platform && userId.length >= 4 && Number.isFinite(amount) && amount >= 100 && amount <= balance;

  const fund = async (pin: string) => {
    if (!valid || done || purchaseInFlight.current) return;
    purchaseInFlight.current = true;
    const fingerprint = [selected, userId.trim(), String(amount)].join('|');
    let deliveryStarted = false;
    setBusy(true);
    try {
      const requestKey = await acquireSpendAttempt('betting', fingerprint);
      deliveryStarted = true;
      const res = await bettingService.fund(selected, userId, amt, pin, requestKey);
      const outcome = classifySpendResponse(res);
      if (outcome === 'success') {
        await clearSpendAttempt('betting', fingerprint, requestKey);
        setRecovered(isRecoveredSpendResponse(res));
        setTxnRef(String(res.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (outcome === 'pending' || outcome === 'unknown') {
        setPending(true);
        setPendingMessage(outcome === 'pending'
          ? (res.message || 'Your betting-wallet funding is processing. Its final status will update only after provider confirmation.')
          : 'We could not confirm this funding attempt. Check History before trying again.');
        setTxnRef(String(res.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') {
        setPinError(res.message || 'Incorrect PIN');
      } else {
        await clearSpendAttempt('betting', fingerprint, requestKey);
        notify('Error', res.message || 'Transaction failed');
        setStep(null);
      }
    } catch {
      if (deliveryStarted) {
        setPending(true);
        setPendingMessage('We could not confirm this funding attempt. Check History before trying again.');
        setStep(null);
        setDone(true);
        reload();
      } else {
        notify('Unable to start funding', 'Could not safely prepare this request. Please try again.');
      }
    } finally {
      purchaseInFlight.current = false;
      setBusy(false);
    }
  };

  if (done && platform) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Funding processing' : recovered ? 'Earlier attempt confirmed' : 'Wallet funded'}
          message={pending
            ? pendingMessage
            : recovered
              ? 'This confirms your earlier betting-wallet funding. No new funding was made. Start a new purchase to fund again.'
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
      {catalogueLoading ? <Text style={{ color: c.ink3, fontFamily: font.regular, marginBottom: 12 }}>Loading available platforms…</Text> : platforms.length === 0 ? <View style={{ marginBottom: 16 }}><Text style={{ color: c.ink3, fontFamily: font.regular }}>No platforms are available right now.</Text><Btn label="Try again" variant="outline" onPress={() => setQuoteRevision((value) => value + 1)} /></View> : null}

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
      {billPaymentsAvailable !== true ? <Text style={{ color: c.ink3, fontFamily: font.regular, marginBottom: 12 }}>Bill payments are currently unavailable. Refresh your wallet or try again later.</Text> : null}

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
