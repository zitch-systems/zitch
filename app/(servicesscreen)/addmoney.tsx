import React, { useEffect, useMemo, useState } from 'react';
import { View, Text, Pressable, Share, Linking } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { router } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { apiJson } from '@/lib/api';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Card, Btn, Field, ZItem, NText, HeaderLink } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font, radius, iconTint } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

type DediAccount = {
  account_number: string;
  account_name: string;
  bank_name: string;
  bank_tier?: number;
};

// ---- Account number display helpers ----
// Grouping is DISPLAY ONLY: copy/share always send the raw digits, because a
// number with spaces in it is rejected by half the bank apps it gets pasted into.
const digitsOnly = (v: string) => (v || '').replace(/\D/g, '');
const groupAccount = (v: string) => {
  const d = digitsOnly(v);
  if (d.length !== 10) return v || '';
  return `${d.slice(0, 3)} ${d.slice(3, 6)} ${d.slice(6)}`;
};

// ---- Bank app shortcuts ----
// Best-effort deep links into the bank apps a customer is most likely to
// transfer FROM. Schemes are matched loosely on the bank's name; anything we
// can't resolve (or that isn't installed) just falls back to the copy, which is
// the part that actually matters.
const BANK_SCHEMES: Record<string, string> = {
  gtbank: 'gtworld://',
  'guaranty trust': 'gtworld://',
  access: 'accessmore://',
  zenith: 'zenithbank://',
  'first bank': 'firstmobile://',
  firstbank: 'firstmobile://',
  uba: 'ubamobile://',
  'united bank for africa': 'ubamobile://',
  kuda: 'kuda://',
  moniepoint: 'moniepoint://',
  palmpay: 'palmpay://',
  wema: 'alat://',
  alat: 'alat://',
  fidelity: 'fidelitybank://',
  sterling: 'onebank://',
  union: 'unionmobile://',
  stanbic: 'stanbicibtc://',
  fcmb: 'fcmbmobile://',
  polaris: 'polarisbank://',
  ecobank: 'ecobankmobile://',
  keystone: 'keystonebank://',
};

const schemeFor = (name: string) => {
  const k = (name || '').toLowerCase();
  const hit = Object.keys(BANK_SCHEMES).find((s) => k.includes(s));
  return hit ? BANK_SCHEMES[hit] : undefined;
};

const DEFAULT_BANKS = ['GTBank', 'Access Bank', 'Zenith Bank', 'First Bank', 'UBA', 'Kuda'];

// No bank logo assets ship with the app, so each bank gets a coloured monogram
// disc instead — built from its own name so a newly linked bank needs no asset.
const monogram = (name: string) => {
  const words = (name || '')
    .replace(/[^A-Za-z ]/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .filter((w) => !/^(bank|plc|mfb|microfinance|limited|ltd|nigeria)$/i.test(w));
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  const w = words[0] || name || '?';
  return w.slice(0, 2).toUpperCase();
};

type BankShortcut = { name: string; url?: string };

// The user's own linked banks first (those are the accounts they actually send
// from), topped up with the common ones until we have `max`.
const bankShortcuts = (linked: { bank_name: string }[], max: number): BankShortcut[] => {
  const seen = new Set<string>();
  const out: BankShortcut[] = [];
  const push = (raw: string) => {
    const name = (raw || '').trim();
    const key = name.toLowerCase();
    if (!name || seen.has(key) || out.length >= max) return;
    seen.add(key);
    out.push({ name, url: schemeFor(name) });
  };
  linked.forEach((a) => push(a.bank_name));
  DEFAULT_BANKS.forEach(push);
  return out;
};

// A receipt-style dashed rule. Rendered as a clipped 2px box with a full dashed
// border: Android ignores `borderStyle` on a single edge, so a plain
// `borderBottomWidth` rule would silently come out solid there.
const DashedRule = ({ style }: { style?: object }) => {
  const { c } = useTheme();
  return (
    <View style={[{ height: 1, overflow: 'hidden' }, style]}>
      <View style={{ height: 2, borderWidth: 1, borderStyle: 'dashed', borderColor: c.line, borderRadius: 1 }} />
    </View>
  );
};

// Secondary action: brand text on a brand tint. Pairs with <Btn> at the same
// height so the two sit as one row of equals.
const TintBtn = ({ label, icon, onPress, disabled }: { label: string; icon: string; onPress: () => void; disabled?: boolean }) => {
  const { c, theme } = useTheme();
  return (
    <Pressable
      onPress={disabled ? undefined : onPress}
      disabled={!!disabled}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: !!disabled }}
      style={({ pressed }) => ({
        flex: 1,
        height: 48,
        borderRadius: radius.pill,
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        backgroundColor: iconTint(c.brand, theme === 'dark'),
        opacity: disabled ? 0.5 : pressed ? 0.85 : 1,
      })}
    >
      <ZIcon name={icon} size={18} color={c.brand} stroke={2.2} />
      <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.brand }}>{label}</Text>
    </Pressable>
  );
};

// Funding is by bank transfer to a dedicated Zitch (Wema/ALAT) NUBAN — minted via
// Wema's reserved-account onboarding (enter BVN; Wema verifies it and issues the
// NUBAN over a one-time OTP). Wema has no hosted checkout, so there is no instant
// card/bank pay-in here; deposits to the NUBAN are credited automatically by the
// reconcile poller.
const AddMoney = () => {
  const { c, theme } = useTheme();
  const { accountNumber: walletAcctNo, accountName: walletAcctName, bankName: walletBank, linked } = useWallet();
  const [loading, setLoading] = useState(true);
  const [account, setAccount] = useState<DediAccount | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  // The customer's registered legal name — shown as the account name whenever the
  // provider omits it, so the funding card never renders a nameless account (which
  // a payer can't confirm before transferring).
  const [holderName, setHolderName] = useState('');
  const [bvn, setBvn] = useState('');
  const [creating, setCreating] = useState(false);

  // Wema/ALAT flow: account creation is a BVN + OTP round-trip. When the backend
  // answers otp_required, we hold the tracking id and show the OTP step.
  const [otpFlow, setOtpFlow] = useState<{ trackingId: string; destination: string } | null>(null);
  const [otp, setOtp] = useState('');
  const [verifying, setVerifying] = useState(false);

  const loadAccount = () => {
    let alive = true;
    setLoading(true);
    setLoadFailed(false);
    // Never let a slow/hanging backend leave the page stuck on the spinner: show
    // the screen within a few seconds no matter what. If the account lookup
    // resolves later, it still fills in (account state).
    const guard = setTimeout(() => { if (alive) setLoading(false); }, 8000);
    apiJson('/api/wallet/account/')
      .then((r) => {
        if (!alive || !r?.success) return;
        if (r.holder_name) setHolderName(r.holder_name);
        if (r.account_number) setAccount(r as DediAccount);
      })
      .catch(() => { if (alive) setLoadFailed(true); })
      .finally(() => { if (alive) { clearTimeout(guard); setLoading(false); } });
    return () => { alive = false; clearTimeout(guard); };
  };

  useEffect(() => {
    let cleanup: undefined | (() => void);
    const timer = setTimeout(() => { cleanup = loadAccount(); }, 0);
    return () => { clearTimeout(timer); cleanup?.(); };
  }, []);

  // The dedicated-account lookup is authoritative; the wallet context carries the
  // same NUBAN and covers the window before that request lands.
  const acctNo = digitsOnly(account?.account_number || walletAcctNo);
  const bank = account?.bank_name || walletBank || '';
  const acctName = account?.account_name || walletAcctName || holderName || '';
  const ready = !!acctNo;

  const banks = useMemo(() => bankShortcuts(linked, 5), [linked]);

  const copyAccount = async () => {
    if (!acctNo) return;
    await Clipboard.setStringAsync(acctNo);
    notify('Copied', 'Account number copied to clipboard');
  };

  const shareDetails = async () => {
    if (!acctNo) return;
    const message = [
      'My Zitch account details',
      `Account number: ${acctNo}`,
      bank ? `Bank: ${bank}` : '',
      acctName ? `Name: ${acctName}` : '',
    ].filter(Boolean).join('\n');
    try {
      await Share.share({ message });
    } catch {
      /* the user dismissed the share sheet — nothing to report */
    }
  };

  // Copy first, then try to hand the user off to their bank app. The copy is the
  // part that always works, so a missing/unopenable app is a silent no-op rather
  // than an error the user can do nothing about.
  const openBankApp = async (b: BankShortcut) => {
    await copyAccount();
    if (!b.url) return;
    try {
      await Linking.openURL(b.url);
    } catch {
      /* app not installed / scheme not registered — the number is already copied */
    }
  };

  const createAccount = async () => {
    if (bvn.length !== 11) return;
    setCreating(true);
    try {
      const r = await apiJson('/api/wallet/account/create/', { bvn });
      if (r?.success && r.account_number) {
        setAccount(r as DediAccount);
      } else if (r?.success && r.otp_required) {
        // Wema flow: an OTP was sent to the user's phone — collect it next.
        setOtpFlow({ trackingId: String(r.tracking_id || ''), destination: String(r.otp_destination || '') });
        setOtp('');
        notify('OTP sent', `Enter the code we sent to ${r.otp_destination || 'your phone'}`);
      } else {
        notify('Error', r?.message || "We couldn't create your account. Please try again.");
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setCreating(false);
    }
  };

  const verifyOtp = async () => {
    if (!otpFlow || otp.length < 4) return;
    setVerifying(true);
    try {
      const r = await apiJson('/api/wallet/wema/verify-otp/', {
        // The server binds this opaque tracking ID to the identity used to start
        // the flow.  Never resend the BVN (or let a client swap identity/type) at
        // verification time.
        otp, tracking_id: otpFlow.trackingId,
      });
      if (r?.success && r.account_number) {
        setAccount(r as DediAccount);
        setOtpFlow(null);
        notify('Account ready', 'Your Zitch account number is ready — fund it by bank transfer.');
      } else {
        notify('Error', r?.message || 'OTP verification failed. Please try again.');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setVerifying(false);
    }
  };

  const resendOtp = async () => {
    if (!otpFlow) return;
    try {
      const r = await apiJson('/api/wallet/wema/resend-otp/', {
        tracking_id: otpFlow.trackingId,
      });
      notify(r?.success ? 'OTP resent' : 'Error', r?.message || (r?.success ? 'Check your phone' : "Couldn't resend the OTP"));
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    }
  };

  if (loading) {
    return (
      <Screen>
        <Header title="Add money" onBack={() => router.back()} />
        <Loading />
      </Screen>
    );
  }

  // OTP is a focused step in the middle of account creation — it gets the whole
  // screen rather than sitting inside the menu, so there is one thing to do.
  if (otpFlow) {
    return (
      <Screen>
        <Header title="Add money" onBack={() => router.back()} />
        <View style={{ alignItems: 'center', paddingHorizontal: 16, paddingTop: 6 }}>
          <View style={{ width: 72, height: 72, borderRadius: 22, backgroundColor: iconTint(c.brand, theme === 'dark'), alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="lock" size={34} color={c.brand} />
          </View>
          <Text style={{ fontSize: 17, color: c.ink1, fontFamily: font.extrabold, marginTop: 16, textAlign: 'center' }}>
            Enter the OTP
          </Text>
          <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular, marginTop: 8, textAlign: 'center', lineHeight: 20 }}>
            We sent a one-time code to {otpFlow.destination || 'your phone'} to confirm your account.
          </Text>
        </View>

        <View style={{ height: 18 }} />
        <Field
          label="One-time code"
          value={otp}
          onChangeText={(v) => setOtp(v.replace(/\D/g, '').slice(0, 8))}
          keyboardType="number-pad"
          placeholder="Enter the code"
        />
        <View style={{ height: 18 }} />
        <Btn
          label={verifying ? 'Confirming…' : 'Confirm code'}
          icon="check"
          disabled={verifying || otp.length < 4}
          onPress={verifyOtp}
        />
        <Pressable onPress={resendOtp} hitSlop={10} style={{ alignItems: 'center', marginTop: 16 }}>
          <Text style={{ fontSize: 13.5, color: c.brand, fontFamily: font.bold }}>Resend code</Text>
        </Pressable>
        <Pressable onPress={() => { setOtpFlow(null); setOtp(''); }} hitSlop={10} style={{ alignItems: 'center', marginTop: 12 }}>
          <Text style={{ fontSize: 13, color: c.ink3, fontFamily: font.medium }}>Start over</Text>
        </Pressable>
      </Screen>
    );
  }

  const chevron = <ZIcon name="right" size={18} color={c.ink3} stroke={2.2} />;

  return (
    <Screen>
      <Header
        title="Add money"
        onBack={() => router.back()}
        right={<HeaderLink label="History" onPress={() => router.push('/history')} />}
      />

      {loadFailed && !ready && (
        <Card style={{ marginBottom: 14 }} pad={14}>
          <Text style={{ fontFamily: font.regular, fontSize: 13, color: c.ink3, lineHeight: 19 }}>
            We couldn’t check your funding account. Retry before creating a new one.
          </Text>
          <Pressable onPress={loadAccount} accessibilityRole="button" style={{ marginTop: 9 }}>
            <Text style={{ fontFamily: font.bold, fontSize: 13, color: c.brand }}>Retry</Text>
          </Pressable>
        </Card>
      )}

      {/* ---- 1. Bank transfer: the primary route, expanded inline ---- */}
      <Card pad={16}>
        <ZItem
          icon="bank"
          title="Bank Transfer"
          sub="Add money via mobile or internet banking"
          right={ready ? chevron : undefined}
          onPress={ready ? () => router.push('/banktransfer') : undefined}
          last
        />
        <DashedRule style={{ marginTop: 4, marginBottom: 16 }} />

        {ready ? (
          <>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.medium }}>Zitch Account Number</Text>
            <NText
              numberOfLines={1}
              style={{ fontSize: 31, color: c.ink1, fontFamily: font.extrabold, letterSpacing: 0.6, marginTop: 5, fontVariant: ['tabular-nums'] }}
            >
              {groupAccount(acctNo)}
            </NText>
            {bank || acctName ? (
              <Text numberOfLines={1} style={{ fontSize: 12.5, color: c.ink2, fontFamily: font.medium, marginTop: 6 }}>
                {[bank, acctName].filter(Boolean).join(' · ')}
              </Text>
            ) : null}

            <View style={{ flexDirection: 'row', gap: 12, marginTop: 16 }}>
              <TintBtn label="Copy Number" icon="copy" onPress={copyAccount} />
              <View style={{ flex: 1 }}>
                <Btn label="Share Details" icon="share" size="md" onPress={shareDetails} />
              </View>
            </View>

            {/* Bank shortcuts — copy the number and hand off to the app they'll
                actually transfer from. */}
            <View style={{ backgroundColor: c.surface2, borderRadius: radius.md, padding: 14, marginTop: 16 }}>
              <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.medium }}>Tap a bank to copy &amp; open its app</Text>
              <View style={{ flexDirection: 'row', gap: 6, marginTop: 12 }}>
                {banks.map((b, i) => {
                  const tones = [c.brand, c.violet, c.amber, c.brandDeep, c.lime];
                  const tone = tones[i % tones.length];
                  return (
                    <Pressable
                      key={b.name}
                      onPress={() => openBankApp(b)}
                      accessibilityRole="button"
                      accessibilityLabel={`Copy account number and open ${b.name}`}
                      style={({ pressed }) => ({ flex: 1, alignItems: 'center', gap: 6, opacity: pressed ? 0.7 : 1 })}
                    >
                      <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: iconTint(tone, theme === 'dark'), alignItems: 'center', justifyContent: 'center' }}>
                        <Text style={{ fontSize: 14, fontFamily: font.extrabold, color: tone }}>{monogram(b.name)}</Text>
                      </View>
                      <Text numberOfLines={1} style={{ fontSize: 10.5, color: c.ink3, fontFamily: font.medium, textAlign: 'center' }}>
                        {b.name}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>

            <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 8, marginTop: 14 }}>
              <View style={{ marginTop: 1 }}>
                <ZIcon name="check" size={14} color={c.lime} stroke={2.6} />
              </View>
              <NText style={{ flex: 1, fontSize: 11.5, color: c.ink3, fontFamily: font.regular, lineHeight: 17 }}>
                {`This account is permanently yours — transfers land automatically, usually in seconds. Tier ${account?.bank_tier || 1}: single inflow up to ${account?.bank_tier === 2 ? '₦100,000' : account?.bank_tier === 3 ? 'no stated limit' : '₦50,000'}, maximum balance ${account?.bank_tier === 2 ? '₦500,000' : account?.bank_tier === 3 ? 'with no stated limit' : '₦300,000'}.`}
              </NText>
            </View>
          </>
        ) : (
          // No dedicated NUBAN yet — the BVN round-trip that mints one lives right
          // where the account number will appear.
          <>
            <Text style={{ fontSize: 15, color: c.ink1, fontFamily: font.bold }}>Get your account number</Text>
            <Text style={{ fontSize: 13, color: c.ink3, fontFamily: font.regular, marginTop: 6, lineHeight: 20 }}>
              Enter your BVN to get a dedicated account for funding by bank transfer. It’s verified
              securely; we never store it.
            </Text>
            {holderName ? (
              <Text style={{ fontSize: 12.5, color: c.ink2, fontFamily: font.semibold, marginTop: 8 }}>
                Opened in your name · {holderName}
              </Text>
            ) : null}
            <View style={{ height: 14 }} />
            <Field
              label="Bank Verification Number (BVN)"
              value={bvn}
              onChangeText={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))}
              keyboardType="number-pad"
              placeholder="Enter your 11-digit BVN"
            />
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 8 }}>
              <ZIcon name="lock" size={13} color={c.ink3} />
              <Text style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.regular }}>
                Dial *565*0# on your registered line to get your BVN.
              </Text>
            </View>
            <View style={{ height: 16 }} />
            <Btn
              label={creating ? 'Creating your account…' : 'Get my account'}
              icon="bank"
              disabled={creating || bvn.length !== 11}
              onPress={createAccount}
            />
          </>
        )}
      </Card>

      {/* ---- 2–5. The other ways in, one card each ---- */}
      <Card pad={16} style={{ marginTop: 12 }}>
        <ZItem
          icon="deposit"
          title="Cash Deposit"
          sub="Fund your account with nearby agents"
          right={chevron}
          onPress={() => router.push('/comingsoon')}
          last
        />
      </Card>

      <Card pad={16} style={{ marginTop: 12 }}>
        <ZItem
          icon="card"
          title="Top-up with Card/Account"
          sub="Add money directly from your bank card or account"
          right={chevron}
          onPress={() => router.push('/linkbank')}
          last
        />
      </Card>

      <Card pad={16} style={{ marginTop: 12 }}>
        <ZItem
          icon="phone"
          title="Bank USSD"
          sub="With other banks’ USSD codes"
          right={chevron}
          onPress={() => router.push('/ussd')}
          last
        />
      </Card>

      <Card pad={16} style={{ marginTop: 12 }}>
        <ZItem
          icon="qr"
          title="Scan my QR Code"
          sub="Show your QR code to any Zitch user"
          right={chevron}
          onPress={() => router.push('/scan')}
          last
        />
      </Card>
    </Screen>
  );
};

export default AddMoney;
