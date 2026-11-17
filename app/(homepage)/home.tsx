import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, Image } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { LinearGradient } from 'expo-linear-gradient';
import { router, useFocusEffect } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Btn, Sheet, money, NText, type Txn } from '@/components/design/ui';
import { ServiceTile } from '@/components/design/widgets';
import SmartPaste from '@/components/design/SmartPaste';
import { ZCoil } from '@/components/design/Loading';
import { useTabInk } from '@/components/design/BottomNav';
import { groupAccountNumber, whenLabel } from '@/lib/format';
import { useTheme, fontA, palette, ICON_COLORS, iconTint } from '@/lib/theme';
import { transactionParams, useWallet } from '@/lib/wallet';
import { transactionStatusPresentation } from '@/lib/transactionStatus';
import { notify } from '@/components/design/Notify';
import { walletCapabilityMessage } from '@/lib/services/wallet';
import { savingsService } from '@/lib/services/savings';
import { loansService } from '@/lib/services/loans';
import { DetailsReviewPrompt } from '@/components/kyc/DetailsReviewPrompt';

/**
 * Home, design A from the Live UI review: Sora for headings and figures, DM Sans
 * for text. Savings and loans appear only when the server says the product is
 * available; otherwise the Loan tile gives way to Electricity and there is no
 * promo card.
 */

type Tile = { label: string; icon: string; color: string; go?: () => void; more?: boolean; hot?: boolean };

const T = {
  airtime: { label: 'Airtime', icon: 'airtime', color: '#0FA295', go: () => router.push('/buyairtime') },
  data: { label: 'Data', icon: 'data', color: '#2D7FF9', go: () => router.push('/buydata') },
  betting: { label: 'Betting', icon: 'dice', color: '#F5A623', go: () => router.push('/betting') },
  cable: { label: 'Cable TV', icon: 'tv', color: '#7A5CFF', go: () => router.push('/buycable') },
  save: { label: 'Save', icon: 'piggy', color: '#00A01A', go: () => router.push('/savings') },
  electricity: { label: 'Electricity', icon: 'bills', color: '#E8590C', go: () => router.push('/buyelectricity') },
  loan: { label: 'Loan', icon: 'loan', color: '#E8590C', go: () => router.push('/getloan'), hot: true },
  exams: { label: 'Exams', icon: 'jamb', color: '#D6336C', go: () => router.push('/exams') },
  more: { label: 'More', icon: 'more', color: '#6E8B86', more: true },
} satisfies Record<string, Tile>;

// More (17 Nov 2026): a pop-up, not a page, with the money actions from the top
// of Home and every bill payment. Tapping one closes the pop-up and opens it.
type MoreItem = { label: string; icon: string; go: () => void; badge?: string };
export const MORE_MONEY: MoreItem[] = [
  { label: 'Transfer', icon: 'send', go: () => router.push('/sendmoney') },
  { label: 'History', icon: 'history', go: () => router.push('/history') },
  { label: 'Statement', icon: 'download', go: () => router.push('/statement') },
];
export const MORE_BILLS: MoreItem[] = [
  { label: 'Airtime', icon: 'airtime', go: () => router.push('/buyairtime') },
  { label: 'Data', icon: 'data', go: () => router.push('/buydata') },
  { label: 'Cable TV', icon: 'tv', go: () => router.push('/buycable') },
  { label: 'Electricity', icon: 'bills', go: () => router.push('/buyelectricity') },
  { label: 'Betting', icon: 'dice', go: () => router.push('/betting') },
  { label: 'Exams', icon: 'jamb', go: () => router.push('/exams') },
  { label: 'Water', icon: 'water', go: () => router.push('/water'), badge: 'Soon' },
  { label: 'Remita', icon: 'remita', go: () => router.push('/remita') },
  { label: 'Movies', icon: 'movie', go: () => router.push('/movies') },
  { label: 'Insurance', icon: 'insurance', go: () => router.push('/insurance') },
];

const greeting = (hour: number) => (hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening');

/** Bank account names arrive in capitals; show them the way people write them. */
const displayName = (name: string) => (name && name === name.toUpperCase()
  ? name.toLowerCase().replace(/(^|[\s'-])\p{L}/gu, (m) => m.toUpperCase())
  : name);

const initialsOf = (name: string) =>
  name.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase() || '').join('') || 'Z';

/** Savings and loans as the server reports them; both off until it says otherwise. */
export const useHomeProducts = () => {
  const [state, setState] = useState({ savings: false, loans: false, bestRate: '' });
  const load = useCallback(async () => {
    const safely = async <R,>(call: () => Promise<R>): Promise<R | null> => {
      try { return (await call()) ?? null; } catch { return null; }
    };
    const [savings, loans] = await Promise.all([
      safely(() => savingsService.getRates()),
      safely(() => loansService.getStatus()),
    ]);
    const rates = (savings?.rates || []).map((r) => Number(r.rate)).filter((r) => Number.isFinite(r) && r > 0);
    setState({
      savings: savings?.product_available === true,
      loans: loans?.product_available === true,
      // The server sends a decimal annual rate ("0.22"); show it as a percent, as Savings does.
      bestRate: rates.length ? String(Number((Math.max(...rates) * 100).toFixed(2))) : '',
    });
  }, []);
  return { ...state, load };
};

const IconButton = ({ icon, label, onPress, dot }: { icon: string; label: string; onPress: () => void; dot?: boolean }) => {
  const { c } = useTheme();
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={onPress} hitSlop={4}
      style={{ width: 42, height: 42, borderRadius: 14, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, alignItems: 'center', justifyContent: 'center' }}>
      <ZIcon name={icon} size={19} color={c.ink2} />
      {dot ? <View style={{ position: 'absolute', top: 7, right: 8, width: 8, height: 8, borderRadius: 4, backgroundColor: '#FF3B3B', borderWidth: 2, borderColor: c.surface }} /> : null}
    </Pressable>
  );
};

const QuickTile = ({ icon, label, color, onPress }: { icon: string; label: string; color: string; onPress: () => void }) => {
  const { c } = useTheme();
  const ink = useTabInk();
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={onPress}
      style={({ pressed }) => ({ flex: 1, minWidth: 0, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, borderRadius: 18,
        paddingTop: 14, paddingBottom: 11, paddingHorizontal: 4, alignItems: 'center', gap: 9, transform: [{ scale: pressed ? 0.96 : 1 }] })}>
      <ZIcon name={icon} size={24} color={color} stroke={2} />
      <Text numberOfLines={1} style={{ fontSize: 12, fontFamily: fontA.bodySemi, color: ink }}>{label}</Text>
    </Pressable>
  );
};

const BillTile = ({ tile, onPress }: { tile: Tile; onPress: () => void }) => {
  const { c, theme } = useTheme();
  const tint = tile.more ? c.surface3 : iconTint(tile.color, theme === 'dark');
  const ink = useTabInk();
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={tile.label} onPress={onPress}
      style={({ pressed }) => ({ width: '25%', alignItems: 'center', gap: 8, marginBottom: 12, transform: [{ scale: pressed ? 0.96 : 1 }] })}>
      <View style={{ width: 52, height: 52, borderRadius: 17, backgroundColor: tint, alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name={tile.icon} size={23} color={tile.more ? c.ink3 : tile.color} />
        {tile.hot ? (
          <View style={{ position: 'absolute', top: -7, right: -9, paddingHorizontal: 6, paddingVertical: 3, borderRadius: 999, backgroundColor: '#FF3B3B' }}>
            <Text style={{ fontSize: 9, fontFamily: fontA.display, color: '#fff', letterSpacing: 0.4 }}>HOT</Text>
          </View>
        ) : null}
      </View>
      <Text numberOfLines={1} style={{ fontSize: 12, fontFamily: fontA.bodySemi, color: ink, textAlign: 'center' }}>{tile.label}</Text>
    </Pressable>
  );
};

const Section = ({ title, action, onAction }: { title: string; action?: string; onAction?: () => void }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingTop: 22 }}>
      <Text accessibilityRole="header" style={{ fontSize: 15.5, fontFamily: fontA.display, color: c.ink1 }}>{title}</Text>
      {action ? (
        <Pressable accessibilityRole="button" accessibilityLabel={`${title}: ${action}`} hitSlop={10} onPress={onAction}>
          <Text style={{ fontSize: 12.5, fontFamily: fontA.bodySemi, color: c.brand }}>{action}</Text>
        </Pressable>
      ) : null}
    </View>
  );
};

/** A recent-activity row in design A: a rounded icon, the title and when, the amount and status. */
const ActivityRow = React.memo(({ txn, first, onSelect }: { txn: Txn; first: boolean; onSelect: (t: Txn) => void }) => {
  const { c, theme } = useTheme();
  const inflow = txn.dir === 'in';
  const color = inflow ? c.lime : (ICON_COLORS[txn.icon] ?? c.ink2);
  const tint = inflow ? 'rgba(0,181,29,.12)' : (ICON_COLORS[txn.icon] ? iconTint(ICON_COLORS[txn.icon], theme === 'dark') : c.surface3);
  const { state, label } = transactionStatusPresentation(txn.status);
  const tone = state === 'failed' ? c.red : state === 'pending' ? c.amber : c.ink3;
  const amount = (inflow ? '+' : '-') + money(Math.abs(txn.amount));
  // "Today · 9:14am", not the server's raw "2026-11-15 08:14".
  const when = txn.ts != null ? whenLabel(txn.ts) : txn.detail;
  return (
    <Pressable onPress={() => onSelect(txn)} accessibilityRole="button" accessibilityLabel={`${txn.type}, ${amount}, ${label}, ${when}`}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 13, borderTopWidth: first ? 0 : 1, borderTopColor: c.line }}>
      <View style={{ width: 38, height: 38, borderRadius: 12, backgroundColor: tint, alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name={txn.icon} size={17} color={color} stroke={2} />
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text numberOfLines={1} style={{ fontSize: 13.5, fontFamily: fontA.bodySemi, color: c.ink1 }}>{txn.type}</Text>
        <Text numberOfLines={1} style={{ fontSize: 11, fontFamily: fontA.body, color: c.ink3, marginTop: 2 }}>{when}</Text>
      </View>
      <View style={{ alignItems: 'flex-end', maxWidth: '45%' }}>
        <NText numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.75}
          style={{ fontSize: 13.5, fontFamily: fontA.display, color: inflow ? c.lime : c.ink1, fontVariant: ['tabular-nums'] }}>{amount}</NText>
        <Text style={{ fontSize: 10.5, fontFamily: fontA.body, color: tone, marginTop: 2 }}>{label}</Text>
      </View>
      <ZIcon name="right" size={15} color={c.ink3} />
    </Pressable>
  );
});
ActivityRow.displayName = 'ActivityRow';

const Home = () => {
  const { c } = useTheme();
  const { balance, totalBalance, firstName, fullName, avatar, accountNumber, bankName, billPaymentsAvailable, transfersAvailable, fundingMessage, txns, showBal, setShowBal, reload, reloadLinked, hydrated, balanceLoaded, balanceError, historyError } = useWallet();
  const capabilityMessage = walletCapabilityMessage({ billPaymentsAvailable, transfersAvailable });
  const products = useHomeProducts();
  const loadProducts = products.load;
  const [more, setMore] = useState(false);
  const [cardMenu, setCardMenu] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (copiedTimer.current) clearTimeout(copiedTimer.current); }, []);

  useFocusEffect(useCallback(() => { void reload(); void reloadLinked(); void loadProducts(); }, [reload, reloadLinked, loadProducts]));

  // Pull-to-refresh: re-fetch balance + activity (e.g. after a bank-transfer
  // top-up the webhook just credited).
  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try { await Promise.all([reload(), reloadLinked(), loadProducts()]); } finally { setRefreshing(false); }
  }, [reload, reloadLinked, loadProducts]);

  // Copy ONLY the bare 10-digit account number and pop a small local "copied"
  // bubble above the line for ~1.3s, not a global toast.
  const copyAccount = async () => {
    if (!accountNumber) return;
    try {
      await Clipboard.setStringAsync(accountNumber);
      setCopied(true);
      if (copiedTimer.current) clearTimeout(copiedTimer.current);
      copiedTimer.current = setTimeout(() => setCopied(false), 1300);
    } catch { notify('Could not copy', 'Please try again.'); }
  };

  // One grouping everywhere the number shows: "012 345 6789".
  const groupedAccount = groupAccountNumber(accountNumber);
  const name = displayName(fullName || firstName || '');
  // Kobo always shows, smaller, as in the design: ₦360,000.00
  const [whole, kobo] = ('₦' + Number(balance || 0).toLocaleString('en-NG', { minimumFractionDigits: 2, maximumFractionDigits: 2 })).split('.');
  const openTxn = useCallback((txn: Txn) => router.push({ pathname: '/txndetail', params: transactionParams(txn) }), []);

  const grid: Tile[] = [T.airtime, T.data, T.betting, T.cable, T.save, products.loans ? T.loan : T.electricity, T.exams, T.more];

  return (
    // The greeting, balance card and quick actions stay put (17 Nov 2026): only
    // what is below them scrolls, and a pull there refreshes with the coil.
    <Screen pad={false} tab onRefresh={onRefresh} refreshing={refreshing} header={(
      <>
        {/* header */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 11, paddingHorizontal: 20, paddingTop: 8 }}>
          <Pressable accessibilityRole="button" accessibilityLabel="Your profile" onPress={() => router.push('/me')}
            style={{ width: 44, height: 44, borderRadius: 15, overflow: 'hidden', backgroundColor: palette.teal50, borderWidth: 1.5, borderColor: '#8FDDD4', alignItems: 'center', justifyContent: 'center' }}>
            {avatar ? <Image source={{ uri: avatar }} style={{ width: '100%', height: '100%' }} resizeMode="cover" />
              : <Text style={{ fontSize: 16, fontFamily: fontA.display, color: palette.teal700 }}>{initialsOf(name)}</Text>}
          </Pressable>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={{ fontSize: 12.5, fontFamily: fontA.body, color: c.ink3 }}>{greeting(new Date().getHours())} 👋</Text>
            <Text numberOfLines={1} style={{ fontSize: 16.5, fontFamily: fontA.display, color: c.ink1, marginTop: 1 }}>{name || 'Welcome'}</Text>
          </View>
          <IconButton icon="scan" label="Scan a QR code" onPress={() => router.push('/scan')} />
          <IconButton icon="bell" label="Notifications" onPress={() => router.push('/notifications')} />
        </View>

        {/* balance card */}
        <LinearGradient
          colors={['#0FA295', '#00847B', '#043C37']}
          locations={[0, 0.52, 1]}
          start={{ x: 0.12, y: 0 }}
          end={{ x: 0.88, y: 1 }}
          style={{ marginHorizontal: 20, marginTop: 16, borderRadius: 26, paddingTop: 20, paddingHorizontal: 20, paddingBottom: 20, overflow: 'hidden' }}
        >
          <View pointerEvents="none" style={{ position: 'absolute', width: 230, height: 230, right: -70, top: -110, borderRadius: 115, backgroundColor: 'rgba(92,245,235,0.14)' }} />
          {/* the turning Zitch coil, as on the Wallet card: same size and strength */}
          <View pointerEvents="none" style={{ position: 'absolute', right: -18, bottom: -22, opacity: 0.18 }}>
            <ZCoil height={175} box={140} />
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ fontSize: 11, fontFamily: fontA.displaySemi, letterSpacing: 1.1, color: 'rgba(255,255,255,0.72)' }}>AVAILABLE BALANCE</Text>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <Pressable accessibilityRole="button" accessibilityLabel={showBal ? 'Hide balance' : 'Show balance'} onPress={() => setShowBal(!showBal)}
                style={{ width: 36, height: 30, borderRadius: 10, backgroundColor: 'rgba(255,255,255,0.16)', alignItems: 'center', justifyContent: 'center' }}>
                <ZIcon name={showBal ? 'eye' : 'eyeoff'} size={16} color="#fff" />
              </Pressable>
              <Pressable accessibilityRole="button" accessibilityLabel="Account options" onPress={() => setCardMenu(true)}
                style={{ width: 36, height: 30, borderRadius: 10, backgroundColor: 'rgba(255,255,255,0.16)', alignItems: 'center', justifyContent: 'center' }}>
                <ZIcon name="dots" size={16} color="#fff" />
              </Pressable>
            </View>
          </View>

          <NText numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.6}
            style={{ marginTop: 12, color: '#fff', fontSize: 38, letterSpacing: -1.1, fontFamily: fontA.displayX, fontVariant: ['tabular-nums'] }}>
            {!balanceLoaded ? (hydrated ? 'Unavailable' : 'Loading…') : showBal
              ? <>{whole}{kobo ? <Text style={{ fontSize: 21, fontFamily: fontA.display, color: 'rgba(255,255,255,0.66)' }}>.{kobo}</Text> : null}</>
              : '₦ ••••••'}
          </NText>
          {balanceLoaded && (totalBalance > balance) ? (
            <View style={{ gap: 3, marginTop: 10 }}>
              <NText style={{ color: 'rgba(255,255,255,.88)', fontSize: 12, fontFamily: fontA.bodyMed }}>Total balance: {showBal ? money(totalBalance) : '₦ ••••••'}</NText>
              <NText style={{ color: 'rgba(255,255,255,.88)', fontSize: 12, fontFamily: fontA.bodyMed }}>Funds under review: {showBal ? money(totalBalance - balance) : '₦ ••••••'}</NText>
            </View>
          ) : null}

          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 16 }}>
            {/* The dedicated funding account number, shown only once the wallet
                has one; never a placeholder that could be shared. Tap to copy. */}
            {accountNumber ? (
              <View style={{ flex: 1, minWidth: 0 }}>
                {copied && (
                  <View style={{ position: 'absolute', top: -30, left: 0, flexDirection: 'row', alignItems: 'center', gap: 5, paddingVertical: 5, paddingHorizontal: 10, borderRadius: 999, backgroundColor: c.ink1 }}>
                    <ZIcon name="check" size={12} color={c.cyan} stroke={2.6} />
                    <Text style={{ color: '#fff', fontSize: 11.5, fontFamily: fontA.bodySemi }}>Account number copied</Text>
                  </View>
                )}
                <Pressable accessibilityRole="button" accessibilityLabel={`Copy account number ${accountNumber}`} onPress={copyAccount} hitSlop={6}
                  style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <ZIcon name="bank" size={14} color="rgba(255,255,255,0.85)" />
                  <NText numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.85} style={{ flexShrink: 1, fontSize: 12.5, fontFamily: fontA.bodySemi, color: '#fff', fontVariant: ['tabular-nums'] }}>
                    {groupedAccount} · {bankName || 'Zitch account'}
                  </NText>
                  <ZIcon name="copy" size={14} color="rgba(255,255,255,0.85)" />
                </Pressable>
              </View>
            ) : <View style={{ flex: 1 }} />}
            <Pressable accessibilityRole="button" accessibilityLabel="Wallet" onPress={() => router.push('/wallet')}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 5, minHeight: 38, paddingHorizontal: 12, borderRadius: 999, backgroundColor: '#fff' }}>
              <ZIcon name="wallet" size={14} color={palette.teal700} />
              <Text style={{ fontSize: 13, fontFamily: fontA.display, color: palette.teal700 }}>Wallet</Text>
            </Pressable>
          </View>
        </LinearGradient>

        {balanceError ? <Text accessibilityRole="alert" style={{ color: c.amber, marginHorizontal: 20, marginTop: 12, fontFamily: fontA.bodyMed }}>{balanceLoaded ? 'Showing your last known balance. ' : ''}{balanceError} Pull down to retry.</Text> : null}
        {balanceError || historyError ? <View style={{ marginHorizontal: 20, marginTop: 12 }}><Btn label="Refresh account" disabled={refreshing} onPress={() => { void onRefresh(); }} /></View> : null}
        {balanceLoaded && capabilityMessage ? (
          <Text style={{ color: c.ink2, fontFamily: fontA.body, lineHeight: 20, marginHorizontal: 20, marginTop: 12 }}>
            {capabilityMessage} {fundingMessage}
          </Text>
        ) : null}

        {/* quick actions */}
        <View style={{ flexDirection: 'row', gap: 9, paddingHorizontal: 20, paddingTop: 14, paddingBottom: 6 }}>
          <QuickTile icon="convert" label="Transfer" color="#00A01A" onPress={() => router.push('/sendmoney')} />
          <QuickTile icon="plus" label="Add Money" color={palette.teal700} onPress={() => router.push('/addmoney')} />
          <QuickTile icon="download" label="Statement" color="#F5A623" onPress={() => router.push('/statement')} />
          <QuickTile icon="chart" label="History" color="#7A5CFF" onPress={() => router.push('/history')} />
        </View>

      </>
    )}>
      {/* Only a refusal of the details sign-up sent shows on its own (17 Nov 2026). */}
      <DetailsReviewPrompt />
      {/* pay a bill */}
      <Section title="Pay a bill" action="See all" onAction={() => setMore(true)} />
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', paddingHorizontal: 12, paddingTop: 14 }}>
        {grid.map((t) => <BillTile key={t.label} tile={t} onPress={() => (t.more ? setMore(true) : t.go?.())} />)}
      </View>

      {/* Fixed Save promo: only while savings is open, at the best rate the server offers */}
      {products.savings ? (
        <Pressable accessibilityRole="button" accessibilityLabel="Fixed Save" onPress={() => router.push('/fixedsave')}
          style={{ marginHorizontal: 20, marginTop: 8, flexDirection: 'row', alignItems: 'center', gap: 13, paddingVertical: 15, paddingHorizontal: 16, borderRadius: 20, backgroundColor: c.surface2, borderWidth: 1.5, borderColor: c.line }}>
          <View style={{ width: 42, height: 42, borderRadius: 14, backgroundColor: c.surface, alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="piggy" size={20} color={palette.teal700} />
          </View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text numberOfLines={1} style={{ fontSize: 13.5, fontFamily: fontA.display, color: c.ink1 }}>
              {products.bestRate ? `Fixed Save · up to ${products.bestRate}% p.a` : 'Fixed Save'}
            </Text>
            <Text numberOfLines={1} style={{ fontSize: 11.5, fontFamily: fontA.body, color: c.ink3, marginTop: 2 }}>Grow your savings, locked & safe</Text>
          </View>
          <View style={{ paddingVertical: 9, paddingHorizontal: 16, borderRadius: 999, backgroundColor: palette.teal700 }}>
            <Text style={{ fontSize: 12.5, fontFamily: fontA.display, color: '#fff' }}>Save</Text>
          </View>
        </Pressable>
      ) : null}

      {/* recent activity */}
      <Section title="Recent activity" action="See all" onAction={() => router.push('/history')} />
      {historyError && txns.length > 0 ? <Text accessibilityRole="alert" style={{ color: c.amber, marginHorizontal: 20, marginTop: 10, fontFamily: fontA.body }}>Showing previously loaded activity. {historyError} Pull down to retry.</Text> : null}
      <View style={{ marginHorizontal: 20, marginTop: 12, marginBottom: 8, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, borderRadius: 20, paddingVertical: 4, paddingHorizontal: 16 }}>
        {txns.length === 0 ? (
          <Text accessibilityRole={historyError ? 'alert' : undefined} style={{ color: c.ink3, fontFamily: fontA.body, paddingVertical: 14 }}>
            {!hydrated ? 'Loading transactions…' : historyError || 'No transactions yet'}
          </Text>
        ) : txns.slice(0, 4).map((x, i) => <ActivityRow key={x.id} txn={x} first={i === 0} onSelect={openTxn} />)}
      </View>

      {/* all services: More opens this pop-up, not a page */}
      <Sheet open={more} onClose={() => setMore(false)} title="All services">
        {([['Money', MORE_MONEY], ['Pay a bill', MORE_BILLS]] as const).map(([heading, items], g) => (
          <View key={heading} style={{ marginTop: g ? 6 : 0 }}>
            <Text style={{ fontSize: 12, fontFamily: fontA.bodyBold, color: c.ink3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 12 }}>{heading}</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
              {items.map((s) => (
                <View key={s.label} style={{ width: '25%', alignItems: 'center', marginBottom: 16 }}>
                  <ServiceTile icon={s.icon} label={s.label} badge={s.badge} onPress={() => { setMore(false); setTimeout(() => s.go(), 240); }} />
                </View>
              ))}
            </View>
          </View>
        ))}
      </Sheet>

      {/* the balance card's "…" */}
      <Sheet open={cardMenu} onClose={() => setCardMenu(false)} title="Your account">
        {[
          { icon: 'card', label: 'Account details', go: () => router.push('/accountdetails') },
          { icon: 'download', label: 'Statement', go: () => router.push('/statement') },
          { icon: 'chart', label: 'Account limits', go: () => router.push('/accountlimits') },
        ].map((o) => (
          <Pressable key={o.label} accessibilityRole="button" accessibilityLabel={o.label}
            onPress={() => { setCardMenu(false); setTimeout(() => o.go(), 240); }}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14 }}>
            <ZIcon name={o.icon} size={20} color={c.brand} />
            <Text style={{ flex: 1, fontSize: 15, fontFamily: fontA.bodySemi, color: c.ink1 }}>{o.label}</Text>
            <ZIcon name="right" size={16} color={c.ink3} />
          </Pressable>
        ))}
      </Sheet>

      {/* smart paste-to-pay */}
      <SmartPaste />
    </Screen>
  );
};

export default Home;
