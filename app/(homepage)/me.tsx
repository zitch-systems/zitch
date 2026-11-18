import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import { apiPost } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import { kycService } from '@/lib/services/kyc';
import ZIcon from '@/components/design/ZIcon';
import { Avatar } from '@/components/design/Brand';
import { Screen, Card, ZItem, money, NText } from '@/components/design/ui';
import { Hero } from '@/components/design/widgets';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { clearSession, getSessionGeneration, getToken } from '@/lib/secureStore';

const RowBadge = ({ label, hot }: { label: string; hot?: boolean }) => {
  const { c, theme } = useTheme();
  return (
    <View style={{ paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, backgroundColor: hot ? c.red : c.amber }}>
      <NText style={{ fontSize: 10, fontFamily: font.bold, color: theme === 'dark' ? c.bg : '#fff' }}>{label}</NText>
    </View>
  );
};

const Me = () => {
  const { c } = useTheme();
  const { totalBalance, firstName, avatar, showBal, setShowBal, balanceLoaded, balanceError, hydrated, reload: reloadWallet } = useWallet();
  const [tier, setTier] = useState<number | null>(null);
  const [tierLoading, setTierLoading] = useState(true);
  const [tierError, setTierError] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const logoutLock = useRef(false);
  const mounted = useRef(true);
  const screenGeneration = useRef(getSessionGeneration()).current;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // Reflect the real KYC tier (was hardcoded "Tier 3"); refresh on focus so it
  // updates after the user completes a KYC step.
  useFocusEffect(
    useCallback(() => {
      let active = true;
      setTierLoading(true);
      reloadWallet(); // keep the balance shown here in sync
      (async () => {
        try {
          const t = await getToken();
          if (!t) throw new Error('No session');
          const res = await kycService.getStatus();
          const nextTier = Number(res?.tier);
          if (res?.success !== true || res.tier == null || !Number.isInteger(nextTier) || nextTier < 0 || nextTier > 3) throw new Error('Tier unavailable');
          if (active) { setTier(nextTier); setTierError(false); }
        } catch {
          if (active) setTierError(true);
        } finally {
          if (active) setTierLoading(false);
        }
      })();
      return () => { active = false; };
    }, [reloadWallet])
  );

  const handleLogout = async () => {
    let logoutGeneration = screenGeneration;
    const current = () => mounted.current && logoutGeneration === getSessionGeneration();
    if (logoutLock.current || !current()) return;
    logoutLock.current = true;
    setLoggingOut(true);
    // Revoke the token server-side first so a leaked copy can't be replayed;
    // best-effort — a network error must not block signing out locally.
    try {
      try { await apiPost(EP.auth.logout, {}, 5000); } catch { /* fall through to local clear */ }
      if (!current()) return;
      // clearSession advances the generation synchronously. Track that specific
      // cleared session so a later login during storage cleanup is not redirected.
      const clearing = clearSession();
      logoutGeneration = getSessionGeneration();
      await clearing;
      if (current()) router.replace('/signin');
    } catch {
      if (current()) notify('Could not finish signing out', 'Please try again to clear this device session.');
    } finally {
      logoutLock.current = false;
      if (mounted.current) setLoggingOut(false);
    }
  };

  const chev = <ZIcon name="right" size={18} color={c.ink3} />;
  const grp1: any[] = [
    { icon: 'user', title: 'Account Details', sub: 'Name, email, phone & photo', go: () => router.push('/accountdetails') },
    { icon: 'insurance', title: 'Identity Verification', sub: 'BVN, NIN and face check · raise limits', badge: 'Verify', go: () => router.push('/kyc') },
    { icon: 'history', title: 'Transaction History', go: () => router.push('/history') },
    { icon: 'chart', title: 'Account Limits', sub: 'Your tier and transaction limits', go: () => router.push('/accountlimits') },
    { icon: 'bank', title: 'Linked bank accounts', sub: 'View and connect bank accounts', go: () => router.push('/wallet') },
    { icon: 'loan', title: 'Buy Now, Pay Later', sub: 'Check availability', go: () => router.push('/bnpl') },
  ];
  const grp2: any[] = [
    // Transaction PIN lives in Security Center (with the password and biometrics),
    // so it isn't listed again here (17 Nov 2026).
    { icon: 'insurance', title: 'Security Center', sub: 'Password, transaction PIN and biometrics', go: () => router.push('/securitysetup') },
    { icon: 'help', title: 'Customer Service Center', go: () => router.push('/support') },
    { icon: 'gift', title: 'Invitation', sub: 'Invite friends to Zitch', go: () => router.push('/invite') },
  ];

  // The menu sits straight on the page, without white cards (review, 17 Nov
  // 2026); the groups stay apart by their spacing. The WhatsApp card and the
  // safety tips banner keep their cards: they are calls to action, not lists.
  const Group = ({ items }: { items: any[] }) => (
    <View style={{ marginHorizontal: 16, marginTop: 14, paddingVertical: 2 }}>
      <View style={{ paddingHorizontal: 16 }}>
        {items.map((r, i) => (
          <ZItem
            key={r.title}
            icon={r.icon}
            title={r.title}
            sub={r.sub}
            onPress={r.go}
            last={i === items.length - 1}
            bare
            right={
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                {r.badge && <RowBadge label={r.badge} hot={r.hot} />}
                {chev}
              </View>
            }
          />
        ))}
      </View>
    </View>
  );

  return (
    <Screen pad={false} tab>
      {/* header */}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 18, paddingTop: 6 }}>
        <Avatar size={50} ring={c.brand} surface={c.surface} uri={avatar} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text numberOfLines={2} style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1 }}>Hi, {firstName || 'there'}</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 4, paddingHorizontal: 9, paddingVertical: 3, borderRadius: 999, backgroundColor: 'rgba(245,166,35,.16)', alignSelf: 'flex-start' }}>
            <ZIcon name={tier != null && tier > 0 && !tierError ? 'check' : 'insurance'} size={11} color={c.amber} stroke={2.6} />
            <Text style={{ color: c.amber, fontSize: 11.5, fontFamily: font.bold }}>{tier != null ? `Tier ${tier}${tierError ? ' · last known' : ''}` : tierLoading ? 'Checking tier…' : 'Tier unavailable'}</Text>
          </View>
        </View>
        <Pressable accessibilityRole="button" accessibilityLabel="Settings" onPress={() => router.push('/settings')} style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="settings" size={20} color={c.ink1} />
        </Pressable>
      </View>

      {/* balance */}
      <View style={{ paddingHorizontal: 20, paddingTop: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7 }}>
          <Text style={{ color: c.ink3, fontSize: 13, fontFamily: font.regular }}>Total balance</Text>
          <Pressable accessibilityRole="button" accessibilityLabel={showBal ? 'Hide balance' : 'Show balance'} hitSlop={12} onPress={() => setShowBal(!showBal)}>
            <ZIcon name={showBal ? 'eye' : 'eyeoff'} size={15} color={c.ink3} />
          </Pressable>
        </View>
        <NText numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.65} style={{ fontSize: 30, fontFamily: font.extrabold, color: c.ink1, marginTop: 2, fontVariant: ['tabular-nums'] }}>
          {balanceLoaded === false ? (hydrated ? 'Unavailable' : 'Loading…') : showBal ? money(totalBalance) : '₦ ••••••'}
        </NText>
        {balanceError ? <Text accessibilityRole="alert" style={{ color: c.amber, marginTop: 8 }}>{balanceLoaded ? 'Showing your last known balance. ' : ''}{balanceError}</Text> : null}
      </View>

      {/* Bank on WhatsApp — design v2 places this card near the top of Me */}
      <Pressable accessibilityRole="button" accessibilityLabel="Bank on WhatsApp" onPress={() => router.push('/linkwhatsapp')} style={{ marginHorizontal: 16, marginTop: 14 }}>
        <Card style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12 }}>
          <View style={{ width: 42, height: 42, borderRadius: 13, backgroundColor: '#25D366', alignItems: 'center', justifyContent: 'center' }}>
            <WhatsAppGlyph size={24} color="#fff" />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 14.5, fontFamily: font.bold, color: c.ink1 }}>Bank on WhatsApp</Text>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>Balance, transfers & bills in your chats</Text>
          </View>
          {chev}
        </Card>
      </Pressable>

      {/* safety tips */}
      <Pressable accessibilityRole="button" accessibilityLabel="View safety tips" onPress={() => router.push('/safetytips')} style={{ marginHorizontal: 16, marginTop: 12 }}>
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

      {/* Face ID and Dark mode live in one place each (18 Nov 2026): biometrics in
          Security Center, the theme in Settings. Me links to both and repeats neither. */}

      {/* logout */}
      <View style={{ paddingHorizontal: 16, paddingTop: 16 }}>
        <Pressable accessibilityRole="button" accessibilityLabel="Log out" disabled={loggingOut} onPress={handleLogout} style={{ paddingVertical: 14, borderRadius: 16, backgroundColor: 'rgba(255,59,59,.1)', alignItems: 'center' }}>
          <Text style={{ color: c.red, fontFamily: font.bold }}>{loggingOut ? 'Logging out…' : 'Log out'}</Text>
        </Pressable>
      </View>
    </Screen>
  );
};

export default Me;
