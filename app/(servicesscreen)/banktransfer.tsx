import React, { useMemo } from 'react';
import { View, Text, Pressable, Share, Linking } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { router } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { Screen, Header, Card, Btn, NText } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font, radius, iconTint } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

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
// can't resolve (or that isn't installed) leaves the user with the number on
// their clipboard, which is the part that actually matters.
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

const DEFAULT_BANKS = [
  'GTBank', 'Access Bank', 'Zenith Bank', 'First Bank', 'UBA',
  'Kuda', 'Moniepoint', 'Fidelity Bank', 'Sterling Bank',
];

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
const DashedRule = () => {
  const { c } = useTheme();
  return (
    <View style={{ height: 1, overflow: 'hidden', marginVertical: 14 }}>
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

// One line of the details card: icon tile + (small grey label / large value).
const DetailRow = ({ icon, label, value, big }: { icon: string; label: string; value: string; big?: boolean }) => {
  const { c, theme } = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
      <View style={{ width: 42, height: 42, borderRadius: 13, backgroundColor: iconTint(c.brand, theme === 'dark'), alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name={icon} size={20} color={c.brand} stroke={2} />
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.medium }}>{label}</Text>
        <NText
          numberOfLines={big ? 1 : 2}
          style={{
            fontSize: big ? 27 : 16,
            color: c.ink1,
            fontFamily: big ? font.extrabold : font.bold,
            marginTop: big ? 3 : 2,
            letterSpacing: big ? 0.5 : 0,
            ...(big ? { fontVariant: ['tabular-nums'] as const } : null),
          }}
        >
          {value}
        </NText>
      </View>
    </View>
  );
};

/**
 * Bank Transfer — everything a payer needs to move money into the Zitch wallet
 * from any other bank: the account details, how it works, and a shortcut into
 * the app they will send from. All details come from the wallet context; nothing
 * here is hardcoded.
 */
const BankTransfer = () => {
  const { c, theme } = useTheme();
  const { accountNumber, accountName, bankName, linked } = useWallet();

  const acctNo = digitsOnly(accountNumber);
  const ready = !!acctNo;
  const banks = useMemo(() => bankShortcuts(linked, 9), [linked]);

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
      bankName ? `Bank: ${bankName}` : '',
      accountName ? `Name: ${accountName}` : '',
    ].filter(Boolean).join('\n');
    try {
      await Share.share({ message });
    } catch {
      /* the user dismissed the share sheet — nothing to report */
    }
  };

  // Copy first, then try to hand the user off to their bank app.
  const openBankApp = async (b: BankShortcut) => {
    await copyAccount();
    if (!b.url) {
      notify('Open your bank app', `Your account number is copied — paste it in the ${b.name} app.`);
      return;
    }
    try {
      await Linking.openURL(b.url);
    } catch {
      notify('Open your bank app', `We couldn’t open ${b.name}. Your account number is copied — paste it there.`);
    }
  };

  const steps: string[] = [
    ready
      ? `Copy the account number above — ${groupAccount(acctNo)} is your Zitch account number.`
      : 'Copy the account number above — it is your Zitch account number.',
    'Open the bank app you want to transfer from.',
    'Transfer any amount to your Zitch account. Money lands instantly.',
  ];

  return (
    <Screen>
      <Header title="Bank Transfer" onBack={() => router.back()} />

      {/* ---- Account details ---- */}
      <Card pad={16}>
        <DetailRow icon="bank" label="Zitch Account Number" value={ready ? groupAccount(acctNo) : 'Not set up yet'} big />
        <DashedRule />
        <DetailRow icon="wallet" label="Bank" value={bankName || '—'} />
        <DashedRule />
        <DetailRow icon="user" label="Name" value={accountName || '—'} />

        <View style={{ flexDirection: 'row', gap: 12, marginTop: 18 }}>
          <TintBtn label="Copy Number" icon="copy" onPress={copyAccount} disabled={!ready} />
          <View style={{ flex: 1 }}>
            <Btn label="Share Details" icon="share" size="md" onPress={shareDetails} disabled={!ready} />
          </View>
        </View>
      </Card>

      {/* ---- How it works ---- */}
      <Card pad={16} style={{ marginTop: 12 }}>
        <Text style={{ fontSize: 15.5, color: c.ink1, fontFamily: font.extrabold }}>Add money in 3 steps</Text>
        <View style={{ marginTop: 14, gap: 14 }}>
          {steps.map((s, i) => (
            <View key={s} style={{ flexDirection: 'row', gap: 12 }}>
              <View style={{ width: 26, height: 26, borderRadius: 13, backgroundColor: iconTint(c.brand, theme === 'dark'), alignItems: 'center', justifyContent: 'center' }}>
                <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.brand }}>{i + 1}</Text>
              </View>
              <NText style={{ flex: 1, fontSize: 13.5, color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>{s}</NText>
            </View>
          ))}
        </View>
      </Card>

      {/* ---- Bank shortcuts ---- */}
      <Card pad={16} style={{ marginTop: 12 }}>
        <Text style={{ fontSize: 15.5, color: c.ink1, fontFamily: font.extrabold }}>Tap a bank to open its app</Text>
        <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular, marginTop: 4 }}>
          We’ll copy your account number first.
        </Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginTop: 14 }}>
          {banks.map((b, i) => {
            const tones = [c.brand, c.violet, c.amber, c.brandDeep, c.lime, c.red];
            const tone = tones[i % tones.length];
            return (
              <Pressable
                key={b.name}
                onPress={() => openBankApp(b)}
                accessibilityRole="button"
                accessibilityLabel={`Copy account number and open ${b.name}`}
                style={({ pressed }) => ({ width: '33.33%', alignItems: 'center', gap: 8, paddingVertical: 10, opacity: pressed ? 0.7 : 1 })}
              >
                <View style={{ width: 52, height: 52, borderRadius: 26, backgroundColor: iconTint(tone, theme === 'dark'), alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ fontSize: 17, fontFamily: font.extrabold, color: tone }}>{monogram(b.name)}</Text>
                </View>
                <Text numberOfLines={1} style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.medium, textAlign: 'center', paddingHorizontal: 4 }}>
                  {b.name}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </Card>
    </Screen>
  );
};

export default BankTransfer;
