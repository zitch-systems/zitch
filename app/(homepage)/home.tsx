import React, { useCallback, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { router, useFocusEffect } from 'expo-router';
import { notify } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { Avatar } from '@/components/design/Brand';
import { Screen, Card, Sheet, TxnRow, money, NText } from '@/components/design/ui';
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

const Home = () => {
  const { c, theme } = useTheme();
  const { balance, firstName, avatar, accountNumber, bankName, txns, showBal, setShowBal, reload } = useWallet();
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

  return (
    <Screen pad={false} tab onRefresh={onRefresh} refreshing={refreshing}>
      {/* header */}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 11, paddingHorizontal: 18, paddingTop: 4 }}>
        <Pressable onPress={() => router.push('/me')}>
          <Avatar size={38} ring={c.brand} surface={c.surface} uri={avatar} />
        </Pressable>
        <Text style={{ flex: 1, fontSize: 18, fontFamily: font.extrabold, color: c.ink1 }}>
          Hi, {firstName || 'there'}
        </Text>
        <View style={{ flexDirection: 'row', gap: 16, alignItems: 'center' }}>
          <Pressable onPress={() => router.push('/support')}><ZIcon name="help" size={24} color={c.ink1} /></Pressable>
          <Pressable onPress={() => router.push('/scan')}><ZIcon name="scan" size={24} color={c.ink1} /></Pressable>
          <Pressable onPress={() => router.push('/notifications')}>
            <View>
              <ZIcon name="bell" size={24} color={c.ink1} />
              <View style={{ position: 'absolute', top: -6, right: -7, minWidth: 16, height: 16, paddingHorizontal: 4, borderRadius: 9, backgroundColor: c.red, alignItems: 'center', justifyContent: 'center' }}>
                <Text style={{ color: '#fff', fontSize: 10, fontFamily: font.bold }}>24</Text>
              </View>
            </View>
          </Pressable>
        </View>
      </View>

      {/* balance hero */}
      <Hero style={{ margin: 16 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7 }}>
            <View style={{ width: 17, height: 17, borderRadius: 9, backgroundColor: 'rgba(255,255,255,.22)', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name="check" size={11} color="#fff" stroke={2.6} />
            </View>
            <Text style={{ color: 'rgba(255,255,255,.88)', fontSize: 13, fontFamily: font.medium }}>Available Balance</Text>
          </View>
          <Pressable onPress={() => router.push('/history')} style={{ flexDirection: 'row', alignItems: 'center', gap: 3 }}>
            <Text style={{ color: '#fff', fontSize: 12.5, fontFamily: font.semibold }}>Transaction History</Text>
            <ZIcon name="right" size={15} color="#fff" />
          </Pressable>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 9 }}>
          <NText style={{ color: '#fff', fontSize: 32, fontFamily: font.extrabold, fontVariant: ['tabular-nums'] }}>
            {showBal ? money(balance) : '₦ ••••••'}
          </NText>
          <Pressable onPress={() => setShowBal(!showBal)}>
            <ZIcon name={showBal ? 'eye' : 'eyeoff'} size={17} color="rgba(255,255,255,.85)" />
          </Pressable>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 12, gap: 10 }}>
          {/* The dedicated (Monnify reserved) account number, shown only once the
              wallet is provisioned with one — never a hardcoded placeholder (it
              could be mistaken for a real account and shared). Tap to copy. */}
          {accountNumber ? (
            <Pressable
              onPress={copyAccount}
              style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8, paddingHorizontal: 12, borderRadius: 999, backgroundColor: 'rgba(255,255,255,.16)' }}
            >
              <ZIcon name="bank" size={14} color="#fff" />
              <NText numberOfLines={1} style={{ flex: 1, color: '#fff', fontSize: 12.5, fontFamily: font.bold }}>
                {accountNumber}{bankName ? ` · ${bankName}` : ''}
              </NText>
              <ZIcon name="copy" size={14} color="rgba(255,255,255,.85)" />
            </Pressable>
          ) : (
            <View style={{ flex: 1 }} />
          )}
          <Pressable onPress={() => router.push('/addmoney')} style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8, paddingHorizontal: 14, borderRadius: 999, backgroundColor: '#fff' }}>
            <ZIcon name="plus" size={15} color={c.brandDeep} stroke={2.4} />
            <Text style={{ color: c.brandDeep, fontSize: 13, fontFamily: font.bold }}>Add Money</Text>
          </Pressable>
        </View>
      </Hero>

      {/* daily interest strip */}
      <Pressable onPress={() => router.push('/savings')} style={{ marginHorizontal: 16, marginTop: -4 }}>
        <Card pad={0} style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 11, paddingHorizontal: 14, borderRadius: 16 }}>
          <View style={{ width: 26, height: 26, borderRadius: 8, backgroundColor: 'rgba(0,181,29,.14)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="spark" size={16} color={c.lime} />
          </View>
          <Text style={{ flex: 1, fontSize: 12.5, color: c.ink2, fontFamily: font.regular }}>
            Act now — start earning <Text style={{ color: c.brand, fontFamily: font.bold }}>daily interest</Text>
          </Text>
          <ZIcon name="right" size={16} color={c.ink3} />
        </Card>
      </Pressable>

      {/* quick actions */}
      <Card style={{ margin: 16, marginBottom: 0, flexDirection: 'row', justifyContent: 'space-around', paddingVertical: 16 }}>
        {[
          { icon: 'send', label: 'Transfer', go: () => router.push('/sendmoney') },
          { icon: 'airtime', label: 'Airtime', go: () => router.push('/buyairtime') },
          { icon: 'withdraw', label: 'Withdraw', go: () => router.push('/sendmoney') },
        ].map((q) => (
          <ServiceTile key={q.label} icon={q.icon} label={q.label} onPress={q.go} round />
        ))}
      </Card>

      {/* services grid */}
      <Card style={{ margin: 16, marginBottom: 0, paddingVertical: 20, paddingHorizontal: 8 }}>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
          {GRID.map((s) => (
            <View key={s.label} style={{ width: '25%', alignItems: 'center', marginBottom: 18 }}>
              <ServiceTile icon={s.icon} label={s.label} badge={s.badge} hot={s.hot} onPress={() => (s.more ? setMore(true) : s.go && s.go())} />
            </View>
          ))}
        </View>
      </Card>

      {/* promo */}
      <Pressable onPress={() => router.push('/savings')} style={{ marginHorizontal: 16, marginTop: 14 }}>
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
          txns.slice(0, 4).map((x, i) => <TxnRow key={x.id} txn={x} last={i === Math.min(3, txns.length - 1)} />)
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
