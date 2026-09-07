import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, ScrollView, ActivityIndicator, Animated } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import { apiJson } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Card, money, Sheet, Tap } from '@/components/design/ui';
import { Monogram } from '@/components/design/flowkit';
import { SectionLabel } from '@/components/design/widgets';
import { useTheme, font } from '@/lib/theme';

// A linked external bank account (Mono open-banking), as returned by
// POST /api/banklink/list/. Balance may be null until a refresh succeeds.
type Linked = {
  id: number;
  bank_name: string;
  account_number: string; // masked
  account_name: string;
  balance: string | null;
  balance_updated: string | null;
  status: 'active' | 'reauth' | string;
};

const CARD_W = 280;
const CARD_GAP = 12;

// The backend Linked shape has no colour field, so derive a stable brand-ish
// tile colour from the bank name (same name → same colour every render).
const PALETTE = ['#0FA295', '#2D7FF9', '#7A5CFF', '#16A34A', '#F4A623', '#E2574C', '#0A66C2', '#C2410C'];
const bankColor = (name: string) => {
  let h = 0;
  for (let i = 0; i < (name || '').length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
};

const initials = (name: string) => (name || 'BK').trim().slice(0, 2).toUpperCase();

// "balance updated" relative time. balance_updated may be an ISO timestamp or an
// already-human string ("Updated just now") — fall back to the raw value.
const relTime = (s: string) => {
  const t = Date.parse(s);
  if (isNaN(t)) return s.replace(/^Updated\s+/i, '');
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
};

// Continuously bobbing arrow (translateY loop). The two arrows on a card are
// desynced by a half-cycle (~0.85s of the ~1.7s loop): the in/deposit arrow
// bobs down (dir +1), the out/withdraw arrow bobs up (dir -1).
const BobArrow = ({ icon, color, dir, delay }: { icon: string; color: string; dir: 1 | -1; delay: number }) => {
  const v = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(v, { toValue: 1, duration: 850, delay, useNativeDriver: true }),
        Animated.timing(v, { toValue: 0, duration: 850, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, []);
  const translateY = v.interpolate({ inputRange: [0, 1], outputRange: [0, 3 * dir] });
  return (
    <Animated.View style={{ transform: [{ translateY }] }}>
      <ZIcon name={icon} size={13} color={color} />
    </Animated.View>
  );
};

const FundChip = ({ icon, dir, delay, label, ghost, onPress }: { icon: string; dir: 1 | -1; delay: number; label: string; ghost?: boolean; onPress: () => void }) => {
  const { c } = useTheme();
  return (
    <Tap onPress={onPress} style={{ flex: 1, minWidth: 0 }}>
      <View
        style={{
          height: 34,
          borderRadius: 10,
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 4,
          paddingHorizontal: 3,
          backgroundColor: ghost ? c.surface : 'rgba(15,162,149,.12)',
          borderWidth: ghost ? 1.5 : 0,
          borderColor: ghost ? 'rgba(15,162,149,.35)' : undefined,
        }}
      >
        <BobArrow icon={icon} color={c.brandDeep} dir={dir} delay={delay} />
        {/* Single string so the label is never split / ellipsised mid-word. */}
        <Text numberOfLines={1} style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 11.5 }}>{label}</Text>
      </View>
    </Tap>
  );
};

const LinkedBankCard = ({ a, onRefresh, refreshing, onOpen }: { a: Linked; onRefresh: () => void; refreshing: boolean; onOpen: () => void }) => {
  const { c } = useTheme();
  const reauth = a.status === 'reauth' || a.balance == null;
  const color = bankColor(a.bank_name);
  const bankTag = a.bank_name || 'bank';
  return (
    <Card pad={0} style={{ width: CARD_W, padding: 14, marginRight: CARD_GAP }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        <Monogram text={initials(a.bank_name)} color={color} size={34} />
        <Pressable onPress={onOpen} style={{ flex: 1, minWidth: 0 }}>
          <Text numberOfLines={1} style={{ fontSize: 13.5, fontFamily: font.bold, color: c.ink1 }}>{a.bank_name || 'Bank'}</Text>
          <Text numberOfLines={1} style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.medium, fontVariant: ['tabular-nums'] }}>{a.account_number}</Text>
        </Pressable>
        <Pressable onPress={onRefresh} hitSlop={8} style={{ width: 28, height: 28, borderRadius: 9, backgroundColor: c.surface3, alignItems: 'center', justifyContent: 'center' }}>
          {refreshing ? <ActivityIndicator size="small" color={c.ink3} /> : <ZIcon name="convert" size={15} color={c.ink2} />}
        </Pressable>
      </View>

      <View style={{ flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, marginTop: 12 }}>
        {reauth ? (
          <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.amber }}>Reconnect to view</Text>
        ) : (
          <Text style={{ fontSize: 15, fontFamily: font.extrabold, color: c.ink1, fontVariant: ['tabular-nums'] }}>
            {a.balance != null ? money(Number(a.balance)) : '—'}
          </Text>
        )}
        {a.balance_updated ? (
          <Text numberOfLines={1} style={{ fontSize: 10, color: c.ink3, fontFamily: font.regular }}>
            {refreshing ? 'Refreshing…' : relTime(a.balance_updated)}
          </Text>
        ) : null}
      </View>

      {reauth ? (
        <Pressable onPress={() => router.push('/linkbank')} style={{ marginTop: 12, paddingVertical: 9, borderRadius: 10, backgroundColor: 'rgba(245,166,35,.16)', alignItems: 'center' }}>
          <Text style={{ color: '#B27400', fontFamily: font.bold, fontSize: 12.5 }}>Reconnect</Text>
        </Pressable>
      ) : (
        <View style={{ flexDirection: 'row', gap: 7, marginTop: 12 }}>
          {/* Fund Zitch = money in (down/deposit arrow, bobs down). */}
          <FundChip icon="deposit" dir={1} delay={0} label="Fund Zitch" onPress={() => router.push('/addmoney')} />
          {/* Fund {bank} = money out (up/withdraw arrow, bobs up, half-cycle desync). */}
          <FundChip icon="withdraw" dir={-1} delay={850} label={'Fund ' + bankTag} ghost onPress={() => router.push('/sendmoney')} />
        </View>
      )}
    </Card>
  );
};

const ConnectTile = () => {
  const { c } = useTheme();
  return (
    <Pressable onPress={() => router.push('/linkbank')} style={{ width: 132, marginRight: CARD_GAP }}>
      <View style={{ flex: 1, minHeight: 96, borderRadius: 18, borderWidth: 1.5, borderColor: c.line, borderStyle: 'dashed', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: c.surface2 }}>
        <View style={{ width: 34, height: 34, borderRadius: 11, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="plus" size={18} color={c.brand} stroke={2.4} />
        </View>
        <Text style={{ fontSize: 12, fontFamily: font.bold, color: c.ink2 }}>Connect a bank</Text>
      </View>
    </Pressable>
  );
};

// Manage sheet: fund in/out, refresh, reconnect (when expired) and unlink.
const ManageRow = ({ icon, color, title, sub, onPress }: { icon: string; color: string; title: string; sub: string; onPress: () => void }) => {
  const { c } = useTheme();
  return (
    <Tap onPress={onPress}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 13, paddingVertical: 12, borderTopWidth: 1, borderTopColor: c.line }}>
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: color + '1F', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name={icon} size={19} color={color} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 14.5, fontFamily: font.bold, color: title === 'Unlink bank' ? c.red : c.ink1 }}>{title}</Text>
          <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.regular }}>{sub}</Text>
        </View>
        <ZIcon name="right" size={18} color={c.ink3} />
      </View>
    </Tap>
  );
};

// Horizontal snap strip of linked external bank accounts + a connect tile.
// Reads /api/banklink/list/ on focus; refresh hits /api/banklink/refresh/.
export const ConnectedAccounts = () => {
  const { c } = useTheme();
  const [accts, setAccts] = useState<Linked[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [manageFor, setManageFor] = useState<Linked | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiJson<{ accounts?: Linked[] }>('/api/banklink/list/');
      setAccts(Array.isArray(res.accounts) ? res.accounts : []);
    } catch {
      // leave last-known list; surfaced elsewhere
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const refresh = async (id: number) => {
    setBusyId(id);
    try {
      const res = await apiJson<{ account?: Linked }>('/api/banklink/refresh/', { linked_id: id });
      if (res.account) setAccts((prev) => prev.map((a) => (a.id === id ? { ...a, ...res.account } : a)));
    } catch {
      // keep prior balance
    } finally {
      setBusyId(null);
    }
  };

  if (loading) return null;

  const manageReauth = manageFor ? (manageFor.status === 'reauth' || manageFor.balance == null) : false;
  const manageTag = manageFor?.bank_name || 'bank';
  const closeAfter = (fn: () => void) => { setManageFor(null); setTimeout(fn, 260); };

  return (
    <View style={{ paddingTop: 22 }}>
      <View style={{ paddingHorizontal: 18 }}>
        <SectionLabel action={accts.length ? 'Add' : undefined} onAction={accts.length ? () => router.push('/linkbank') : undefined}>
          Connected accounts
        </SectionLabel>
      </View>
      {accts.length === 0 ? (
        <Pressable onPress={() => router.push('/linkbank')} style={{ marginHorizontal: 16 }}>
          <Card style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
            <View style={{ width: 42, height: 42, borderRadius: 13, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name="bank" size={22} color={c.brand} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>Connect a bank</Text>
              <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>See balances & move money in — via Mono</Text>
            </View>
            <ZIcon name="right" size={18} color={c.ink3} />
          </Card>
        </Pressable>
      ) : (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ paddingHorizontal: 16 }}
          snapToInterval={CARD_W + CARD_GAP}
          snapToAlignment="start"
          decelerationRate="fast"
        >
          {accts.map((a) => (
            <LinkedBankCard key={a.id} a={a} onRefresh={() => refresh(a.id)} refreshing={busyId === a.id} onOpen={() => setManageFor(a)} />
          ))}
          <ConnectTile />
        </ScrollView>
      )}

      <Sheet open={!!manageFor} onClose={() => setManageFor(null)} title={manageFor?.bank_name || 'Linked bank'}>
        {manageFor ? (
          <>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 14 }}>
              <Monogram text={initials(manageFor.bank_name)} color={bankColor(manageFor.bank_name)} size={48} />
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text numberOfLines={1} style={{ fontSize: 13, color: c.ink2, fontFamily: font.semibold }}>{manageFor.account_name}</Text>
                <Text numberOfLines={1} style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.medium, fontVariant: ['tabular-nums'] }}>{manageFor.account_number}</Text>
              </View>
              <Text style={{ fontSize: 16, fontFamily: font.extrabold, color: manageReauth ? c.amber : c.ink1, fontVariant: ['tabular-nums'] }}>
                {manageReauth ? 'Reconnect' : (manageFor.balance != null ? money(Number(manageFor.balance)) : '—')}
              </Text>
            </View>

            {manageReauth ? (
              <ManageRow icon="convert" color={c.brand} title="Reconnect bank" sub="Restore access to balances" onPress={() => closeAfter(() => router.push('/linkbank'))} />
            ) : (
              <>
                <ManageRow icon="deposit" color="#16A34A" title="Fund Zitch wallet" sub={'Move money in from ' + manageTag} onPress={() => closeAfter(() => router.push('/addmoney'))} />
                <ManageRow icon="withdraw" color={c.brand} title={'Fund ' + manageTag} sub={'Send money out to ' + manageTag} onPress={() => closeAfter(() => router.push('/sendmoney'))} />
                <ManageRow icon="convert" color={c.brand} title="Refresh balance" sub="Sync the latest balance" onPress={() => closeAfter(() => refresh(manageFor.id))} />
              </>
            )}
            {/* No unlink/remove API exists in lib/api yet, so Unlink routes to the
                link-bank manager (documented fallback) rather than a no-op call. */}
            <ManageRow icon="x" color={c.red} title="Unlink bank" sub="Remove this connection from Zitch" onPress={() => closeAfter(() => router.push('/linkbank'))} />
          </>
        ) : null}
      </Sheet>
    </View>
  );
};

export default ConnectedAccounts;
