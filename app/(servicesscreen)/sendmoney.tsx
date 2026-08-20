import React, { useEffect, useRef, useState } from 'react';
import { View, Text, TextInput, Alert, Pressable, ScrollView } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { router, useLocalSearchParams } from 'expo-router';
import { getToken, hasTransactionPin, saveTransactionPin, hasOfferedBiometricPay, markBiometricPayOffered } from '@/lib/secureStore';
import { apiPost, apiJson, newIdempotencyKey } from '@/lib/api';
import { isBiometricAvailable, authenticate, biometricLabel, setBiometricTxnEnabled } from '@/lib/biometrics';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Card, Field, Btn, Sheet, PinPad, money, NText } from '@/components/design/ui';
import { Label, Segmented, QuickAmounts, ConfirmSheet, BalanceHint, Monogram, BankLogo, AmountField } from '@/components/design/flowkit';
import { LoadingMark } from '@/components/design/Loading';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font, radius, iconTint } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const AMOUNTS = [1000, 2000, 5000, 10000, 20000, 50000];
// Mirrors backend User.LARGE_TXN_THRESHOLD — drives the device biometric step-up.
const LARGE_TXN = 100000;
// Display-only mirror of the `amount >= 50` gate in `valid` below (backend
// common.http.MIN_TRANSFER). Shown in the info strip so the floor is stated
// before the user types, not after the button refuses to enable.
const MIN_AMOUNT = 50;
type Step = null | 'confirm' | 'pin';
type Bank = { code: string; name: string; aliases?: string[]; color: string; logo?: string };
type Beneficiary = {
  id: number; name: string; account_number: string; bank_name: string;
  initials: string; color: string;
  // Added alongside the six above, which shipped builds already read. `name`
  // stays the bank's holder name — it is what the server re-confirms against a
  // fresh name enquiry before money moves — and `display_name` is what a person
  // should be called on screen.
  nickname?: string; display_name?: string; saved?: boolean; transfer_count?: number; frequent?: boolean;
};
type BankMatch = { bank: string; bank_name: string; name: string };

// Banner strip content. This app has NO offers/promotions endpoint or constant,
// so the strip carries one factual line about the transfer service itself
// rather than an invented discount. Appending real banners here is enough —
// the pager and the dot indicators below already handle more than one.
const BANNERS: { title: string; body: string }[] = [
  { title: 'Any bank, any time', body: 'Send to any Nigerian bank account, 24/7.' },
];

// One tab, deliberately. The list mixes saved people and recents, because a
// saved recipient IS someone you paid recently and splitting them would empty
// the tab a customer is standing on the moment they star their only row.
// Saved people sort to the top and carry a filled star instead.
const BEN_TABS: { v: string; label: string }[] = [{ v: 'recents', label: 'Recents' }];

// ---- Local presentation helpers (kept in this file on purpose: they are this
// screen's reference layout, not design-system components) ----

// A borderless input/select row with a hairline rule under it — the card's
// fields read as ruled lines rather than a stack of boxes.
const URow = ({ children, onPress, label }: { children: React.ReactNode; onPress?: () => void; label?: string }) => {
  const { c } = useTheme();
  const inner = (
    <View
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: 10,
        minHeight: 54,
        borderBottomWidth: 1,
        borderBottomColor: c.line,
      }}
    >
      {children}
    </View>
  );
  if (!onPress) return inner;
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={({ pressed }) => ({ opacity: pressed ? 0.7 : 1 })}>
      {inner}
    </Pressable>
  );
};

const SendMoney = () => {
  const { c, theme } = useTheme();
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
  // The ledger reference the server minted for this transaction — shown on the
  // receipt and carried into the saved/shared file, so a support ticket can name it.
  const [txnRef, setTxnRef] = useState('');
  const [pending, setPending] = useState(false);  // queued by the rail, not yet confirmed
  const [sentName, setSentName] = useState('');  // server-resolved holder (authoritative for the receipt)
  const [sentNarration, setSentNarration] = useState('');
  const [pinError, setPinError] = useState('');
  const idemKey = useRef('');  // stable across retries of one transfer attempt

  // ---- Presentation-only state (layout, not flow) ----
  const [bannerW, setBannerW] = useState(0);     // measured page width for the pager
  const [bannerPage, setBannerPage] = useState(0);
  const [benTab, setBenTab] = useState(BEN_TABS[0].v);
  const [benSearch, setBenSearch] = useState(false);  // search field revealed by the tab-row icon
  const [benAll, setBenAll] = useState(false);        // "View All" expands past the inline cap
  const [renaming, setRenaming] = useState<Beneficiary | null>(null);
  const [nickDraft, setNickDraft] = useState('');
  const [savedOnReceipt, setSavedOnReceipt] = useState(false);
  const [sentBeneficiaryId, setSentBeneficiaryId] = useState<number | null>(null);

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
        .then((r) => r.json()).then((res) => {
          const saved = Array.isArray(res.beneficiaries) ? res.beneficiaries : [];
          const frequent = Array.isArray(res.frequent_recipients) ? res.frequent_recipients : [];
          const seen = new Set<number>();
          setBeneficiaries([...saved, ...frequent].filter((b) => !seen.has(b.id) && seen.add(b.id)));
        }).catch(() => {});
    });
  }, []);

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
    let cancelled = false;
    let detectTimer: ReturnType<typeof setTimeout> | undefined;
    const setupTimer = setTimeout(() => {
      setBank(null); setBankName(''); setBankErr(''); setMatches([]);
      if (acct.length !== 10) return;
      const ben = beneficiaries.find((b) => b.bank_name !== 'Zitch' && b.account_number === acct);
      const known = ben && banks.find((x) => x.name === ben.bank_name);
      if (ben && known) {
        setBank(known);
        setBankName(ben.name);
        return;
      }
      setResolvingBank(true);
      detectTimer = setTimeout(async () => {
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
    }, 0);
    return () => {
      cancelled = true;
      clearTimeout(setupTimer);
      if (detectTimer) clearTimeout(detectTimer);
    };
  }, [acct, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  // Manual override: resolve at the specific bank the user picks from the sheet.
  const chooseBank = async (b: Bank) => {
    idemKey.current = '';
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

  // Tapping a saved recipient. For a bank recipient in bank mode this just fills
  // the account number — the auto-detect effect above then supplies the bank and
  // holder name (its beneficiary fast path), exactly as the "Sent before"
  // suggestions do. A Zitch recipient (or zitch mode) has no 10-digit account to
  // fill, so it takes the existing `picked` path.
  const tapBeneficiary = (b: Beneficiary) => {
    idemKey.current = '';
    if (mode === 'bank' && b.bank_name !== 'Zitch') setAcct(b.account_number);
    else setPicked(b);
  };

  // --- Keeping a recipient ---------------------------------------------------
  // Every row here is already a RECENT: the server wrote it when a payout to that
  // account settled. Saving is the customer's own decision on top, and it is what
  // makes the recipient payable by name in the WhatsApp channel. There is no
  // "unsave": the only way down is Remove, which is destructive and says so.

  const applyBen = (row: Beneficiary) =>
    setBeneficiaries((prev) => prev.map((b) => (b.id === row.id ? row : b)));

  const saveBen = async (b: Beneficiary, nickname?: string) => {
    const res = await apiJson('/api/transfers/beneficiaries/save/',
                              { beneficiary_id: b.id, nickname: nickname || '' });
    if (res.success && res.beneficiary) { applyBen(res.beneficiary); return true; }
    notify('Error', res.message || "Couldn't save that recipient.");
    return false;
  };

  const renameBen = async (b: Beneficiary, nickname: string) => {
    const res = await apiJson('/api/transfers/beneficiaries/rename/',
                              { beneficiary_id: b.id, nickname });
    if (res.success && res.beneficiary) applyBen(res.beneficiary);
    else notify('Error', res.message || "Couldn't rename that recipient.");
  };

  const removeBen = (b: Beneficiary) => {
    const label = b.display_name || b.name;
    Alert.alert(
      `Remove ${label}?`,
      'They come off this list and stop filling in automatically when you type their account number.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: async () => {
            const res = await apiJson('/api/transfers/beneficiaries/delete/', { beneficiary_id: b.id });
            if (res.success) setBeneficiaries((prev) => prev.filter((x) => x.id !== b.id));
            else notify('Error', res.message || "Couldn't remove that recipient.");
          },
        },
      ],
    );
  };

  // Long-press on a row. Kept as the platform's own action sheet rather than a
  // bespoke menu: it is the gesture people already try on a list like this, and
  // the destructive option gets the system's own styling for one.
  const benActions = (b: Beneficiary) => {
    const label = b.display_name || b.name;
    Alert.alert(label, `${b.account_number} ${b.bank_name}`, [
      { text: 'Cancel', style: 'cancel' },
      { text: b.nickname ? 'Change name' : 'Give them a name', onPress: () => { setNickDraft(b.nickname || ''); setRenaming(b); } },
      ...(b.saved ? [] : [{ text: 'Save', onPress: () => { saveBen(b); } }]),
      { text: 'Remove', style: 'destructive' as const, onPress: () => removeBen(b) },
    ]);
  };

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
              // SecureStore performs the single native authentication while it
              // writes the PIN behind the keychain biometric ACL.
              await saveTransactionPin(pin);
              await setBiometricTxnEnabled(true);
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
        if (res.narration) setSentNarration(String(res.narration));
        setTxnRef(String(res.reference || ''));
        // The row the server just wrote for this recipient, so the receipt can
        // offer to keep them without posting an account number back and paying
        // for a second name enquiry to identify someone we already know.
        setSentBeneficiaryId(Number(res.beneficiary_id) || null);
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
    const narration = sentNarration || note.trim() || `Transfer to ${finalName || 'recipient'}`;
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Transfer processing' : 'Money sent'}
          message={pending
            ? `${money(amount)} to ${finalName || 'recipient'} is processing and will be confirmed shortly.`
            : `${money(amount)} sent to ${finalName || 'recipient'}.`}
          rows={[['Recipient', finalName || '—'], ['Account', acctShown], ['Bank', bankShown], ['Narration', narration], ['Fee', '₦0'], ['Total', money(amount), true]]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          onDone={() => router.replace('/home')}
          footer={sentBeneficiaryId && !savedOnReceipt ? (
            <Pressable
              onPress={async () => {
                const row = { id: sentBeneficiaryId, name: finalName || '', account_number: acctShown,
                              bank_name: bankShown, initials: '', color: c.brand } as Beneficiary;
                if (await saveBen(row)) setSavedOnReceipt(true);
              }}
              accessibilityRole="button"
              accessibilityLabel={`Save ${finalName || 'this recipient'}`}
              style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
                       paddingVertical: 14, borderRadius: radius.md, borderWidth: 1, borderColor: c.line }}
            >
              <ZIcon name="plus" size={16} color={c.brand} stroke={2.2} />
              <Text style={{ fontSize: 14, fontFamily: font.semibold, color: c.brand }}>
                Save {finalName || 'this recipient'}
              </Text>
            </Pressable>
          ) : savedOnReceipt ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 14 }}>
              <ZIcon name="check" size={16} color={c.brand} stroke={2.4} />
              <Text style={{ fontSize: 14, fontFamily: font.medium, color: c.ink3 }}>Saved to your recipients</Text>
            </View>
          ) : null}
        />
      </Screen>
    );
  }

  // Saved people first, then the rest in server order (newest paid first).
  // Searching covers the nickname too: someone who saved an account as "Mum"
  // will look for Mum, not for the holder name printed on the bank's records.
  const filteredBens = beneficiaries
    .filter((b) => b.saved)
    .filter((b) => (b.name + ' ' + (b.nickname || '') + ' ' + b.account_number)
      .toLowerCase().includes(query.toLowerCase()))
    .slice()
    .sort((x, y) => Number(!!y.saved) - Number(!!x.saved));
  const filteredBanks = banks.filter((b) => (b.name + ' ' + (b.aliases || []).join(' ')).toLowerCase().includes(bankQuery.trim().toLowerCase()));
  // "Sent before" suggestions: as the user types 4+ digits, surface up to 3
  // prior bank beneficiaries whose account number starts with what they've
  // typed. Tap fills the field, which triggers the fast-path effect above to
  // populate bank + holder — no scroll to the saved-beneficiaries row needed.
  const acctSuggestions = mode === 'bank' && !picked && acct.length >= 4 && acct.length < 10
    ? beneficiaries.filter((b) => b.bank_name !== 'Zitch' && (b.saved || b.frequent || (b.transfer_count || 0) >= 3) && b.account_number.startsWith(acct)).slice(0, 3)
    : [];
  // Inline list is capped at 3 rows; "View All" swaps in the rest in place.
  const shownBens = benAll ? filteredBens : filteredBens.slice(0, 3);

  return (
    <Screen>
      <Header
        title="Transfer to Bank Account"
        onBack={() => router.back()}
        right={(
          <Pressable onPress={() => router.push('/history')} hitSlop={10}>
            <Text style={{ fontSize: 14.5, fontFamily: font.bold, color: c.brand }}>History</Text>
          </Pressable>
        )}
      />

      {/* Banner strip — paged, with dot indicators once there is more than one
          card to page between. */}
      <View onLayout={(e) => setBannerW(Math.round(e.nativeEvent.layout.width))}>
        {bannerW > 0 && (
          <ScrollView
            horizontal
            pagingEnabled
            showsHorizontalScrollIndicator={false}
            onMomentumScrollEnd={(e) => setBannerPage(Math.round(e.nativeEvent.contentOffset.x / bannerW))}
          >
            {BANNERS.map((b) => (
              <View key={b.title} style={{ width: bannerW }}>
                <LinearGradient
                  colors={c.heroGradient}
                  start={{ x: 0, y: 0 }}
                  end={{ x: 1, y: 1 }}
                  style={{ borderRadius: radius.lg, paddingHorizontal: 20, paddingVertical: 18, minHeight: 94, justifyContent: 'center' }}
                >
                  <Text style={{ color: '#fff', fontFamily: font.extrabold, fontSize: 16.5, letterSpacing: -0.2 }}>{b.title}</Text>
                  <Text style={{ color: '#fff', opacity: 0.86, fontFamily: font.regular, fontSize: 12.5, marginTop: 5 }}>{b.body}</Text>
                </LinearGradient>
              </View>
            ))}
          </ScrollView>
        )}
        {BANNERS.length > 1 && (
          <View style={{ flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 6, marginTop: 10 }}>
            {BANNERS.map((b, i) => (
              <View
                key={b.title}
                style={{ width: i === bannerPage ? 16 : 6, height: 6, borderRadius: 3, backgroundColor: i === bannerPage ? c.brand : c.line }}
              />
            ))}
          </View>
        )}
      </View>

      {/* What this screen already knows to be true about a transfer: the fee it
          prints on every confirm sheet and receipt, and the minimum the
          Continue button enforces. Nothing here is a promise the flow can't keep. */}
      <View
        style={{
          flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 14,
          paddingVertical: 9, paddingHorizontal: 14, borderRadius: radius.pill,
          backgroundColor: iconTint(c.brand, theme === 'dark'),
        }}
      >
        <ZIcon name="spark" size={15} color={c.brand} stroke={2.2} />
        <NText style={{ flex: 1, fontSize: 12.5, fontFamily: font.semibold, color: c.ink2 }}>
          {`No transfer fee · Minimum transfer ${money(MIN_AMOUNT)}`}
        </NText>
      </View>

      <View style={{ height: 18 }} />

      <Segmented
        options={[{ v: 'bank', label: 'To Bank' }, { v: 'zitch', label: 'To Zitch' }]}
        value={mode}
        onChange={(v) => { idemKey.current = ''; setMode(v as any); setPicked(null); setAcct(''); setBank(null); setIdentifier(''); setResolvedName(''); }}
      />

      {/* Recipient Account — the account row, the bank row, the amount and the
          Continue button live in one card, as ruled lines rather than boxes. */}
      <Card pad={18}>
        <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1, marginBottom: 4 }}>Recipient Account</Text>

        {picked ? (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: c.line }}>
            <Monogram text={picked.initials} color={picked.color} />
            <View style={{ flex: 1 }}>
              <Text style={{ fontFamily: font.bold, color: c.ink1 }}>{picked.name}</Text>
              <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>{picked.account_number} · {picked.bank_name}</Text>
            </View>
            <Pressable onPress={() => { idemKey.current = ''; setPicked(null); }} hitSlop={8}>
              <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.brand }}>Change</Text>
            </Pressable>
          </View>
        ) : mode === 'bank' ? (
          <>
            <URow>
              <ZIcon name="bank" size={18} color={c.ink3} />
              <TextInput
                value={acct}
                onChangeText={(v) => { idemKey.current = ''; setAcct(v.replace(/\D/g, '').slice(0, 10)); }}
                keyboardType="number-pad"
                placeholder="Enter 10 digits Account Number"
                placeholderTextColor={c.ink3}
                accessibilityLabel="Account number"
                style={{ flex: 1, fontSize: 16, color: c.ink1, fontFamily: font.medium, paddingVertical: 0 }}
              />
            </URow>

            {acctSuggestions.length > 0 && (
              <View style={{ paddingTop: 10 }}>
                <Text style={{ color: c.ink3, fontFamily: font.regular, fontSize: 12, marginBottom: 4 }}>Sent before</Text>
                {acctSuggestions.map((b) => (
                  <Pressable key={b.id} onPress={() => { idemKey.current = ''; setAcct(b.account_number); }} style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8 }}>
                    <Monogram text={b.initials} color={b.color} size={32} />
                    <View style={{ flex: 1 }}>
                      <Text style={{ fontFamily: font.semibold, color: c.ink1, fontSize: 13.5 }}>{b.name}</Text>
                      <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 12 }}>{b.account_number} · {b.bank_name}</Text>
                    </View>
                  </Pressable>
                ))}
              </View>
            )}

            {/* While auto-detecting, the row shows the small branded Zitch loader
                in place of the bank name (no "Detecting…" copy). */}
            <URow onPress={() => setBankSheet(true)} label="Select bank">
              {bank
                ? <BankLogo name={bank.name} color={bank.color} logo={bank.logo} size={26} />
                : <ZIcon name="bank" size={18} color={c.ink3} />}
              {resolvingBank ? (
                <View style={{ flex: 1 }} accessible accessibilityRole="progressbar" accessibilityLabel="Loading">
                  <LoadingMark size={22} />
                </View>
              ) : (
                <Text
                  numberOfLines={1}
                  style={{ flex: 1, fontSize: 16, fontFamily: bank ? font.semibold : font.regular, color: bank ? c.ink1 : c.ink3 }}
                >
                  {bank?.name || 'Select Bank'}
                </Text>
              )}
              <ZIcon name="right" size={16} color={c.ink3} />
            </URow>

            {resolvingBank ? null : matches.length > 1 ? (
              <View style={{ marginTop: 10 }}>
                <Text style={{ color: c.ink3, fontFamily: font.regular, fontSize: 12, marginBottom: 4 }}>Found at more than one bank — pick the right one:</Text>
                {matches.map((m) => (
                  <Pressable key={m.bank} onPress={() => applyMatch(m)} style={{ paddingVertical: 7 }}>
                    <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 13 }}>{m.bank_name}</Text>
                    <Text style={{ color: c.ink2, fontFamily: font.regular, fontSize: 12 }}>{m.name}</Text>
                  </Pressable>
                ))}
              </View>
            ) : bankName ? (
              <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 12.5, marginTop: 10 }}>✓ {bankName}</Text>
            ) : bankErr ? (
              <Text style={{ color: c.red, fontFamily: font.semibold, fontSize: 12.5, marginTop: 10 }}>{bankErr}</Text>
            ) : null}
          </>
        ) : (
          <>
            <URow>
              <ZIcon name="user" size={18} color={c.ink3} />
              <TextInput
                value={identifier}
                onChangeText={(v) => { idemKey.current = ''; setResolvedName(''); setIdentifier(v.replace(/[^\d@a-zA-Z]/g, '').slice(0, 15)); }}
                placeholder="@username / 0801…"
                placeholderTextColor={c.ink3}
                accessibilityLabel="Zitch tag or phone"
                autoCapitalize="none"
                autoCorrect={false}
                style={{ flex: 1, fontSize: 16, color: c.ink1, fontFamily: font.medium, paddingVertical: 0 }}
              />
            </URow>
            <View style={{ marginTop: 10 }}>
              {resolvedName ? <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 12.5 }}>✓ {resolvedName}</Text>
                : <Btn label="Confirm recipient" variant="outline" size="sm" full={false} onPress={resolveZitch} disabled={resolving} />}
            </View>
          </>
        )}

        {/* Amount: the field leads, with the quick presets as a slim pill row of
            suggestions underneath (was a dominant 2×3 grid above the field). */}
        <View style={{ height: 6 }} />
        <Label>Amount</Label>
        <AmountField value={amt} onChangeText={(v) => { idemKey.current = ''; setAmt(v); }} />
        <View style={{ height: 10 }} />
        <QuickAmounts amounts={AMOUNTS} value={amt} onPick={(v) => { idemKey.current = ''; setAmt(v); }} />
        <BalanceHint amount={amount} balance={balance} />

        <Field label="Narration (optional)" value={note} onChangeText={(v) => { idemKey.current = ''; setNote(v); }} placeholder="What's it for?" />
        <View style={{ height: 20 }} />

        <Btn label="Continue" disabled={!valid} onPress={() => setStep('confirm')} />
      </Card>

      <View style={{ height: 14 }} />

      <Card pad={14} onPress={() => router.push('/safetytips')}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <View style={{ width: 40, height: 40, borderRadius: 13, backgroundColor: iconTint(c.brand, theme === 'dark'), alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="shield" size={20} color={c.brand} />
          </View>
          <Text style={{ flex: 1, fontSize: 14.5, fontFamily: font.semibold, color: c.ink1 }}>Transfer tips & safety</Text>
          <ZIcon name="right" size={16} color={c.ink3} />
        </View>
      </Card>

      {/* Saved recipients — the real /api/transfers/beneficiaries/ list (newest
          first), so "Recents" is the accounts this user has actually paid. */}
      {!picked && beneficiaries.length > 0 && (
        <>
          <View style={{ height: 14 }} />
          <Card pad={18}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 20, borderBottomWidth: 1, borderBottomColor: c.line, marginBottom: 4 }}>
              {BEN_TABS.map((t) => {
                const on = benTab === t.v;
                return (
                  <Pressable
                    key={t.v}
                    onPress={() => setBenTab(t.v)}
                    accessibilityRole="tab"
                    accessibilityState={{ selected: on }}
                    style={{ paddingBottom: 10, marginBottom: -1, borderBottomWidth: 2, borderBottomColor: on ? c.brand : 'transparent' }}
                  >
                    <Text style={{ fontSize: 14.5, fontFamily: font.bold, color: on ? c.brand : c.ink3 }}>{t.label}</Text>
                  </Pressable>
                );
              })}
              <View style={{ flex: 1 }} />
              <Pressable
                onPress={() => { const next = !benSearch; setBenSearch(next); if (!next) setQuery(''); }}
                hitSlop={10}
                accessibilityRole="button"
                accessibilityLabel={benSearch ? 'Hide search' : 'Search saved recipients'}
                style={{ paddingBottom: 10 }}
              >
                <ZIcon name="search" size={18} color={benSearch ? c.brand : c.ink3} />
              </Pressable>
            </View>

            {benSearch && (
              <URow>
                <ZIcon name="search" size={16} color={c.ink3} />
                <TextInput
                  value={query}
                  onChangeText={setQuery}
                  placeholder="Search by name or account"
                  placeholderTextColor={c.ink3}
                  accessibilityLabel="Search saved recipients"
                  autoCapitalize="none"
                  autoCorrect={false}
                  autoFocus
                  style={{ flex: 1, fontSize: 15, color: c.ink1, fontFamily: font.medium, paddingVertical: 0 }}
                />
              </URow>
            )}

            {filteredBens.length === 0 ? (
              <Text style={{ fontSize: 13, color: c.ink3, paddingVertical: 14, fontFamily: font.regular }}>No matching beneficiary</Text>
            ) : (
              shownBens.map((b, i) => (
                <Pressable
                  key={b.id}
                  onPress={() => tapBeneficiary(b)}
                  onLongPress={() => benActions(b)}
                  accessibilityRole="button"
                  accessibilityLabel={`${b.display_name || b.name}, ${b.account_number} ${b.bank_name}${b.saved ? ', saved' : ''}`}
                  accessibilityHint="Long press to rename or remove"
                  style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, borderTopWidth: i === 0 ? 0 : 1, borderTopColor: c.line }}
                >
                  {/* Saving is one tap, and it is the only state this star sets.
                      Coming back off the list is Remove, on the long-press menu,
                      because it deletes the row rather than un-ticking it. */}
                  <Pressable
                    onPress={() => (b.saved ? benActions(b) : saveBen(b))}
                    hitSlop={10}
                    accessibilityRole="button"
                    accessibilityLabel={b.saved ? `${b.display_name || b.name} is saved` : `Save ${b.name}`}
                  >
                    <ZIcon name={b.saved ? 'check' : 'plus'} size={17} color={b.saved ? c.brand : c.ink3} stroke={b.saved ? 2.4 : 1.8} />
                  </Pressable>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text numberOfLines={1} style={{ fontSize: 14.5, fontFamily: font.semibold, color: c.ink1 }}>{b.display_name || b.name}</Text>
                    <Text numberOfLines={1} style={{ fontSize: 12.5, fontFamily: font.regular, color: c.ink3, marginTop: 2 }}>{b.account_number} {b.bank_name}</Text>
                  </View>
                  <View style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: iconTint(b.color, theme === 'dark'), alignItems: 'center', justifyContent: 'center' }}>
                    <Text style={{ fontSize: 13, fontFamily: font.extrabold, color: b.color }}>{b.initials}</Text>
                  </View>
                </Pressable>
              ))
            )}

            {filteredBens.length > 3 && (
              <Pressable
                onPress={() => setBenAll((v) => !v)}
                accessibilityRole="button"
                style={{ alignSelf: 'center', marginTop: 14, paddingVertical: 9, paddingHorizontal: 20, borderRadius: radius.pill, backgroundColor: c.surface3 }}
              >
                <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.ink2 }}>{benAll ? 'Show less' : 'View All ›'}</Text>
              </Pressable>
            )}
          </Card>
        </>
      )}

      {/* Bank picker */}
      {/* Naming somebody. A nickname is display-only — the holder name the bank
          returned is what the server re-confirms against before money moves —
          but it is also what the WhatsApp channel matches on, which is why an
          empty name simply clears the label rather than being refused. */}
      <Sheet open={!!renaming} onClose={() => setRenaming(null)} title={renaming?.nickname ? 'Change their name' : 'Give them a name'}>
        <Text style={{ fontSize: 13, fontFamily: font.regular, color: c.ink3, lineHeight: 19, marginBottom: 14 }}>
          {renaming ? `${renaming.name} · ${renaming.account_number} ${renaming.bank_name}` : ''}
        </Text>
        <TextInput
          value={nickDraft}
          onChangeText={setNickDraft}
          placeholder="Mum, landlord, Ada…"
          placeholderTextColor={c.ink3}
          accessibilityLabel="Name for this recipient"
          autoCapitalize="words"
          autoCorrect={false}
          maxLength={80}
          style={{ fontSize: 16, color: c.ink1, fontFamily: font.medium, borderWidth: 1,
                   borderColor: c.line, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 12 }}
        />
        <View style={{ height: 16 }} />
        <Btn
          label="Save name"
          onPress={async () => {
            const target = renaming;
            setRenaming(null);
            if (target) await renameBen(target, nickDraft.trim());
          }}
        />
      </Sheet>

      <Sheet open={bankSheet} onClose={() => { setBankSheet(false); setBankQuery(''); }} title="Select bank">
        <Field value={bankQuery} onChangeText={setBankQuery} placeholder="Search bank" prefix={<ZIcon name="search" size={18} color={c.ink3} />} />
        <View style={{ height: 6 }} />
        {filteredBanks.length === 0 ? (
          <Text style={{ color: c.ink3, fontFamily: font.regular, paddingVertical: 16, textAlign: 'center' }}>No bank matches “{bankQuery.trim()}”.</Text>
        ) : (
          filteredBanks.map((b, i) => (
            <Pressable key={b.code} onPress={() => chooseBank(b)} style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, borderTopWidth: i === 0 ? 0 : 1, borderTopColor: c.line }}>
              <BankLogo name={b.name} color={b.color} logo={b.logo} size={36} />
              <View style={{ flex: 1 }}><Text style={{ fontFamily: font.semibold, color: c.ink1 }}>{b.name}</Text>{b.aliases?.length ? <Text style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.regular }}>{b.aliases.join(' · ')}</Text> : null}</View>
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
        rows={[['To', recipientName || '—'], ['Account', picked ? picked.account_number : mode === 'bank' ? acct : identifier], ['Bank', picked ? picked.bank_name : mode === 'bank' ? bank?.name || '—' : 'Zitch'], ['Narration', note.trim() || `Transfer to ${recipientName || 'recipient'}`]]}
        onPay={() => { setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title={busy ? 'Processing transfer' : 'Enter your PIN'} protectScreen>
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
