import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, Pressable, Linking } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import Constants from 'expo-constants';
import { apiJson, apiPost } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Avatar } from '@/components/design/Brand';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { Screen, Card, ZItem, Toggle, money, NText, PinSheet } from '@/components/design/ui';
import { Hero } from '@/components/design/widgets';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { clearSession, getToken, saveTransactionPin } from '@/lib/secureStore';
import { isBiometricAvailable, isBiometricEnabled, setBiometricEnabled, isBiometricTxnEnabled, setBiometricTxnEnabled, authenticate } from '@/lib/biometrics';
import { TERMS_URL, PRIVACY_URL } from '@/components/configFiles/links';

const RowBadge = ({ label, hot }: { label: string; hot?: boolean }) => {
  const { c } = useTheme();
  return (
    <View style={{ paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, backgroundColor: hot ? c.red : c.amber }}>
      <NText style={{ fontSize: 10, fontFamily: font.bold, color: '#fff' }}>{label}</NText>
    </View>
  );
};

/** Uppercase group heading. One spacing rule for every section on the screen. */
const GroupLabel = ({ children }: { children: string }) => {
  const { c } = useTheme();
  return (
    <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.ink3, marginLeft: 22, marginTop: 20, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>
      {children}
    </Text>
  );
};

type Row = { icon: string; title: string; sub?: string; badge?: string; hot?: boolean; go?: () => void; right?: React.ReactNode };

/**
 * A group of rows in one card. Every group on this screen renders through here,
 * so the icon column, text baseline and divider insets line up from top to
 * bottom — rows and toggles alike, which is what previously drifted when
 * toggles lived in their own one-off cards.
 */
const Group = ({ items }: { items: Row[] }) => {
  const { c } = useTheme();
  return (
    <Card pad={0} style={{ marginHorizontal: 16, paddingHorizontal: 16 }}>
      {items.map((r, i) => (
        <ZItem
          key={r.title}
          icon={r.icon}
          title={r.title}
          sub={r.sub}
          onPress={r.go}
          last={i === items.length - 1}
          right={
            r.right ?? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                {r.badge && <RowBadge label={r.badge} hot={r.hot} />}
                <ZIcon name="right" size={18} color={c.ink3} />
              </View>
            )
          }
        />
      ))}
    </Card>
  );
};

const Me = () => {
  const { c, theme, setTheme } = useTheme();
  const { balance, firstName, avatar, showBal, setShowBal, reload: reloadWallet } = useWallet();
  const [biometrics, setBiometrics] = useState(false);
  const [bioTxn, setBioTxn] = useState(false);
  const [pinOpen, setPinOpen] = useState(false);
  const [tier, setTier] = useState(1);

  useEffect(() => {
    isBiometricEnabled().then(setBiometrics);
    isBiometricTxnEnabled().then(setBioTxn);
  }, []);

  // Reflect the real KYC tier (was hardcoded "Tier 3"); refresh on focus so it
  // updates after the user completes a KYC step.
  useFocusEffect(
    useCallback(() => {
      reloadWallet(); // keep the balance shown here in sync
      (async () => {
        const t = await getToken();
        if (!t) return;
        try {
          const res = await apiJson('/api/kyc/status/');
          if (res?.tier !== undefined) setTier(Number(res.tier));
        } catch {
          // keep last-known tier
        }
      })();
    }, [reloadWallet])
  );

  // Biometric SIGN-IN toggle. Enabling requires a live scan; disabling is
  // immediate and independent of transaction-biometrics.
  const toggleBio = async (v: boolean) => {
    if (!v) {
      await setBiometricEnabled(false);
      setBiometrics(false);
      return;
    }
    const available = await isBiometricAvailable();
    if (!available) {
      notify('Biometrics unavailable', 'Set up Face ID or a fingerprint in your device settings first.');
      return;
    }
    const ok = await authenticate('Enable biometric sign-in');
    if (ok) {
      await setBiometricEnabled(true);
      setBiometrics(true);
    }
  };

  // Biometric TRANSACTION-approval toggle — separate from sign-in. SecureStore
  // performs the single native authentication when the entered PIN is saved.
  const toggleBioTxn = async (v: boolean) => {
    if (!v) {
      await setBiometricTxnEnabled(false);
      setBioTxn(false);
      return;
    }
    const available = await isBiometricAvailable();
    if (!available) {
      notify('Biometrics unavailable', 'Set up Face ID or a fingerprint in your device settings first.');
      return;
    }
    setPinOpen(true);  // capture the PIN, then save behind the OS biometric ACL
  };

  const enablePay = async (pin: string) => {
    setPinOpen(false);
    await saveTransactionPin(pin);
    await setBiometricTxnEnabled(true);
    setBioTxn(true);
    notify('Done', 'You can now approve payments with biometrics.');
  };

  const openUrl = (url: string) => Linking.openURL(url).catch(() => notify('Error', 'Could not open this link.'));

  const handleLogout = async () => {
    // Revoke the token server-side first so a leaked copy can't be replayed;
    // best-effort — a network error must not block signing out locally.
    try { await apiPost('/api/logout/'); } catch { /* fall through to local clear */ }
    await clearSession();
    router.replace('/signin');
  };

  const version = Constants.expoConfig?.version ?? '1.0.0';

  const account: Row[] = [
    { icon: 'user', title: 'Personal details', sub: 'Name, photo, email & phone', go: () => router.push('/accountdetails') },
    { icon: 'history', title: 'Transaction History', go: () => router.push('/history') },
    // Named for the thing people come here to DO. A refused spend now tells the
    // customer to "open Me → Verify identity"; sending them to a row called
    // "Account Limits" makes them hunt for a screen they were just pointed at.
    { icon: 'chart', title: 'Verify identity', sub: 'KYC tiers & transaction limits', go: () => router.push('/kyc') },
    // Separate from "Verify identity" on purpose. That row RAISES the ceiling by
    // proving who you are; this one LOWERS your own limit under it. Same number
    // on screen, opposite directions, and one row for both would explain neither.
    { icon: 'shield', title: 'Transaction limit', sub: 'Set your own lower spending limit', go: () => router.push('/limits') },
    { icon: 'card', title: 'Cards', sub: 'Your virtual cards', go: () => router.push('/cards') },
  ];
  const preferences: Row[] = [
    { icon: 'spark', title: 'Dark mode', sub: 'Easier on the eyes at night', right: <Toggle on={theme === 'dark'} onChange={(v) => setTheme(v ? 'dark' : 'light')} /> },
    { icon: 'fingerprint', title: 'Biometric sign-in', sub: 'Unlock the app with Face ID / fingerprint', right: <Toggle on={biometrics} onChange={toggleBio} /> },
    { icon: 'faceid', title: 'Approve payments with biometrics', sub: 'Confirm transfers & bills with Face ID / fingerprint instead of your PIN', right: <Toggle on={bioTxn} onChange={toggleBioTxn} /> },
  ];
  const security: Row[] = [
    { icon: 'insurance', title: 'Security Center', sub: 'Protect your funds', go: () => router.push('/securitysetup') },
    { icon: 'lock', title: 'Change Transaction PIN', sub: 'Update your 6-digit PIN', go: () => router.push('/resetpin') },
  ];
  const explore: Row[] = [
    { icon: 'invite', title: 'Zitch Junior', sub: 'Create an account for your child', badge: 'New', hot: true, go: () => router.push('/junior') },
    { icon: 'loan', title: 'Buy Now, Pay Later', sub: 'Shop now, spread the cost', badge: 'Enjoy ₦0', go: () => router.push('/bnpl') },
    { icon: 'gift', title: 'Invitation', sub: 'Share Zitch — rewards coming soon', go: () => router.push('/invite') },
    { icon: 'airtime', title: 'Zitch USSD', sub: 'Bank without internet', go: () => router.push('/ussd') },
  ];
  const about: Row[] = [
    { icon: 'help', title: 'Help & Support', go: () => router.push('/support') },
    { icon: 'ticket', title: 'Terms of Service', go: () => openUrl(TERMS_URL) },
    { icon: 'insurance', title: 'Privacy Policy', go: () => openUrl(PRIVACY_URL) },
  ];

  return (
    <Screen pad={false} tab>
      {/* header — the whole block is the profile tap target (it previously did
          nothing, which read as a broken screen) */}
      <Pressable
        onPress={() => router.push('/accountdetails')}
        accessibilityRole="button"
        accessibilityLabel="Your profile"
        style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 18, paddingTop: 6 }}
      >
        <Avatar size={50} ring={c.brand} surface={c.surface} uri={avatar} />
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1 }}>Hi, {firstName || 'there'}</Text>
          {/* Tier pill sits on the screen bg (no surface). The amber tint goes
              dark over the near-black dark bg, so a dark-brown ink is only
              legible in light mode — use the bright amber token in dark. */}
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 4, paddingHorizontal: 9, paddingVertical: 3, borderRadius: 999, backgroundColor: 'rgba(245,166,35,.16)', alignSelf: 'flex-start' }}>
            <ZIcon name="check" size={11} color={theme === 'dark' ? c.amber : '#B27400'} stroke={2.6} />
            <Text style={{ color: theme === 'dark' ? c.amber : '#B27400', fontSize: 11.5, fontFamily: font.bold }}>Tier {tier}</Text>
          </View>
        </View>
        <View style={{ width: 40, height: 40, borderRadius: 13, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="right" size={20} color={c.ink1} />
        </View>
      </Pressable>

      {/* balance */}
      <View style={{ paddingHorizontal: 20, paddingTop: 12 }}>
        <Pressable onPress={() => setShowBal(!showBal)} hitSlop={12} style={{ flexDirection: 'row', alignItems: 'center', gap: 7, alignSelf: 'flex-start' }}>
          <Text style={{ color: c.ink3, fontSize: 13, fontFamily: font.regular }}>Total balance</Text>
          <ZIcon name={showBal ? 'eye' : 'eyeoff'} size={15} color={c.ink3} />
        </Pressable>
        <NText style={{ fontSize: 32, fontFamily: font.extrabold, color: c.ink1, marginTop: 2, fontVariant: ['tabular-nums'] }}>
          {showBal ? money(balance) : '₦ ••••••'}
        </NText>
      </View>

      {/* safety tips */}
      <Pressable onPress={() => router.push('/safetytips')} style={{ marginHorizontal: 16, marginTop: 16 }}>
        <Hero style={{ padding: 14, flexDirection: 'row', alignItems: 'center', gap: 12 }} watermark={0}>
          <ZIcon name="insurance" size={22} color="#fff" />
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 14, fontFamily: font.bold, color: '#fff' }}>5 Safety Tips</Text>
            <Text style={{ fontSize: 12, color: 'rgba(255,255,255,.85)', fontFamily: font.regular }}>Make your account more secure</Text>
          </View>
          <View style={{ paddingVertical: 7, paddingHorizontal: 16, borderRadius: 999, backgroundColor: '#fff' }}>
            <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 12.5 }}>View</Text>
          </View>
        </Hero>
      </Pressable>

      {/* Bank on WhatsApp — the channel's hero entry */}
      <Card pad={0} style={{ marginHorizontal: 16, marginTop: 14, paddingHorizontal: 16 }}>
        <ZItem
          leading={<WhatsAppGlyph size={22} color="#fff" />} iconBg="#25D366"
          title="Link WhatsApp" sub="Bank from your WhatsApp chats" last
          onPress={() => router.push('/linkwhatsapp')}
          right={<ZIcon name="right" size={18} color={c.ink3} />}
        />
      </Card>

      <GroupLabel>Account</GroupLabel>
      <Group items={account} />

      <GroupLabel>Preferences</GroupLabel>
      <Group items={preferences} />

      <GroupLabel>Security</GroupLabel>
      <Group items={security} />

      <GroupLabel>Explore</GroupLabel>
      <Group items={explore} />

      <GroupLabel>About</GroupLabel>
      <Group items={about} />

      {/* logout */}
      <View style={{ paddingHorizontal: 16, paddingTop: 20 }}>
        <Pressable onPress={handleLogout} style={{ paddingVertical: 14, borderRadius: 16, backgroundColor: 'rgba(255,59,59,.1)', alignItems: 'center' }}>
          <Text style={{ color: c.red, fontFamily: font.bold }}>Log out</Text>
        </Pressable>
      </View>

      <Text style={{ textAlign: 'center', color: c.ink3, fontSize: 12, marginTop: 16, fontFamily: font.regular }}>Zitch v{version}</Text>

      <PinSheet
        open={pinOpen}
        onClose={() => setPinOpen(false)}
        onComplete={enablePay}
        title="Pay with biometrics?"
        subtitle="Enter your 6-digit PIN to approve payments with biometrics too. Skip to use it for sign-in only."
      />
    </Screen>
  );
};

export default Me;
