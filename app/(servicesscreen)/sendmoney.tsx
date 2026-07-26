import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Alert, Pressable, ScrollView } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { getToken, hasTransactionPin, saveTransactionPin, hasOfferedBiometricPay, markBiometricPayOffered } from '@/lib/secureStore';
import { apiPost, apiJson, newIdempotencyKey } from '@/lib/api';
import { isBiometricAvailable, authenticate, biometricLabel, setBiometricEnabled } from '@/lib/biometrics';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, Naira } from '@/components/design/ui';
import { Label, Segmented, QuickAmounts, ConfirmSheet, BalanceHint, Monogram, BankLogo } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const AMOUNTS = [1000, 2000, 5000, 10000, 20000, 50000];
// Mirrors backend User.LARGE_TXN_THRESHOLD — drives the device biometric step-up.
const LARGE_TXN = 100000;
type Step = null | 'confirm' | 'pin';
type Bank = { code: string; name: string; color: string; logo?: string };
type Beneficiary = { id: number; name: string; account_number: string; bank_name: string; initials: string; color: string };
type BankMatch = { bank: string; bank_name: string; name: string };

const SendMoney = () => {
  const { c } = useTheme();
  const { balance, reload } = useWallet();
  const params = useLocalSearchParams<{ identifier?: string }>();

  const [, setToken] = useState('');
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
  const [bankQuery, setBankQuery] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);  // queued by the rail, not yet confirmed
  const [sentName, setSentName] = useState('');  // server-resolved holder (authoritative for the receipt)
  const [pinError, setPinError] = useState('');

  const loadBanks = () => {
    // Banks are a PUBLIC endpoint — load them regardless of session so the picker
    // is never empty. Routed through apiJson so it carries the app User-Agent
    // (keeps the request out of edge/bot 403 rules) and surfaces a failure
    // instead of silently leaving the list empty.
    apiJson('/api/transfers/banks/').then((res) => {
      if (res?.banks?.length) setBanks(res.banks);
      else notify('Couldn’t load banks', 'Check your connection and try again.', 'error');
    });
  };

  useEffect(() => {
    loadBanks();
    getToken().then((t) => {
      if (!t) return;
      setToken(t);
      apiPost('/api/transfers/beneficiaries/')
        .then((r) => r.json()).then((res) => res.beneficiaries && setBeneficiaries(res.beneficiaries)).catch(() => {});
    });
  }, []);

  useEffect(() => { setResolvedName(''); }, [identifier]);

  const amount = Number(amt || 0);
  // Bank mode: type a 10-digit account and we AUTO-DETECT the bank — the server
  // name-enquires across banks and returns the match, so the bank + holder name
  // fill in by themselves. The user can still tap the bank field to override
  // (which resolves at just that one bank).
  const [bankName, setBankName] = useState('');   // resolved account holder name
  const [resolvingBank, setResolvingBank] = useState(false);
  const [bankErr, setBankErr] = useState('');
  const [matches, setMatches] = useState<BankMatch[]>([]);  // shown when >1 bank matches

  const applyMatch = (m: BankMatch) => {
    setBank(banks.find((b) => b.code === m.bank) || { code: m.bank, name: m.bank_name, color: c.brand });
    setBankName(m.name);
    setMatches([]);
    setBankErr('');
  };

  // Auto-detect on a 10-digit account. Keyed on acct only, so the bank it sets
  // (or a manual pick) doesn't re-trigger it; editing the account re-detects.
  // Fast path: if the same account is already in prior beneficiaries (a past
  // successful payout wrote it there), fill the bank + holder name from that
  // row — no name-enquiry round-trip, no spinner. Makes "send again" feel instant.
  useEffect(() => {
    if (mode !== 'bank') return;
    setBank(null); setBankName(''); setBankErr(''); setMatches([]);
    if (acct.length !== 10) return;
    const ben = beneficiaries.find((b) => b.bank_name !== 'Zitch' && b.account_number === acct);
    const known = ben && banks.find((x) => x.name === ben.bank_name);
    if (ben && known) {
      setBank(known);
      setBankName(ben.name);
      return;
    }
    let cancelled = false;
    setResolvingBank(true);
    const t = setTimeout(async () => {
      try {
        const res = await apiJson('/api/transfers/resolve/', { account_number: acct }); // no bank -> auto-detect
        if (cancelled) return;
        // `mock` => the server has no live name-enquiry rail, so the match is a
        // placeholder, not a real detection. Don't auto-fill it as a verified
        // bank/holder (that's what looked like "mis-detection") — ask the user to
        // pick the bank instead (the picker is searchable).
        if (res.success && res.mock) setBankErr("Couldn't auto-detect — tap “Bank” to choose it.");
        else if (res.success && res.matches?.length === 1) applyMatch(res.matches[0]);
        else if (res.success && res.matches?.length) setMatches(res.matches);
        else setBankErr(res.message || "Couldn't detect the bank — tap “Bank” to pick it.");
      } catch {
        if (!cancelled) setBankErr("Couldn't verify this account. Please try again.");
      } finally {
        if (!cancelled) setResolvingBank(false);
      }
    }, 500);
    return () => { cancelled = true; clearTimeout(t); };
  }, [acct, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  // Manual override: resolve at the specific bank the user picks from the sheet.
  const chooseBank = async (b: Bank) => {
    setBank(b); setBankSheet(false); setBankQuery(''); setMatches([]); setBankName(''); setBankErr('');
    if (acct.length !== 10) return;
    setResolvingBank(true);
    try {
      const res = await apiJson('/api/transfers/resolve/', { account_number: acct, bank: b.code });
      // `mock` => no live name-enquiry rail; don't present the stub name as a
      // verified holder (parity with the auto-detect path).
      if (res.success && res.mock) setBankErr('Account name check is unavailable right now — double-check the number before sending.');
      else if (res.success && res.name) setBankName(res.name);
      else setBankErr(res.message || "Couldn't verify this account at that bank.");
    } catch { setBankErr("Couldn't verify this account. Please try again."); }
    finally { setResolvingBank(false); }
  };

  const acctReady = mode === 'bank' ? acct.length === 10 && !!bank : !!resolvedName;
  const recipientName = picked ? picked.name : mode === 'bank' ? bankName : resolvedName;
  const valid = (!!picked || acctReady) && amount >= 50 && amount <= balance;

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

  // A change to ANY transfer detail is a new spend — drop the retained key so
  // the next attempt mints a fresh one. We deliberately KEEP the key across a
  // byte-identical retry (so a timed-out-but-delivered attempt replays
  // server-side instead of double-debiting); but if the user edits the amount,
  // account, bank, recipient, mode or note, reusing that key would replay the
  // PRIOR transfer and render a false success for the edited one.
  useEffect(() => { idemKey.current = ''; }, [amt, acct, bank?.code, mode, picked?.id, identifier, note]);

  // After a PIN-approved transfer, offer (once) to approve future payments with
  // Face ID / fingerprint instead of the PIN. The money PIN is cached only after
  // a fresh biometric scan, so the PinPad's biometric shortcut then appears. This
  // is the only in-flow path that enables "pay with biometrics".
  const offerBiometricPay = async (pin: string) => {
    try {
      if (!(await isBiometricAvailable())) return;        // no hardware / not enrolled
      if (await hasTransactionPin()) return;              // already set up
      if (await hasOfferedBiometricPay()) return;         // asked once already — don't nag
      await markBiometricPayOffered();
      const kind = await biometricLabel();
      const label = kind === 'face' ? 'Face ID' : kind === 'fingerprint' ? 'fingerprint' : 'biometrics';
      Alert.alert(
        `Approve with ${label}?`,
        `Use your ${label} to approve payments instead of typing your PIN every time.`,
        [
          { text: 'Not now', style: 'cancel' },
          {
            text: 'Enable',
            onPress: async () => {
              // biometricOnly: tie the cached PIN to the owner's biometric, not the
              // device passcode.
              const okScan = await authenticate(`Enable ${label} approval`, true);
              if (!okScan) return;
              await setBiometricEnabled(true);
              await saveTransactionPin(pin);
              notify('Enabled', `You can now approve payments with ${label}.`);
            },
          },
        ],
      );
    } catch { /* offering biometrics must never block the receipt */ }
  };

  const send = async (pin: string, viaBiometric = false) => {
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      // Defense-in-depth: a device biometric step-up for large transfers, on top
      // of the transaction PIN and the server-side face_verified gate. If the
      // device has no enrolled biometrics, the PIN + server checks still apply.
      // Skip it when this approval was ITSELF a biometric scan (don't prompt twice).
      if (!viaBiometric && amount >= LARGE_TXN && (await isBiometricAvailable())) {
        // biometricOnly: don't let the device passcode stand in for the owner's
        // biometric on a large-transfer authorization.
        const okScan = await authenticate(`Authorize ${money(amount)} transfer`, true);
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

      // `pending` = the rail accepted and QUEUED the payout (money already left
      // the wallet); it carries no `success` field. Treat it as a completed
      // attempt — NOT a failure — so it never falls into the else branch that
      // would mint a fresh key and let a retry debit a second time.
      if (res.success || res.pending || res.duplicate) {
        // `success`   = transfer confirmed sent
        // `pending`   = rail queued it (money already left wallet, webhook confirms later)
        // `duplicate` = server replayed a prior completed attempt (idempotent replay)
        //               — the transfer DID go through; show the receipt, not an error.
        idemKey.current = '';
        setPending(!res.success && !!res.pending && !res.duplicate);
        if (res.name) setSentName(String(res.name));  // show who the bank actually resolved to
        setStep(null);   // close the PIN sheet FIRST…
        reload();
        // …then show the receipt once the sheet has animated out. Switching to the
        // receipt while the modal was still visible left the PIN sheet lingering on
        // screen on Android. Offer biometric pay over the receipt.
        setTimeout(() => { setDone(true); offerBiometricPay(pin); }, 300);
      }
      else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') { setPinError(res.message || 'Incorrect PIN'); }
      else {
        // Only a definitive backend rejection mints a new key. On a connectivity
        // failure (`offline`) the request may have been delivered, so the key is
        // KEPT — a retry then replays server-side instead of debiting twice.
        if (!res.offline) idemKey.current = '';
        // Guard against the server echoing a success-sounding message (e.g.
        // "Already processed" / "success") inside an error path — show a
        // generic fallback so the dialog never reads "Error / success".
        const errMsg = (res.message && !/^success$/i.test(res.message.trim()))
          ? res.message
          : 'Transfer could not be completed. Please try again.';
        notify('Error', errMsg);
        setStep(null);
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.'); setStep(null);
    } finally { setBusy(false); }
  };

  if (done) {
    const acctShown = picked ? picked.account_number : mode === 'bank' ? acct : identifier;
    const bankShown = picked ? picked.bank_name : mode === 'bank' ? bank?.name || 'Bank' : 'Zitch';
    // Prefer the name the bank actually resolved to (returned by the send), so the
    // receipt reflects who was really paid — not a possibly-stale displayed name.
    const finalName = sentName || recipientName;
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Transfer processing' : 'Money sent'}
          message={pending
            ? `${money(amount)} to ${finalName || 'recipient'} is processing and will be confirmed shortly.`
            : `${money(amount)} sent to ${finalName || 'recipient'}.`}
          rows={[['Recipient', finalName || '—'], ['Account', acctShown], ['Bank', bankShown], ...(note ? ([['Note', note]] as [string, string][]) : []), ['Fee', '₦0'], ['Total', money(amount), true]]}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  const filteredBens = beneficiaries.filter((b) => (b.name + ' ' + b.account_number).toLowerCase().includes(query.toLowerCase()));
  const filteredBanks = banks.filter((b) => b.name.toLowerCase().includes(bankQuery.trim().toLowerCase()));
  // "Sent before" suggestions: as the user types 4+ digits, surface up to 3
  // prior bank beneficiaries whose account number starts with what they've
  // typed. Tap fills the field, which triggers the fast-path effect above to
  // populate bank + holder — no scroll to the saved-beneficiaries row needed.
  const acctSuggestions = mode === 'bank' && !picked && acct.length >= 4 && acct.length < 10
    ? beneficiaries.filter((b) => b.bank_name !== 'Zitch' && b.account_number.startsWith(acct)).slice(0, 3)
    : [];

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
          {acctSuggestions.length > 0 && (
            <View style={{ marginTop: 8 }}>
              <Text style={{ color: c.ink3, fontFamily: font.regular, fontSize: 12, marginBottom: 4 }}>Sent before</Text>
              {acctSuggestions.map((b) => (
                <Pressable key={b.id} onPress={() => setAcct(b.account_number)} style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8 }}>
                  <Monogram text={b.initials} color={b.color} size={32} />
                  <View style={{ flex: 1 }}>
                    <Text style={{ fontFamily: font.semibold, color: c.ink1, fontSize: 13.5 }}>{b.name}</Text>
                    <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 12 }}>{b.account_number} · {b.bank_name}</Text>
                  </View>
                </Pressable>
              ))}
            </View>
          )}
          <View style={{ height: 14 }} />
          <Pressable onPress={() => setBankSheet(true)}>
            {/* While auto-detecting, the field shows the small branded Zitch
                loader in place of the text (no "Detecting…" copy). */}
            <Field
              label="Bank"
              value={bank?.name || ''}
              editable={false}
              loading={resolvingBank}
              placeholder="Auto-detected from account — or tap to choose"
              prefix={bank ? <BankLogo name={bank.name} color={bank.color} logo={bank.logo} size={26} /> : <ZIcon name="bank" size={18} color={c.ink3} />}
              suffix={<ZIcon name="down" size={16} color={c.ink3} />}
              pointerEvents="none"
            />
          </Pressable>
          {resolvingBank ? null : matches.length > 1 ? (
            <View style={{ marginTop: 8 }}>
              <Text style={{ color: c.ink3, fontFamily: font.regular, fontSize: 12, marginBottom: 4 }}>Found at more than one bank — pick the right one:</Text>
              {matches.map((m) => (
                <Pressable key={m.bank} onPress={() => applyMatch(m)} style={{ paddingVertical: 7 }}>
                  <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 13 }}>{m.bank_name}</Text>
                  <Text style={{ color: c.ink2, fontFamily: font.regular, fontSize: 12 }}>{m.name}</Text>
                </Pressable>
              ))}
            </View>
          ) : bankName ? (
            <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 12.5, marginTop: 8 }}>✓ {bankName}</Text>
          ) : bankErr ? (
            <Text style={{ color: c.red, fontFamily: font.semibold, fontSize: 12.5, marginTop: 8 }}>{bankErr}</Text>
          ) : null}
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

      {/* Amount: the field leads, with the quick presets as a slim pill row of
          suggestions underneath (was a dominant 2×3 grid above the field). */}
      <Label>Amount</Label>
      <Field value={amt} onChangeText={(v) => setAmt(v.replace(/\D/g, ''))} keyboardType="number-pad" placeholder="Enter amount" prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />} />
      <View style={{ height: 10 }} />
      <QuickAmounts amounts={AMOUNTS} value={amt} onPick={setAmt} />
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
      <Sheet open={bankSheet} onClose={() => { setBankSheet(false); setBankQuery(''); }} title="Select bank">
        <Field value={bankQuery} onChangeText={setBankQuery} placeholder="Search bank" prefix={<ZIcon name="search" size={18} color={c.ink3} />} />
        <View style={{ height: 6 }} />
        {filteredBanks.length === 0 ? (
          <Text style={{ color: c.ink3, fontFamily: font.regular, paddingVertical: 16, textAlign: 'center' }}>No bank matches “{bankQuery.trim()}”.</Text>
        ) : (
          filteredBanks.map((b, i) => (
            <Pressable key={b.code} onPress={() => chooseBank(b)} style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, borderTopWidth: i === 0 ? 0 : 1, borderTopColor: c.line }}>
              <BankLogo name={b.name} color={b.color} logo={b.logo} size={36} />
              <Text style={{ flex: 1, fontFamily: font.semibold, color: c.ink1 }}>{b.name}</Text>
              {bank?.code === b.code && <ZIcon name="check" size={18} color={c.brand} />}
            </Pressable>
          ))
        )}
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

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title={busy ? 'Processing transfer' : 'Enter your PIN'}>
        {/* No negative top margin — inside the sheet's ScrollView it clips the
            subtitle's top edge under the title. */}
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, fontFamily: font.regular }}>
          {busy ? 'Sending your money — hold on a moment…' : `Confirm transfer of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p, bio) => send(p, !!bio)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default SendMoney;

