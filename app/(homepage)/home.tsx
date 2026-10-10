import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { router, useFocusEffect } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Avatar } from '@/components/design/Brand';
import { Screen, Card, Sheet, TxnRow, money, NText } from '@/components/design/ui';
import { Hero, SectionLabel, ServiceTile } from '@/components/design/widgets';
import SmartPaste from '@/components/design/SmartPaste';
import { useTheme, font } from '@/lib/theme';
import { transactionParams, useWallet } from '@/lib/wallet';
import { notify } from '@/components/design/Notify';
import { walletCapabilityMessage } from '@/lib/services/wallet';

const GRID = [
  { label: 'Airtime', icon: 'airtime', go: () => router.push('/buyairtime') },
  { label: 'Data', icon: 'data', go: () => router.push('/buydata') },
  { label: 'Betting', icon: 'dice', go: () => router.push('/betting') },
  { label: 'Cable TV', icon: 'tv', go: () => router.push('/buycable') },
  { label: 'Save', icon: 'fixed', go: () => router.push('/savings') },
  { label: 'Electricity', icon: 'bills', go: () => router.push('/buyelectricity') },
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
  const { c } = useTheme();
  const { balance, totalBalance, historicalBalance, fundingProvider, firstName, fullName, avatar, accountNumber, bankName, billPaymentsAvailable, transfersAvailable, fundingMessage, txns, showBal, setShowBal, reload, linked, reloadLinked, hydrated, balanceLoaded, balanceError, historyError } = useWallet();
  const capabilityMessage = walletCapabilityMessage({ billPaymentsAvailable, transfersAvailable });
  const [more, setMore] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (copiedTimer.current) clearTimeout(copiedTimer.current); }, []);
  const linkedCount = linked.length;

  useFocusEffect(useCallback(() => { void reload(); void reloadLinked(); }, [reload, reloadLinked]));

  // Pull-to-refresh: re-fetch balance + activity (e.g. after a bank-transfer
  // top-up the webhook just credited).
  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try { await Promise.all([reload(), reloadLinked()]); } finally { setRefreshing(false); }
  }, [reload, reloadLinked]);

  // Copy ONLY the bare 10-digit account number (not the "· bank" suffix) and pop
  // a small local "copied" bubble above the chip for ~1.3s, per the v2 design —
  // not a global toast.
  const copyAccount = async () => {
    if (!accountNumber) return;
    try {
      await Clipboard.setStringAsync(accountNumber);
      setCopied(true);
      if (copiedTimer.current) clearTimeout(copiedTimer.current);
      copiedTimer.current = setTimeout(() => setCopied(false), 1300);
    } catch { notify('Could not copy', 'Please try again.'); }
  };

  // NUBAN account numbers display grouped 4-3-3 ("9012 345 678").
  const groupedAccount = accountNumber.replace(/^(\d{4})(\d{3})(\d{3}).*$/, '$1 $2 $3');

  return (
    <Screen pad={false} tab onRefresh={onRefresh} refreshing={refreshing}>
      {/* header */}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 11, paddingHorizontal: 18, paddingTop: 4 }}>
        <Pressable accessibilityRole="button" accessibilityLabel="Your profile" onPress={() => router.push('/me')}>
          <Avatar size={38} ring={c.brand} surface={c.surface} uri={avatar} />
        </Pressable>
        <Text style={{ flex: 1, fontSize: 18, fontFamily: font.extrabold, color: c.ink1 }}>
          Hi, {firstName || 'there'}
        </Text>
        <View style={{ flexDirection: 'row', gap: 16, alignItems: 'center' }}>
          <Pressable accessibilityRole="button" accessibilityLabel="Help and support" hitSlop={10} onPress={() => router.push('/support')}><ZIcon name="help" size={24} color={c.ink1} /></Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Scan a QR code" hitSlop={10} onPress={() => router.push('/scan')}><ZIcon name="scan" size={24} color={c.ink1} /></Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Notifications" hitSlop={10} onPress={() => router.push('/notifications')}>
            <ZIcon name="bell" size={24} color={c.ink1} />
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
            <Text style={{ color: 'rgba(255,255,255,.88)', fontSize: 13, fontFamily: font.medium }}>{fundingProvider === 'wema_vas' ? 'Available for bills' : 'Available Balance'}</Text>
          </View>
          <Pressable onPress={() => router.push('/history')} style={{ flexDirection: 'row', alignItems: 'center', gap: 3 }}>
            <Text style={{ color: '#fff', fontSize: 12.5, fontFamily: font.semibold }}>Transaction History</Text>
            <ZIcon name="right" size={15} color="#fff" />
          </Pressable>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 9 }}>
          <NText style={{ color: '#fff', fontSize: 32, fontFamily: font.extrabold, fontVariant: ['tabular-nums'] }}>
            {!balanceLoaded ? (hydrated ? 'Unavailable' : 'Loading…') : showBal ? money(balance) : '₦ ••••••'}
          </NText>
          <Pressable accessibilityRole="button" accessibilityLabel={showBal ? 'Hide balance' : 'Show balance'} hitSlop={10} onPress={() => setShowBal(!showBal)}>
            <ZIcon name={showBal ? 'eye' : 'eyeoff'} size={17} color="rgba(255,255,255,.85)" />
          </Pressable>
        </View>
        {balanceLoaded && (fundingProvider === 'wema_vas' || totalBalance > balance) ? (
          <View style={{ gap: 3, marginTop: 10 }}>
            <NText style={{ color: 'rgba(255,255,255,.88)', fontSize: 12, fontFamily: font.medium }}>Total balance: {showBal ? money(totalBalance) : '₦ ••••••'}</NText>
            <NText style={{ color: 'rgba(255,255,255,.88)', fontSize: 12, fontFamily: font.medium }}>{fundingProvider === 'wema_vas' ? 'Historical funds' : 'Funds under review'}: {showBal ? money(fundingProvider === 'wema_vas' ? historicalBalance : totalBalance - balance) : '₦ ••••••'}</NText>
            {fundingProvider === 'wema_vas' && historicalBalance > 0 ? <Text style={{ color: 'rgba(255,255,255,.82)', fontSize: 11.5, fontFamily: font.regular }}>Historical funds are not available for new payments.</Text> : null}
          </View>
        ) : null}
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 12, gap: 10 }}>
          {/* The dedicated (Monnify reserved) account number, shown only once the
              wallet is provisioned with one — never a hardcoded placeholder (it
              could be mistaken for a real account and shared). Tap to copy. */}
          {accountNumber ? (
            <View style={{ flex: 1 }}>
              {/* local "copied" confirmation bubble — sits just above the chip */}
              {copied && (
                <View style={{ position: 'absolute', top: -30, left: 0, flexDirection: 'row', alignItems: 'center', gap: 5, paddingVertical: 5, paddingHorizontal: 10, borderRadius: 999, backgroundColor: c.ink1 }}>
                  <ZIcon name="check" size={12} color={c.cyan} stroke={2.6} />
                  <Text style={{ color: '#fff', fontSize: 11.5, fontFamily: font.semibold }}>Account number copied</Text>
                </View>
              )}
              <Pressable
                onPress={copyAccount}
                style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 7, paddingHorizontal: 12, borderRadius: 16, backgroundColor: 'rgba(255,255,255,.16)' }}
              >
                <View style={{ flex: 1 }}>
                  <Text numberOfLines={1} style={{ color: '#fff', fontSize: 12.5, fontFamily: font.bold }}>
                    {fullName || firstName || 'Your account'}
                  </Text>
                  <NText numberOfLines={1} style={{ color: 'rgba(255,255,255,.82)', fontSize: 11.5, fontFamily: font.medium, marginTop: 1 }}>
                    {groupedAccount}{bankName ? ` · ${bankName}` : ''}
                  </NText>
                </View>
                <ZIcon name="copy" size={15} color="rgba(255,255,255,.85)" />
              </Pressable>
            </View>
          ) : (
            <View style={{ flex: 1 }} />
          )}
          <Pressable onPress={() => router.push('/addmoney')} style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8, paddingHorizontal: 14, borderRadius: 999, backgroundColor: '#fff' }}>
            <ZIcon name="plus" size={15} color={c.brandDeep} stroke={2.4} />
            <Text style={{ color: c.brandDeep, fontSize: 13, fontFamily: font.bold }}>Add Money</Text>
          </Pressable>
        </View>
      </Hero>

      {balanceError ? <Text accessibilityRole="alert" style={{ color: c.amber, marginHorizontal: 16, marginBottom: 12, fontFamily: font.medium }}>{balanceLoaded ? 'Showing your last known balance. ' : ''}{balanceError} Pull down to retry.</Text> : null}

      {balanceLoaded && capabilityMessage ? (
        <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 20, marginHorizontal: 16, marginBottom: 16 }}>
          {capabilityMessage} {fundingMessage}
        </Text>
      ) : null}

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
              <ServiceTile icon={s.icon} label={s.label} onPress={() => (s.more ? setMore(true) : s.go && s.go())} />
            </View>
          ))}
        </View>
      </Card>

      {/* linked banks summary — live count of Mono-connected accounts; tap to the
          wallet (where they're managed) or the link flow when none are connected */}
      <Pressable
        onPress={() => router.push(linkedCount && linkedCount > 0 ? '/wallet' : '/linkbank')}
        style={{ marginHorizontal: 16, marginTop: 14 }}
      >
        <Card style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <View style={{ width: 42, height: 42, borderRadius: 13, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="bank" size={22} color={c.brand} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>Linked banks</Text>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular, marginTop: 1 }}>
              {linkedCount && linkedCount > 0
                ? `${linkedCount} ${linkedCount === 1 ? 'account' : 'accounts'} connected`
                : 'Connect a bank to see all your balances'}
            </Text>
          </View>
          <ZIcon name="right" size={18} color={c.ink3} />
        </Card>
      </Pressable>

      {/* recent */}
      <View style={{ paddingHorizontal: 18, paddingTop: 22 }}>
        <SectionLabel action="See all" onAction={() => router.push('/history')}>Recent activity</SectionLabel>
        {txns.length === 0 ? (
          <Text accessibilityRole={historyError ? 'alert' : undefined} style={{ color: c.ink3, fontFamily: font.regular, paddingVertical: 8 }}>{!hydrated ? 'Loading transactions…' : historyError || 'No transactions yet'}</Text>
        ) : (
          txns.slice(0, 4).map((x, i) => <TxnRow key={x.id} txn={x} last={i === Math.min(3, txns.length - 1)} onSelect={(txn) => router.push({ pathname: '/txndetail', params: transactionParams(txn) })} />)
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
