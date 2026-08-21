import React, { useCallback, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { router, useFocusEffect } from 'expo-router';
import { notify } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Sheet, TxnRow, money, NText } from '@/components/design/ui';
import { Hero, SectionLabel, ServiceTile } from '@/components/design/widgets';
import SmartPaste from '@/components/design/SmartPaste';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const GRID = [
  { label: 'Airtime', icon: 'airtime', badge: '6% off', go: () => router.push('/buyairtime') },
  { label: 'Data', icon: 'data', go: () => router.push('/buydata') },
  { label: 'Betting', icon: 'dice', go: () => router.push('/betting') },
  { label: 'Cable TV', icon: 'tv', go: () => router.push('/buycable') },
  { label: 'Save', icon: 'fixed', go: () => router.push('/savings') },
  { label: 'Loan', icon: 'loan', badge: 'Hot', hot: true, go: () => router.push('/getloan') },
  { label: 'Exams', icon: 'jamb', go: () => router.push('/exams') },
  { label: 'More', icon: 'more', more: true },
];

const MORE = [
  { label: 'Electricity', icon: 'bills', go: () => router.push('/buyelectricity') },
  { label: 'Send money', icon: 'send', go: () => router.push('/sendmoney') },
  { label: 'Airtime', icon: 'airtime', go: () => router.push('/buyairtime') },
  { label: 'Data', icon: 'data', go: () => router.push('/buydata') },
  { label: 'Cable TV', icon: 'tv', go: () => router.push('/buycable') },
  { label: 'Betting', icon: 'dice', go: () => router.push('/betting') },
  { label: 'Exams', icon: 'jamb', go: () => router.push('/exams') },
  { label: 'Insurance', icon: 'insurance', go: () => router.push('/insurance') },
  { label: 'Remita', icon: 'remita', go: () => router.push('/remita') },
  { label: 'Movie', icon: 'movie', go: () => router.push('/movies') },
  { label: 'Convert', icon: 'convert', go: () => router.push('/convert') },
  { label: 'Invite', icon: 'invite', go: () => router.push('/invite') },
];

// The four things people open the app to do. Deliberately not the services grid
// below: these are account actions, and separating them is what lets the grid be
// scanned as a list of bills rather than as fourteen equally-weighted icons.
const ACTIONS = [
  { label: 'Transfer', icon: 'send', go: () => router.push('/sendmoney') },
  { label: 'Add Money', icon: 'plus', go: () => router.push('/addmoney') },
  { label: 'Statement', icon: 'download', go: () => router.push('/history') },
  { label: 'History', icon: 'chart', go: () => router.push('/history') },
];

/** "Good morning" / "afternoon" / "evening", by the phone's own clock. */
const greeting = () => {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
};

/** Up to two initials from whatever name we hold. */
const initialsOf = (full: string, fallback: string) => {
  const words = (full || fallback || '').trim().split(/\s+/).filter(Boolean);
  const letters = words.slice(0, 2).map((w) => w[0]).join('');
  return letters.toUpperCase() || 'ZT';
};

const Home = () => {
  const { c } = useTheme();
  const { balance, firstName, accountName, accountNumber, bankName, txns, showBal, setShowBal, reload } = useWallet();
  const [more, setMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  // Refresh balance & activity whenever Home regains focus — after sign-in and
  // after returning from a transfer/purchase — so the dashboard never shows a
  // stale figure.
  useFocusEffect(useCallback(() => { reload(); }, [reload]));

  // Pull-to-refresh: re-fetch balance + activity (e.g. after a bank-transfer
  // top-up the webhook just credited).
  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try { await reload(); } finally { setRefreshing(false); }
  }, [reload]);

  const copyAccount = async () => {
    if (!accountNumber) return;
    await Clipboard.setStringAsync(accountNumber);
    notify('Copied', 'Account number copied to clipboard');
  };

  // The balance is split so the kobo can be set smaller, as on the mockup. Split
  // on the LAST dot rather than parsed as a number: money() has already done the
  // grouping and the currency mark, and re-deriving them here would be a second
  // opinion on how naira are written.
  const shown = money(balance);
  const dot = shown.lastIndexOf('.');
  const [whole, kobo] = dot > -1 ? [shown.slice(0, dot), shown.slice(dot)] : [shown, ''];
  const name = accountName || firstName || 'there';

  // Everything down to the quick actions is pinned — never part of the scroll —
  // so the identity, the balance and the four account actions stay on screen
  // while "Pay a bill" and everything after it scrolls underneath. Built as its
  // own block and handed to Screen's `header` prop rather than left inline, so
  // it renders once as the ScrollView's sibling instead of its first child.
  const fixed = (
    <>
      {/* header */}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 18, paddingTop: 4 }}>
        <Pressable onPress={() => router.push('/me')} accessibilityRole="button" accessibilityLabel="Your profile">
          {/* Initials rather than the illustration: a monogram reads as "this is
              your account" at a glance, and it cannot be mistaken for a photo the
              customer never set. */}
          <View style={{
            width: 44, height: 44, borderRadius: 14, borderWidth: 1.5, borderColor: c.brand,
            backgroundColor: c.brand + '1A', alignItems: 'center', justifyContent: 'center',
          }}>
            <Text style={{ color: c.brand, fontFamily: font.extrabold, fontSize: 15 }}>
              {initialsOf(accountName, firstName)}
            </Text>
          </View>
        </Pressable>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={{ fontSize: 13, fontFamily: font.regular, color: c.ink3 }}>{greeting()} 👋</Text>
          <Text numberOfLines={1} style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1, marginTop: 1 }}>
            {name}
          </Text>
        </View>
        <Pressable
          onPress={() => router.push('/scan')}
          hitSlop={8}
          accessibilityRole="button"
          accessibilityLabel="Scan a code"
          style={{ width: 38, height: 38, borderRadius: 12, borderWidth: 1, borderColor: c.line, alignItems: 'center', justifyContent: 'center' }}
        >
          <ZIcon name="scan" size={19} color={c.ink1} />
        </Pressable>
        <Pressable
          onPress={() => router.push('/notifications')}
          hitSlop={8}
          accessibilityRole="button"
          accessibilityLabel="Notifications"
          style={{ width: 38, height: 38, borderRadius: 12, borderWidth: 1, borderColor: c.line, alignItems: 'center', justifyContent: 'center' }}
        >
          <ZIcon name="bell" size={19} color={c.ink1} />
          {/* A dot, not a count. The app has no notifications-count source, so a
              number here would be invented — and a wrong number is worse than no
              number on a screen about money. */}
          <View style={{
            position: 'absolute', top: 7, right: 8, width: 8, height: 8, borderRadius: 4,
            backgroundColor: c.red, borderWidth: 1.5, borderColor: c.bg,
          }} />
        </Pressable>
      </View>

      {/* balance hero */}
      <Hero style={{ margin: 16 }}>
        <View style={{ flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <Text style={{ color: 'rgba(255,255,255,.82)', fontSize: 11.5, fontFamily: font.bold, letterSpacing: 1.1 }}>
            AVAILABLE BALANCE
          </Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable
              onPress={() => setShowBal(!showBal)}
              hitSlop={10}
              accessibilityRole="button"
              accessibilityLabel={showBal ? 'Hide balance' : 'Show balance'}
              style={{ width: 28, height: 28, borderRadius: 9, backgroundColor: 'rgba(255,255,255,.18)', alignItems: 'center', justifyContent: 'center' }}
            >
              <ZIcon name={showBal ? 'eye' : 'eyeoff'} size={15} color="#fff" />
            </Pressable>
            <Pressable
              onPress={() => router.push('/analysis')}
              hitSlop={10}
              accessibilityRole="button"
              accessibilityLabel="Account insights"
              style={{ width: 28, height: 28, borderRadius: 9, backgroundColor: 'rgba(255,255,255,.18)', alignItems: 'center', justifyContent: 'center' }}
            >
              <ZIcon name="more" size={15} color="#fff" />
            </Pressable>
          </View>
        </View>

        <View style={{ flexDirection: 'row', alignItems: 'baseline', marginTop: 8 }}>
          <NText style={{ color: '#fff', fontSize: 28, fontFamily: font.extrabold, fontVariant: ['tabular-nums'] }}>
            {showBal ? whole : '₦ ••••••'}
          </NText>
          {showBal && kobo ? (
            <NText style={{ color: 'rgba(255,255,255,.7)', fontSize: 16, fontFamily: font.extrabold, fontVariant: ['tabular-nums'] }}>
              {kobo}
            </NText>
          ) : null}
        </View>

        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 14, gap: 10 }}>
          {/* The dedicated (Wema/ALAT) account number, shown only once the wallet
              is provisioned with one — never a hardcoded placeholder (it could be
              mistaken for a real account and shared). Tap to copy. */}
          {accountNumber ? (
            <Pressable
              onPress={copyAccount}
              accessibilityRole="button"
              accessibilityLabel={`Copy account number ${accountNumber}`}
              style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 7 }}
            >
              <ZIcon name="bank" size={14} color="rgba(255,255,255,.85)" />
              <NText numberOfLines={1} style={{ flexShrink: 1, color: '#fff', fontSize: 12.5, fontFamily: font.bold }}>
                {accountNumber}{bankName ? ` · ${bankName}` : ''}
              </NText>
              <ZIcon name="copy" size={13} color="rgba(255,255,255,.75)" />
            </Pressable>
          ) : (
            <View style={{ flex: 1 }} />
          )}
          <Pressable
            onPress={() => router.push('/wallet')}
            accessibilityRole="button"
            accessibilityLabel="Open your wallet"
            style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 9, paddingHorizontal: 15, borderRadius: 999, backgroundColor: '#fff' }}
          >
            <ZIcon name="wallet" size={15} color={c.brandDeep} stroke={2.2} />
            <Text style={{ color: c.brandDeep, fontSize: 13, fontFamily: font.bold }}>Wallet</Text>
          </Pressable>
        </View>
      </Hero>

      {/* quick actions */}
      <View style={{ flexDirection: 'row', gap: 10, paddingHorizontal: 16, paddingBottom: 14 }}>
        {ACTIONS.map((a) => (
          <Pressable
            key={a.label}
            onPress={a.go}
            accessibilityRole="button"
            accessibilityLabel={a.label}
            style={{
              flex: 1, alignItems: 'center', gap: 8, paddingVertical: 14,
              borderRadius: 18, borderWidth: 1, borderColor: c.line, backgroundColor: c.surface,
            }}
          >
            <ZIcon name={a.icon} size={21} color={c.brand} stroke={2} />
            <Text numberOfLines={1} style={{ fontSize: 11.5, fontFamily: font.semibold, color: c.ink2 }}>
              {a.label}
            </Text>
          </Pressable>
        ))}
      </View>
    </>
  );

  return (
    <Screen pad={false} tab onRefresh={onRefresh} refreshing={refreshing} header={fixed}>
      {/* services grid */}
      <View style={{ paddingHorizontal: 18, paddingTop: 24 }}>
        <SectionLabel action="See all" onAction={() => setMore(true)}>Pay a bill</SectionLabel>
      </View>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', paddingHorizontal: 8 }}>
        {GRID.map((s) => (
          <View key={s.label} style={{ width: '25%', alignItems: 'center', marginBottom: 18 }}>
            <ServiceTile icon={s.icon} label={s.label} badge={s.badge} hot={s.hot} onPress={() => (s.more ? setMore(true) : s.go && s.go())} />
          </View>
        ))}
      </View>

      {/* promo */}
      <Pressable onPress={() => router.push('/savings')} style={{ marginHorizontal: 16, marginTop: 2 }}>
        <View style={{ borderRadius: 20, padding: 16, flexDirection: 'row', alignItems: 'center', gap: 14, borderWidth: 1, borderColor: c.line, backgroundColor: c.surface2 }}>
          <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: 'rgba(15,162,149,.16)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="fixed" size={23} color={c.brand} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ fontFamily: font.bold, fontSize: 14, color: c.ink1 }}>Fixed Save · 22% p.a</Text>
            <Text style={{ fontSize: 12, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>Grow your savings, locked & safe</Text>
          </View>
          <View style={{ paddingVertical: 9, paddingHorizontal: 18, borderRadius: 999, backgroundColor: c.brand }}>
            <Text style={{ color: '#fff', fontSize: 13, fontFamily: font.bold }}>Save</Text>
          </View>
        </View>
      </Pressable>

      {/* recent */}
      <View style={{ paddingHorizontal: 18, paddingTop: 22 }}>
        <SectionLabel action="See all" onAction={() => router.push('/history')}>Recent activity</SectionLabel>
        {txns.length === 0 ? (
          <Text style={{ color: c.ink3, fontFamily: font.regular, paddingVertical: 8 }}>No transactions yet</Text>
        ) : (
          txns.slice(0, 4).map((x, i) => (
            <TxnRow
              key={x.id}
              txn={x}
              last={i === Math.min(3, txns.length - 1)}
              onPress={() => router.push({ pathname: '/txndetail', params: { type: x.type, amount: String(x.amount), status: x.status, dir: x.dir, detail: x.detail, reference: x.reference, icon: x.icon } })}
            />
          ))
        )}
      </View>

      {/* more services sheet */}
      <Sheet open={more} onClose={() => setMore(false)} title="All services">
        <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
          {MORE.map((s) => (
            <View key={s.label} style={{ width: '25%', alignItems: 'center', marginBottom: 18 }}>
              <ServiceTile icon={s.icon} label={s.label} onPress={() => { setMore(false); setTimeout(() => s.go(), 240); }} />
            </View>
          ))}
        </View>
      </Sheet>

      {/* smart paste-to-pay */}
      <SmartPaste />
    </Screen>
  );
};

export default Home;
