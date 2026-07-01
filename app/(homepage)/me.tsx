import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import { apiJson, apiPost } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Avatar } from '@/components/design/Brand';
import { Screen, Card, ZItem, money, NText, PinSheet } from '@/components/design/ui';
import { Hero } from '@/components/design/widgets';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { clearSession, getToken, saveTransactionPin } from '@/lib/secureStore';
import { isBiometricAvailable, isBiometricEnabled, setBiometricEnabled, isBiometricTxnEnabled, setBiometricTxnEnabled, authenticate } from '@/lib/biometrics';

const Toggle = ({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) => {
  const { c } = useTheme();
  return (
    <Pressable onPress={() => onChange(!on)} style={{ width: 46, height: 28, borderRadius: 999, padding: 3, backgroundColor: on ? c.brand : c.surface3, justifyContent: 'center' }}>
      <View style={{ width: 22, height: 22, borderRadius: 11, backgroundColor: '#fff', transform: [{ translateX: on ? 18 : 0 }] }} />
    </Pressable>
  );
};

const RowBadge = ({ label, hot }: { label: string; hot?: boolean }) => {
  const { c } = useTheme();
  return (
    <View style={{ paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, backgroundColor: hot ? c.red : c.amber }}>
      <NText style={{ fontSize: 10, fontFamily: font.bold, color: '#fff' }}>{label}</NText>
    </View>
  );
};

const Me = () => {
  const { c, theme, setTheme } = useTheme();
  const { balance, firstName, avatar, showBal, reload: reloadWallet } = useWallet();
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
          if (res?.tier) setTier(Number(res.tier));
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

  // Biometric TRANSACTION-approval toggle — separate from sign-in. Enabling scans
  // then captures the PIN to cache (the only path that stores it); disabling drops
  // the cached PIN so payments fall back to the keypad.
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
    const ok = await authenticate('Approve payments with biometrics', true);
    if (ok) setPinOpen(true);  // capture the PIN to cache
  };

  const enablePay = async (pin: string) => {
    setPinOpen(false);
    await saveTransactionPin(pin);
    await setBiometricTxnEnabled(true);
    setBioTxn(true);
    notify('Done', 'You can now approve payments with biometrics.');
  };

  const handleLogout = async () => {
    // Revoke the token server-side first so a leaked copy can't be replayed;
    // best-effort — a network error must not block signing out locally.
    try { await apiPost('/api/logout/'); } catch { /* fall through to local clear */ }
    await clearSession();
    router.replace('/signin');
  };

  const chev = <ZIcon name="right" size={18} color={c.ink3} />;
  const grp1: any[] = [
    { icon: 'history', title: 'Transaction History', go: () => router.push('/history') },
    { icon: 'chart', title: 'Account Limits', sub: 'KYC tiers & transaction limits', go: () => router.push('/kyc') },
    { icon: 'card', title: 'Bank Card / Account', sub: 'Add a payment option', go: () => router.push('/accountdetails') },
    { icon: 'bank', title: 'My BizPayment', sub: 'Receive payment for business', go: () => router.push('/bizpayment') },
    { icon: 'invite', title: 'Zitch Junior', sub: 'Create an account for your child', badge: 'New', hot: true, go: () => router.push('/junior') },
    { icon: 'loan', title: 'Buy Now, Pay Later', sub: 'Shop now, spread the cost', badge: 'Enjoy ₦0', go: () => router.push('/bnpl') },
  ];
  const grp2: any[] = [
    { icon: 'insurance', title: 'Security Center', sub: 'Protect your funds', go: () => router.push('/securitysetup') },
    { icon: 'lock', title: 'Change Transaction PIN', sub: 'Update your 4-digit PIN', go: () => router.push('/resetpin') },
    { icon: 'help', title: 'Customer Service Center', go: () => router.push('/support') },
    { icon: 'gift', title: 'Invitation', sub: 'Invite friends & earn up to ₦5,600', go: () => router.push('/invite') },
    { icon: 'airtime', title: 'Zitch USSD', sub: 'Bank without internet', go: () => router.push('/ussd') },
  ];

  const Group = ({ items }: { items: any[] }) => (
    <Card style={{ marginHorizontal: 16, marginTop: 14, paddingVertical: 2 }} pad={0}>
      <View style={{ paddingHorizontal: 16 }}>
        {items.map((r, i) => (
          <ZItem
            key={r.title}
            icon={r.icon}
            title={r.title}
            sub={r.sub}
            onPress={r.go}
            last={i === items.length - 1}
            right={
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                {r.badge && <RowBadge label={r.badge} hot={r.hot} />}
                {chev}
              </View>
            }
          />
        ))}
      </View>
    </Card>
  );

  return (
    <Screen pad={false} tab>
      {/* header */}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 18, paddingTop: 6 }}>
        <Avatar size={50} ring={c.brand} surface={c.surface} uri={avatar} />
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1 }}>Hi, {firstName || 'there'}</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 4, paddingHorizontal: 9, paddingVertical: 3, borderRadius: 999, backgroundColor: 'rgba(245,166,35,.16)', alignSelf: 'flex-start' }}>
            <ZIcon name="check" size={11} color="#B27400" stroke={2.6} />
            <Text style={{ color: '#B27400', fontSize: 11.5, fontFamily: font.bold }}>Tier {tier}</Text>
          </View>
        </View>
        <Pressable onPress={() => router.push('/settings')} style={{ width: 40, height: 40, borderRadius: 13, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="settings" size={20} color={c.ink1} />
        </Pressable>
      </View>

      {/* balance */}
      <View style={{ paddingHorizontal: 20, paddingTop: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7 }}>
          <Text style={{ color: c.ink3, fontSize: 13, fontFamily: font.regular }}>Total balance</Text>
          <ZIcon name={showBal ? 'eye' : 'eyeoff'} size={15} color={c.ink3} />
        </View>
        <NText style={{ fontSize: 32, fontFamily: font.extrabold, color: c.ink1, marginTop: 2, fontVariant: ['tabular-nums'] }}>
          {showBal ? money(balance) : '₦ ••••••'}
        </NText>
      </View>

      {/* safety tips */}
      <Pressable onPress={() => router.push('/safetytips')} style={{ marginHorizontal: 16, marginTop: 12 }}>
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

      <Group items={grp1} />
      <Group items={grp2} />

      {/* biometrics — sign-in */}
      <Card style={{ marginHorizontal: 16, marginTop: 14, flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12 }}>
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="fingerprint" size={20} color={c.brand} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={{ fontFamily: font.semibold, color: c.ink1 }}>Biometric sign-in</Text>
          <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>Unlock the app with Face ID / fingerprint</Text>
        </View>
        <Toggle on={biometrics} onChange={toggleBio} />
      </Card>

      {/* biometrics — transaction approval (separate toggle) */}
      <Card style={{ marginHorizontal: 16, marginTop: 14, flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12 }}>
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="faceid" size={20} color={c.brand} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={{ fontFamily: font.semibold, color: c.ink1 }}>Approve payments with biometrics</Text>
          <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>Confirm transfers & bills with Face ID / fingerprint instead of your PIN</Text>
        </View>
        <Toggle on={bioTxn} onChange={toggleBioTxn} />
      </Card>

      {/* dark mode */}
      <Card style={{ marginHorizontal: 16, marginTop: 14, flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12 }}>
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="spark" size={20} color={c.brand} />
        </View>
        <Text style={{ flex: 1, fontFamily: font.semibold, color: c.ink1 }}>Dark mode</Text>
        <Toggle on={theme === 'dark'} onChange={(v) => setTheme(v ? 'dark' : 'light')} />
      </Card>

      {/* logout */}
      <View style={{ paddingHorizontal: 16, paddingTop: 16 }}>
        <Pressable onPress={handleLogout} style={{ paddingVertical: 14, borderRadius: 16, backgroundColor: 'rgba(255,59,59,.1)', alignItems: 'center' }}>
          <Text style={{ color: c.red, fontFamily: font.bold }}>Log out</Text>
        </Pressable>
      </View>

      <PinSheet
        open={pinOpen}
        onClose={() => setPinOpen(false)}
        onComplete={enablePay}
        title="Pay with biometrics?"
        subtitle="Enter your 4-digit PIN to approve payments with biometrics too. Skip to use it for sign-in only."
      />
    </Screen>
  );
};

export default Me;
