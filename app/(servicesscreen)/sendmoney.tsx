import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Alert, Pressable, ScrollView } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import { apiPost } from '@/lib/api';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import { EP } from '@/lib/endpoints';
import { transfersService } from '@/lib/services/transfers';
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
type Beneficiary = { id: number; name: string; account_number: string; bank_name: string; bank_code?: string; initials: string; color: string };
type BankMatch = { bank: string; bank_name: string; name: string };

const SendMoney = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const params = useLocalSearchParams<{ identifier?: string }>();

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
  const [resolvedRecipient, setResolvedRecipient] = useState('');
  const [resolvedFor, setResolvedFor] = useState('');
  const [resolving, setResolving] = useState(false);
  const resolveGeneration = useRef(0);

  const [amt, setAmt] = useState('');
  const [note, setNote] = useState('');
  const [bankSheet, setBankSheet] = useState(false);
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState('');
  const [recovered, setRecovered] = useState(false);
  const [txnRef, setTxnRef] = useState('');
  const [pinError, setPinError] = useState('');

  useEffect(() => {
    getToken().then((t) => {
      if (!t) return;
      fetch(`${baseUrl}/api/transfers/banks/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then((r) => r.json()).then((res) => res.banks && setBanks(res.banks)).catch(() => {});
      apiPost(EP.transfers.beneficiaries)
        .then((r) => r.json()).then((res) => res.beneficiaries && setBeneficiaries(res.beneficiaries)).catch(() => {});
    });
  }, []);

  const amount = Number(amt || 0);
  // Bank mode: type a 10-digit account and we AUTO-DETECT the bank — the server
  // name-enquires across banks and returns the match, so the bank + holder name
  // fill in by themselves. The user can still tap the bank field to override
  // (which resolves at just that one bank).
  const [bankName, setBankName] = useState('');   // resolved account holder name
  const [bankNameFor, setBankNameFor] = useState('');
  const [resolvingBank, setResolvingBank] = useState(false);
  const [bankErr, setBankErr] = useState('');
  const [matches, setMatches] = useState<BankMatch[]>([]);  // shown when >1 bank matches
  const bankResolveGeneration = useRef(0);

  const applyMatch = (m: BankMatch, requestedAccount = acct) => {
    setBank(banks.find((b) => b.code === m.bank) || { code: m.bank, name: m.bank_name, color: c.brand });
    setBankName(m.name);
    setBankNameFor(`${requestedAccount}|${m.bank}`);
    setMatches([]);
    setBankErr('');
  };

  // Auto-detect on a 10-digit account. Keyed on acct only, so the bank it sets
  // (or a manual pick) doesn't re-trigger it; editing the account re-detects.
  useEffect(() => {
    if (mode !== 'bank') return;
    const requestedAccount = acct;
    const generation = ++bankResolveGeneration.current;
    setBank(null); setBankName(''); setBankNameFor(''); setBankErr(''); setMatches([]);
    if (requestedAccount.length !== 10) { setResolvingBank(false); return; }
    let cancelled = false;
    setResolvingBank(true);
    const t = setTimeout(async () => {
      try {
        const res = await transfersService.resolve(requestedAccount); // no bank -> auto-detect
        if (cancelled || generation !== bankResolveGeneration.current) return;
        if (res.success && res.matches?.length === 1) applyMatch(res.matches[0], requestedAccount);
        else if (res.success && res.matches?.length) setMatches(res.matches);
        else setBankErr(res.message || "Couldn't detect the bank — tap “Bank” to pick it.");
      } catch {
        if (!cancelled && generation === bankResolveGeneration.current) {
          setBankErr("Couldn't verify this account. Please try again.");
        }
      } finally {
        if (!cancelled && generation === bankResolveGeneration.current) setResolvingBank(false);
      }
    }, 500);
    return () => { cancelled = true; clearTimeout(t); };
  }, [acct, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  // Manual override: resolve at the specific bank the user picks from the sheet.
  const chooseBank = async (b: Bank) => {
    const requestedAccount = acct;
    const generation = ++bankResolveGeneration.current;
    setBank(b); setBankSheet(false); setMatches([]); setBankName(''); setBankNameFor(''); setBankErr('');
    if (requestedAccount.length !== 10) { setResolvingBank(false); return; }
    setResolvingBank(true);
    try {
      const res = await transfersService.resolve(requestedAccount, b.code);
      if (generation !== bankResolveGeneration.current) return;
      if (res.success && res.name) {
        setBankName(res.name);
        setBankNameFor(`${requestedAccount}|${b.code}`);
      }
      else setBankErr(res.message || "Couldn't verify this account at that bank.");
    } catch {
      if (generation === bankResolveGeneration.current) {
        setBankErr("Couldn't verify this account. Please try again.");
      }
    }
    finally {
      if (generation === bankResolveGeneration.current) setResolvingBank(false);
    }
  };

  const identifierKey = identifier.trim().toLowerCase();
  const activeResolvedName = resolvedFor === identifierKey ? resolvedName : '';
  const activeResolvedRecipient = resolvedFor === identifierKey ? resolvedRecipient : '';
  const bankResolutionKey = `${acct.trim()}|${bank?.code || ''}`;
  const activeBankName = bankNameFor === bankResolutionKey ? bankName : '';
  const pickedBankCode = picked?.bank_code
    || banks.find((candidate) => candidate.name === picked?.bank_name)?.code
    || '';
  const pickedReady = !!picked && (picked.bank_name === 'Zitch' || !!pickedBankCode);
  const acctReady = mode === 'bank'
    ? acct.length === 10 && !!bank && !!activeBankName
    : !!activeResolvedName;
  const recipientName = picked ? picked.name : mode === 'bank' ? activeBankName : activeResolvedName;
  const valid = (pickedReady || acctReady) && amount >= 10 && amount <= balance;

  const resolveZitch = async () => {
    const requestedIdentifier = identifier.trim();
    if (requestedIdentifier.length < 4) { notify('Error', 'Enter the recipient phone number.'); return; }
    const requestedFor = requestedIdentifier.toLowerCase();
    const generation = ++resolveGeneration.current;
    setResolving(true);
    try {
      const res = await transfersService.resolveLegacy(requestedIdentifier);
      if (generation !== resolveGeneration.current) return;
      const recipientKey = String(res.recipient_key || '').trim();
      if (res.success && recipientKey) {
        setResolvedName(res.name);
        // The same account may be typed as phone, email or @username. Bind the
        // local durable marker to the backend's immutable opaque account key,
        // never to mutable PII or the alias the customer happened to type.
        setResolvedRecipient(recipientKey);
        setResolvedFor(requestedFor);
      }
      else if (res.success) {
        notify('Unable to confirm recipient', 'Refresh the app and confirm this recipient again.');
      }
      else notify('Not found', res.message || 'No Zitch user with that detail.');
    } catch {
      if (generation === resolveGeneration.current) notify('Error', 'Something went wrong.');
    }
    finally {
      if (generation === resolveGeneration.current) setResolving(false);
    }
  };

  const changeIdentifier = (value: string) => {
    resolveGeneration.current += 1;
    setResolving(false);
    setResolvedName('');
    setResolvedRecipient('');
    setResolvedFor('');
    setIdentifier(value.replace(/[^\d@a-zA-Z]/g, '').slice(0, 15));
  };

  const transferAttempt = () => {
    const usingBank = (picked && picked.bank_name !== 'Zitch') || (!picked && mode === 'bank');
    if (usingBank) {
      const accountNumber = picked ? picked.account_number : acct;
      const bankCode = picked ? pickedBankCode : bank?.code;
      return {
        scope: 'bank-transfer',
        fingerprint: [
          accountNumber.trim(),
          String(bankCode || '').trim(),
          String(amount),
        ].join('|'),
      };
    }
    const id = picked ? picked.account_number : activeResolvedRecipient;
    return {
      scope: 'zitch-transfer',
      fingerprint: [id.trim().toLowerCase(), String(amount)].join('|'),
    };
  };

  const postSend = async (pin: string, idempotencyKey: string) => {
    const usingBank = (picked && picked.bank_name !== 'Zitch') || (!picked && mode === 'bank');
    if (usingBank) {
      const accountNumber = picked ? picked.account_number : acct;
      const bankCode = picked ? pickedBankCode : bank?.code;
      return transfersService.send({
        account_number: accountNumber, bank: bankCode, name: recipientName, amount: amt,
        transaction_pin: pin, note, idempotency_key: idempotencyKey,
      });
    }
    const id = picked ? picked.account_number : identifier;
    return transfersService.sendLegacy({
      identifier: id,
      recipient_key: picked ? undefined : activeResolvedRecipient,
      amount: amt, transaction_pin: pin, note, idempotency_key: idempotencyKey,
    });
  };

  const send = async (pin: string) => {
    const attempt = transferAttempt();
    let requestKey = '';
    let deliveryStarted = false;
    setBusy(true);
    try {
      // Defense-in-depth: a device biometric step-up for large transfers, on top
      // of the transaction PIN and the server-side face_verified gate. If the
      // device has no enrolled biometrics, the PIN + server checks still apply.
      if (amount >= LARGE_TXN && (await isBiometricAvailable())) {
        const okScan = await authenticate(`Authorize ${money(amount)} transfer`);
        if (!okScan) { setStep(null); return; }
      }
      // Persist before delivery. A retry after an app restart therefore presents
      // the same key, while a different material recipient/amount gets its own.
      requestKey = await acquireSpendAttempt(attempt.scope, attempt.fingerprint);
      deliveryStarted = true;
      const res = await postSend(pin, requestKey);
      const outcome = classifySpendResponse(res);

      // Large transfers need durable face verification (done once in KYC).
      if (!res.success && res.code === 'face_required') {
        await clearSpendAttempt(attempt.scope, attempt.fingerprint, requestKey);
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

      if (outcome === 'success') {
        await clearSpendAttempt(attempt.scope, attempt.fingerprint, requestKey);
        setRecovered(isRecoveredSpendResponse(res));
        setTxnRef(String(res.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      }
      else if (outcome === 'pending' || outcome === 'unknown') {
        setPending(true);
        setPendingMessage(outcome === 'pending'
          ? (res.message || 'Your transfer is processing. Its final status will update only after provider confirmation.')
          : 'We could not confirm this transfer. Check History before trying again.');
        setTxnRef(String(res.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      }
      else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') { setPinError(res.message || 'Incorrect PIN'); }
      else {
        await clearSpendAttempt(attempt.scope, attempt.fingerprint, requestKey);
        notify('Error', res.message || 'Transfer failed');
        setStep(null);
      }
    } catch {
      if (deliveryStarted) {
        setPending(true);
        setPendingMessage('We could not confirm this transfer. Check History before trying again.');
        setStep(null);
        setDone(true);
        reload();
      } else {
        notify('Unable to start transfer', 'Could not safely prepare or authorize this request. Please try again.');
      }
    } finally { setBusy(false); }
  };

  if (done) {
    const acctShown = picked ? picked.account_number : mode === 'bank' ? acct : identifier;
    const bankShown = picked ? picked.bank_name : mode === 'bank' ? bank?.name || 'Bank' : 'Zitch';
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Transfer processing' : recovered ? 'Earlier attempt confirmed' : 'Money sent'}
          message={pending
            ? pendingMessage
            : recovered
              ? 'This confirms your earlier transfer. No new transfer was made. Authorize a new transfer to send again.'
            : `${money(amount)} sent to ${recipientName || 'recipient'}.`}
          rows={[['Recipient', recipientName || '—'], ['Account', acctShown], ['Bank', bankShown], ...(note ? ([['Note', note]] as [string, string][]) : []), ['Fee', '₦0'], ['Total', money(amount), true]]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
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
        onChange={(v) => {
          resolveGeneration.current += 1;
          bankResolveGeneration.current += 1;
          setResolving(false);
          setResolvingBank(false);
          setResolvedFor('');
          setResolvedRecipient('');
          setMode(v as any);
          setPicked(null);
          setAcct('');
          setBank(null);
          setBankName('');
          setBankNameFor('');
          setBankErr('');
          setMatches([]);
          setIdentifier('');
          setResolvedName('');
        }}
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
          <Field
            label="Account number"
            value={acct}
            onChangeText={(v) => {
              bankResolveGeneration.current += 1;
              setResolvingBank(false);
              setBank(null);
              setBankName('');
              setBankNameFor('');
              setBankErr('');
              setMatches([]);
              setAcct(v.replace(/\D/g, '').slice(0, 10));
            }}
            keyboardType="number-pad"
            placeholder="Enter 10-digit account number"
            prefix={<ZIcon name="bank" size={18} color={c.ink3} />}
          />
          <View style={{ height: 14 }} />
          <Pressable onPress={() => setBankSheet(true)}>
            <Field
              label="Bank"
              value={bank?.name || ''}
              editable={false}
              placeholder={resolvingBank ? 'Detecting…' : 'Auto-detected from account — or tap to choose'}
              prefix={bank ? <View style={{ width: 18, height: 18, borderRadius: 5, backgroundColor: bank.color }} /> : <ZIcon name="bank" size={18} color={c.ink3} />}
              suffix={<ZIcon name="down" size={16} color={c.ink3} />}
              pointerEvents="none"
            />
          </Pressable>
          {resolvingBank ? (
            <Text style={{ color: c.ink3, fontFamily: font.medium, fontSize: 12.5, marginTop: 8 }}>Detecting bank…</Text>
          ) : matches.length > 1 ? (
            <View style={{ marginTop: 8 }}>
              <Text style={{ color: c.ink3, fontFamily: font.regular, fontSize: 12, marginBottom: 4 }}>Found at more than one bank — pick the right one:</Text>
              {matches.map((m) => (
                <Pressable
                  key={m.bank}
                  onPress={() => {
                    bankResolveGeneration.current += 1;
                    setResolvingBank(false);
                    applyMatch(m);
                  }}
                  style={{ paddingVertical: 7 }}
                >
                  <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 13 }}>{m.bank_name}</Text>
                  <Text style={{ color: c.ink2, fontFamily: font.regular, fontSize: 12 }}>{m.name}</Text>
                </Pressable>
              ))}
            </View>
          ) : activeBankName ? (
            <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 12.5, marginTop: 8 }}>✓ {activeBankName}</Text>
          ) : bankErr ? (
            <Text style={{ color: c.red, fontFamily: font.semibold, fontSize: 12.5, marginTop: 8 }}>{bankErr}</Text>
          ) : null}
          <View style={{ height: 16 }} />
        </>
      ) : (
        <>
          <Field label="Zitch tag or phone" value={identifier} onChangeText={changeIdentifier} placeholder="@username / 0801…" prefix={<ZIcon name="user" size={18} color={c.ink3} />} />
          <View style={{ marginTop: 8, marginBottom: 8 }}>
            {activeResolvedName ? <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 12.5 }}>✓ {activeResolvedName}</Text>
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
          <Pressable key={b.code} onPress={() => chooseBank(b)} style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, borderTopWidth: i === 0 ? 0 : 1, borderTopColor: c.line }}>
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
