import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Alert, Pressable, ScrollView } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import { apiPost, apiJson, newIdempotencyKey } from '@/lib/api';
import { isBiometricAvailable, authenticate } from '@/lib/biometrics';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, Naira } from '@/components/design/ui';
import { Label, Segmented, QuickAmounts, ConfirmSheet, BalanceHint, Monogram } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const AMOUNTS = [1000, 2000, 5000, 10000, 20000, 50000];
// Mirrors backend User.LARGE_TXN_THRESHOLD — drives the device biometric step-up.
const LARGE_TXN = 100000;
type Step = null | 'confirm' | 'pin';
type Bank = { code: string; name: string; color: string };
type Beneficiary = { id: number; name: string; account_number: string; bank_name: string; initials: string; color: string };

const SendMoney = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const params = useLocalSearchParams<{ identifier?: string }>();

  const [token, setToken] = useState('');
  const [mode, setMode] = useState<'bank' | 'zitch'>('bank');
  const [banks, setBanks] = useState<Bank[]>([]);
  const [beneficiaries, setBeneficiaries] = useState<Beneficiary[]>([]);
  const [query, setQuery] = useState('');
  const [picked, setPicked] = useState<Beneficiary | null>(null);

  // bank mode
  const [acct, setAcct] = useState(params.identifier?.replace(/\D/g, '').slice(0, 10) ?? '');
  const [bank, setBank] = useState<Bank | null>(null);
  // zitch mode
  const [identifier, setIdentifier] = useState('');
  const [resolvedName, setResolvedName] = useState('');
  const [resolving, setResolving] = useState(false);

  const [amt, setAmt] = useState('');
  const [note, setNote] = useState('');
  const [bankSheet, setBankSheet] = useState(false);
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pinError, setPinError] = useState('');

  useEffect(() => {
    getToken().then((t) => {
      if (!t) return;
      setToken(t);
      fetch(`${baseUrl}/api/transfers/banks/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then((r) => r.json()).then((res) => res.banks && setBanks(res.banks)).catch(() => {});
      apiPost('/api/transfers/beneficiaries/')
        .then((r) => r.json()).then((res) => res.beneficiaries && setBeneficiaries(res.beneficiaries)).catch(() => {});
    });
  }, []);

  // Auto-detect bank once a 10-digit account number is entered (mirrors prototype).
  useEffect(() => {
    if (mode === 'bank' && acct.length === 10 && !bank && banks.length) {
      setBank(banks[Number(acct[0] || 0) % banks.length]);
    }
  }, [acct, mode, banks]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setResolvedName(''); }, [identifier]);

  const amount = Number(amt || 0);
  // For bank mode we resolve a name lazily; treat a 10-digit acct + bank as ready.
  const [bankName, setBankName] = useState('');
  useEffect(() => { setBankName(''); }, [acct, bank]);

  const acctReady = mode === 'bank' ? acct.length === 10 && !!bank : !!resolvedName;
  const recipientName = picked ? picked.name : mode === 'bank' ? bankName : resolvedName;
  const valid = (!!picked || acctReady) && amount >= 10 && amount <= balance;

  const resolveBank = async () => {
    if (acct.length !== 10 || !bank) return;
    try {
      const res = await apiJson('/api/transfers/resolve/', { account_number: acct, bank: bank.code });
      if (res.success) setBankName(res.name);
    } catch { /* ignore */ }
  };
  useEffect(() => { if (mode === 'bank' && acct.length === 10 && bank) resolveBank(); }, [acct, bank]); // eslint-disable-line

  const resolveZitch = async () => {
    if (identifier.trim().length < 4) { notify('Error', 'Enter the recipient phone number.'); return; }
    setResolving(true);
    try {
      const res = await apiJson('/api/transfer/resolve/', { identifier });
      if (res.success) setResolvedName(res.name);
      else notify('Not found', res.message || 'No Zitch user with that detail.');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setResolving(false); }
  };

  const postSend = async (pin: string) => {
    const usingBank = (picked && picked.bank_name !== 'Zitch') || (!picked && mode === 'bank');
    if (usingBank) {
      const accountNumber = picked ? picked.account_number : acct;
      const bankNameFinal = picked ? picked.bank_name : bank?.name;
      const bankCode = picked ? banks.find((b) => b.name === bankNameFinal)?.code : bank?.code;
      return apiJson('/api/transfers/send/', {
        account_number: accountNumber, bank: bankCode, name: recipientName, amount: amt,
        transaction_pin: pin, note, idempotency_key: idemKey.current,
      });
    }
    const id = picked ? picked.account_number : identifier;
    return apiJson('/api/transfer/send/', {
      identifier: id, amount: amt, transaction_pin: pin, note, idempotency_key: idemKey.current,
    });
  };

  const idemKey = useRef('');  // stable across retries of one transfer attempt

  const send = async (pin: string) => {
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      // Defense-in-depth: a device biometric step-up for large transfers, on top
      // of the transaction PIN and the server-side face_verified gate. If the
      // device has no enrolled biometrics, the PIN + server checks still apply.
      if (amount >= LARGE_TXN && (await isBiometricAvailable())) {
        const okScan = await authenticate(`Authorize ${money(amount)} transfer`);
        if (!okScan) { setStep(null); return; }
      }
      const res = await postSend(pin);

      // Large transfers need durable face verification (done once in KYC).
      if (!res.success && res.code === 'face_required') {
        setStep(null);
        Alert.alert(
          'Face verification needed',
          'For transfers this large, verify your identity once in KYC. It only takes a moment.',
          [
            { text: 'Not now', style: 'cancel' },
            { text: 'Verify now', onPress: () => router.push('/kyc') },
          ],
        );
        return;
      }

      if (res.success) { idemKey.current = ''; setStep(null); setDone(true); reload(); }
      else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') { setPinError(res.message || 'Incorrect PIN'); }
      else { idemKey.current = ''; notify('Error', res.message || 'Transfer failed'); setStep(null); }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.'); setStep(null);
    } finally { setBusy(false); }
  };

  if (done) {
    const acctShown = picked ? picked.account_number : mode === 'bank' ? acct : identifier;
    const bankShown = picked ? picked.bank_name : mode === 'bank' ? bank?.name || 'Bank' : 'Zitch';
    return (
      <Screen scroll={false}>
        <Receipt
          title="Money sent"
          message={`${money(amount)} sent to ${recipientName || 'recipient'}.`}
          rows={[['Recipient', recipientName || '—'], ['Account', acctShown], ['Bank', bankShown], ...(note ? ([['Note', note]] as [string, string][]) : []), ['Fee', '₦0'], ['Total', money(amount), true]]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  const filteredBens = beneficiaries.filter((b) => (b.name + ' ' + b.account_number).toLowerCase().includes(query.toLowerCase()));

  return (
    <Screen>
      <Header title="Send money" onBack={() => router.back()} />

      <Segmented
        options={[{ v: 'bank', label: 'To Bank' }, { v: 'zitch', label: 'To Zitch' }]}
        value={mode}
        onChange={(v) => { setMode(v as any); setPicked(null); setAcct(''); setBank(null); setIdentifier(''); setResolvedName(''); }}
      />

      {picked ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, marginBottom: 16 }}>
          <Monogram text={picked.initials} color={picked.color} />
          <View style={{ flex: 1 }}>
            <Text style={{ fontFamily: font.bold, color: c.ink1 }}>{picked.name}</Text>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>{picked.account_number} · {picked.bank_name}</Text>
          </View>
          <Pressable onPress={() => setPicked(null)}><Text style={{ fontSize: 13, fontFamily: font.bold, color: c.brand }}>Change</Text></Pressable>
        </View>
      ) : mode === 'bank' ? (
        <>
          <Field label="Account number" value={acct} onChangeText={(v) => setAcct(v.replace(/\D/g, '').slice(0, 10))} keyboardType="number-pad" placeholder="Enter 10-digit account number" prefix={<ZIcon name="bank" size={18} color={c.ink3} />} />
          <View style={{ height: 14 }} />
          <Pressable onPress={() => setBankSheet(true)}>
            <Field
              label="Bank"
              value={bank?.name || ''}
              editable={false}
              placeholder={acct.length === 10 ? 'Select bank' : 'Auto-detected after account number'}
              prefix={bank ? <View style={{ width: 18, height: 18, borderRadius: 5, backgroundColor: bank.color }} /> : <ZIcon name="bank" size={18} color={c.ink3} />}
              suffix={<ZIcon name="down" size={16} color={c.ink3} />}
              pointerEvents="none"
            />
          </Pressable>
          {bankName ? <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 12.5, marginTop: 8 }}>✓ {bankName}</Text> : null}
          <View style={{ height: 16 }} />
        </>
      ) : (
        <>
          <Field label="Zitch tag or phone" value={identifier} onChangeText={(v) => setIdentifier(v.replace(/[^\d@a-zA-Z]/g, '').slice(0, 15))} placeholder="@username / 0801…" prefix={<ZIcon name="user" size={18} color={c.ink3} />} />
          <View style={{ marginTop: 8, marginBottom: 8 }}>
            {resolvedName ? <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 12.5 }}>✓ {resolvedName}</Text>
              : <Btn label="Confirm recipient" variant="outline" size="sm" full={false} onPress={resolveZitch} disabled={resolving} />}
          </View>
        </>
      )}

      <Label>Amount</Label>
      <QuickAmounts amounts={AMOUNTS} value={amt} onPick={setAmt} />
      <Field value={amt} onChangeText={(v) => setAmt(v.replace(/\D/g, ''))} keyboardType="number-pad" placeholder="Enter amount" prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />} />
      <View style={{ height: 6 }} />
      <BalanceHint amount={amount} balance={balance} />

      <Field label="Narration (optional)" value={note} onChangeText={setNote} placeholder="What's it for?" />
      <View style={{ height: 20 }} />

      <Btn label="Continue" disabled={!valid} onPress={() => setStep('confirm')} />

      {/* Saved beneficiaries — moved to the bottom; tap one to fill the form above */}
      {!picked && beneficiaries.length > 0 && (
        <>
          <View style={{ height: 28 }} />
          <Label>Saved beneficiaries</Label>
          <Field value={query} onChangeText={setQuery} placeholder="Search by name or account" prefix={<ZIcon name="search" size={18} color={c.ink3} />} />
          <View style={{ height: 12 }} />
          {filteredBens.length === 0 ? (
            <Text style={{ fontSize: 13, color: c.ink3, marginBottom: 14, fontFamily: font.regular }}>No matching beneficiary</Text>
          ) : (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 14, paddingBottom: 4 }}>
              {filteredBens.map((b) => (
                <Pressable key={b.id} onPress={() => setPicked(b)} style={{ alignItems: 'center', gap: 7, width: 64 }}>
                  <Monogram text={b.initials} color={b.color} size={52} />
                  <Text numberOfLines={1} style={{ fontSize: 11, fontFamily: font.semibold, color: c.ink2, textAlign: 'center' }}>{b.name.split(' ')[0]}</Text>
                </Pressable>
              ))}
            </ScrollView>
          )}
        </>
      )}

      {/* Bank picker */}
      <Sheet open={bankSheet} onClose={() => setBankSheet(false)} title="Select bank">
        {banks.map((b, i) => (
          <Pressable key={b.code} onPress={() => { setBank(b); setBankSheet(false); }} style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, borderTopWidth: i === 0 ? 0 : 1, borderTopColor: c.line }}>
            <View style={{ width: 36, height: 36, borderRadius: 11, backgroundColor: b.color, alignItems: 'center', justifyContent: 'center' }}>
              <Text style={{ color: '#fff', fontFamily: font.extrabold, fontSize: 13 }}>{(b.name || '').replace(/[^A-Za-z ]/g, '').split(' ').map((w) => w[0] || '').join('').slice(0, 2).toUpperCase()}</Text>
            </View>
            <Text style={{ flex: 1, fontFamily: font.semibold, color: c.ink1 }}>{b.name}</Text>
            {bank?.code === b.code && <ZIcon name="check" size={18} color={c.brand} />}
          </Pressable>
        ))}
      </Sheet>

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm transfer"
        total={amount}
        balance={balance}
        rows={[['To', recipientName || '—'], ['Account', picked ? picked.account_number : mode === 'bank' ? acct : identifier], ['Bank', picked ? picked.bank_name : mode === 'bank' ? bank?.name || '—' : 'Zitch']]}
        onPay={() => { setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Sending…' : `Confirm transfer of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => send(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default SendMoney;
