import React, { useRef, useState } from 'react';
import { View, Text, Pressable, ScrollView, Alert } from 'react-native';
import { router } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import { Card, Sheet, PinSheet, Field, Naira, money, NText } from '@/components/design/ui';
import { SectionLabel } from '@/components/design/widgets';
import { Monogram } from '@/components/design/flowkit';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { apiJson, newIdempotencyKey } from '@/lib/api';
import { useTheme, font } from '@/lib/theme';
import { useWallet, type LinkedAccount } from '@/lib/wallet';

// ---- helpers ---------------------------------------------------------------
const PALETTE = ['#E8590C', '#1E6FD9', '#7A5CFF', '#0CA678', '#D6336C', '#F08C00', '#2B8A3E', '#5C7CFA'];
const bankColor = (name: string): string => {
  const s = name || 'bank';
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h + s.charCodeAt(i)) % PALETTE.length;
  return PALETTE[h];
};
const bankInitials = (name: string): string => {
  const clean = (name || '').replace(/\bbank\b/gi, ' ').replace(/\s+/g, ' ').trim();
  if (!clean) return 'BK';
  const w = clean.split(' ');
  return ((w.length > 1 ? (w[0][0] || '') + (w[1][0] || '') : clean.slice(0, 2)) || 'BK').toUpperCase();
};
const balanceAge = (iso: string | null): string => {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
};
const needsReconnect = (b: LinkedAccount): boolean => b.status !== 'active' || b.balance == null;
const sumBalance = (banks: LinkedAccount[]): number =>
  banks.reduce((s, b) => s + (b.balance != null ? b.balance : 0), 0);

// ---- Home: aggregate summary card ------------------------------------------
export const LinkedBanksSummary = () => {
  const { c } = useTheme();
  const { balance, linked, showBal } = useWallet();
  if (linked.length === 0) return null;

  const linkedTotal = sumBalance(linked);
  const reconnect = linked.filter(needsReconnect).length;

  return (
    <Pressable onPress={() => router.push('/wallet')} style={{ marginHorizontal: 16, marginTop: -4 }}>
      <Card pad={16}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 9 }}>
          <ZIcon name="bank" size={18} color={c.brand} />
          <Text style={{ flex: 1, fontSize: 14.5, fontFamily: font.bold, color: c.ink1 }}>Connected banks</Text>
          {reconnect > 0 ? (
            <View style={{ backgroundColor: 'rgba(240,140,0,.14)', borderRadius: 999, paddingHorizontal: 9, paddingVertical: 4 }}>
              <Text style={{ fontSize: 11, color: c.amber, fontFamily: font.bold }}>{reconnect} to reconnect</Text>
            </View>
          ) : null}
          <ZIcon name="right" size={16} color={c.ink3} />
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: 14 }}>
          <View style={{ flex: 1 }}>
            <NText style={{ fontSize: 19, fontFamily: font.extrabold, color: c.ink1, fontVariant: ['tabular-nums'] }}>
              {showBal ? money(linkedTotal) : '₦ ••••'}
            </NText>
            <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.regular, marginTop: 2 }}>
              across {linked.length} linked bank{linked.length === 1 ? '' : 's'}
            </Text>
          </View>
          <View style={{ width: 1, alignSelf: 'stretch', backgroundColor: c.line }} />
          <View style={{ flex: 1, alignItems: 'flex-end' }}>
            <NText style={{ fontSize: 19, fontFamily: font.extrabold, color: c.brand, fontVariant: ['tabular-nums'] }}>
              {showBal ? money(balance + linkedTotal) : '₦ ••••'}
            </NText>
            <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.regular, marginTop: 2 }}>total with Zitch</Text>
          </View>
        </View>
      </Card>
    </Pressable>
  );
};

// ---- Wallet: connected-accounts carousel + fund/payout/refresh/unlink -------
export const ConnectedAccounts = () => {
  const { c } = useTheme();
  const { linked, showBal, reload, reloadLinked } = useWallet();

  const [busyId, setBusyId] = useState<number | null>(null);
  const [mode, setMode] = useState<null | 'in' | 'out'>(null); // fund Zitch / fund bank
  const [target, setTarget] = useState<LinkedAccount | null>(null);
  const [amount, setAmount] = useState('');
  const [pinOpen, setPinOpen] = useState(false);
  const [pinErr, setPinErr] = useState('');
  const [busy, setBusy] = useState(false);
  const idem = useRef('');

  const closeAll = () => { setMode(null); setTarget(null); setAmount(''); setPinOpen(false); setPinErr(''); idem.current = ''; };

  const refreshOne = async (b: LinkedAccount) => {
    setBusyId(b.id);
    try {
      const r = await apiJson<{ success?: boolean; message?: string }>('/api/banklink/refresh/', { linked_id: b.id });
      if (r?.success === false) notify('Could not refresh', r.message || 'Please try again in a moment.');
      await reloadLinked();
    } catch { /* keep cached */ }
    finally { setBusyId(null); }
  };

  const unlinkOne = (b: LinkedAccount) => {
    Alert.alert('Unlink this bank?', `${b.bank_name} ${b.account_number} will be removed. You can connect it again anytime.`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Unlink', style: 'destructive', onPress: async () => {
          setBusyId(b.id);
          try {
            const r = await apiJson<{ success?: boolean }>('/api/banklink/unlink/', { linked_id: b.id });
            if (r?.success) await reloadLinked(); else notify('Error', 'Could not unlink. Please try again.');
          } catch { notify('Error', 'Something went wrong.'); } finally { setBusyId(null); }
        },
      },
    ]);
  };

  const openFund = (b: LinkedAccount, m: 'in' | 'out') => { setTarget(b); setMode(m); setAmount(''); idem.current = newIdempotencyKey(); };

  // Fund Zitch FROM the bank (Mono DirectPay) — wallet credited via webhook.
  const fundIn = async () => {
    if (!target) return;
    const amt = Number(amount);
    if (!Number.isFinite(amt) || amt < 100) { notify('Error', 'Minimum amount is ₦100'); return; }
    setBusy(true);
    try {
      const r = await apiJson<{ success?: boolean; authorization_url?: string; mock?: boolean; message?: string }>(
        '/api/banklink/fund/', { linked_id: target.id, amount: String(amt), idempotency_key: idem.current });
      if (!r?.success) { notify('Error', r?.message || 'Could not start bank funding.'); return; }
      closeAll();
      if (r.mock || !r.authorization_url || !/^https?:/.test(r.authorization_url)) {
        notify('Test mode', 'Bank funding is in test mode — no real debit was made.');
        return;
      }
      await WebBrowser.openBrowserAsync(r.authorization_url);
      notify('Authorize in your bank', 'Finish there — your Zitch wallet is credited once your bank confirms.');
    } catch { notify('Error', 'Something went wrong. Please try again later.'); }
    finally { setBusy(false); }
  };

  // Fund the bank FROM Zitch (wallet debit -> bank payout), PIN-verified.
  const fundOut = async (pin: string) => {
    if (!target) return;
    const amt = Number(amount);
    setBusy(true);
    try {
      const r = await apiJson<{ success?: boolean; code?: string; message?: string }>(
        '/api/banklink/payout/', { linked_id: target.id, amount: String(amt), pin, idempotency_key: idem.current });
      if (r?.success) { closeAll(); reload(); reloadLinked(); notify('On its way', `${money(amt)} sent to ${target.bank_name}.`); }
      else if (r?.code === 'pin_incorrect' || r?.code === 'pin_locked') { setPinErr(r.message || 'Incorrect PIN'); }
      else { closeAll(); notify('Error', r?.message || 'Could not complete the payout.'); }
    } catch { closeAll(); notify('Error', 'Something went wrong. Please try again later.'); }
    finally { setBusy(false); }
  };

  return (
    <>
      <View style={{ paddingHorizontal: 18, paddingTop: 6 }}>
        <SectionLabel action="+ Add" onAction={() => router.push('/linkbank')}>Connected accounts</SectionLabel>
      </View>

      {linked.length === 0 ? (
        <Pressable onPress={() => router.push('/linkbank')} style={{ marginHorizontal: 16, flexDirection: 'row', alignItems: 'center', gap: 12, padding: 16, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line }}>
          <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="link" size={20} color={c.brand} />
          </View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={{ fontSize: 14.5, fontFamily: font.semibold, color: c.ink1 }}>Connect a bank</Text>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>See its balance & move money in or out</Text>
          </View>
          <ZIcon name="right" size={18} color={c.ink3} />
        </Pressable>
      ) : (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 16, gap: 12 }}>
          {linked.map((b) => {
            const reconnect = needsReconnect(b);
            return (
              <Pressable key={b.id} onLongPress={() => unlinkOne(b)} delayLongPress={350}
                style={{ width: 270, borderRadius: 18, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, padding: 16 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 11 }}>
                  <Monogram text={bankInitials(b.bank_name)} color={bankColor(b.bank_name)} size={40} />
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <NText numberOfLines={1} style={{ fontSize: 14.5, fontFamily: font.bold, color: c.ink1 }}>{b.bank_name || 'Bank'}</NText>
                    <Text numberOfLines={1} style={{ fontSize: 12, color: c.ink3, fontFamily: font.regular }}>{b.account_number}</Text>
                  </View>
                  <Pressable onPress={() => refreshOne(b)} hitSlop={8} style={{ width: 32, height: 32, borderRadius: 10, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
                    <ZIcon name="refresh" size={15} color={c.brand} />
                  </Pressable>
                </View>

                <View style={{ flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', marginTop: 12 }}>
                  <NText style={{ fontSize: 21, fontFamily: font.extrabold, color: c.ink1, fontVariant: ['tabular-nums'] }}>
                    {showBal ? (b.balance != null ? money(b.balance) : '—') : '••••'}
                  </NText>
                  {reconnect ? (
                    <Pressable onPress={() => router.push('/linkbank')} style={{ backgroundColor: 'rgba(240,140,0,.14)', borderRadius: 999, paddingHorizontal: 9, paddingVertical: 4 }}>
                      <Text style={{ fontSize: 11, color: c.amber, fontFamily: font.bold }}>Reconnect</Text>
                    </Pressable>
                  ) : b.balance_updated ? (
                    <Text style={{ fontSize: 11, color: c.ink3, fontFamily: font.regular }}>{busyId === b.id ? 'updating…' : balanceAge(b.balance_updated)}</Text>
                  ) : null}
                </View>

                <View style={{ flexDirection: 'row', gap: 9, marginTop: 14 }}>
                  <Pressable onPress={() => openFund(b, 'in')} style={{ flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5, height: 40, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.12)' }}>
                    <ZIcon name="deposit" size={15} color={c.brand} />
                    <Text style={{ fontSize: 12.5, color: c.brand, fontFamily: font.bold }}>Fund Zitch</Text>
                  </Pressable>
                  <Pressable onPress={() => openFund(b, 'out')} style={{ flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5, height: 40, borderRadius: 12, borderWidth: 1.5, borderColor: c.line }}>
                    <ZIcon name="withdraw" size={15} color={c.ink2} />
                    <Text numberOfLines={1} style={{ fontSize: 12.5, color: c.ink2, fontFamily: font.bold }}>Fund bank</Text>
                  </Pressable>
                </View>
              </Pressable>
            );
          })}
        </ScrollView>
      )}

      {/* amount sheet (fund in / out) */}
      <Sheet open={!!mode && !pinOpen} onClose={() => !busy && closeAll()} title={mode === 'out' ? `Fund ${target?.bank_name || 'bank'}` : `Fund Zitch from ${target?.bank_name || 'bank'}`}>
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 16, marginTop: -6, fontFamily: font.regular }}>
          {mode === 'out'
            ? `Move money from your Zitch wallet to ${target?.bank_name || 'your bank'}. You’ll confirm with your PIN.`
            : `We’ll open ${target?.bank_name || 'your bank'} to authorize the debit. Your wallet is credited once it’s confirmed.`}
        </Text>
        <Field value={amount} onChangeText={(v) => setAmount(v.replace(/\D/g, ''))} keyboardType="number-pad" placeholder="Enter amount" prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />} />
        <View style={{ height: 16 }} />
        {mode === 'out' ? (
          <Pressable onPress={() => { if (Number(amount) >= 100) { setPinErr(''); setPinOpen(true); } else notify('Error', 'Minimum amount is ₦100'); }}
            style={{ height: 54, borderRadius: 16, backgroundColor: Number(amount) >= 100 ? c.brand : c.surface3, alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ color: Number(amount) >= 100 ? '#fff' : c.ink3, fontFamily: font.bold, fontSize: 15 }}>Continue</Text>
          </Pressable>
        ) : (
          <Pressable onPress={fundIn} disabled={busy || Number(amount) < 100}
            style={{ height: 54, borderRadius: 16, backgroundColor: Number(amount) >= 100 && !busy ? c.brand : c.surface3, alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ color: Number(amount) >= 100 && !busy ? '#fff' : c.ink3, fontFamily: font.bold, fontSize: 15 }}>
              {busy ? 'Starting…' : Number(amount) >= 100 ? `Fund ${money(Number(amount))}` : 'Fund Zitch'}
            </Text>
          </Pressable>
        )}
      </Sheet>

      {/* PIN step for payout (fund bank) */}
      <PinSheet
        open={pinOpen}
        onClose={() => !busy && setPinOpen(false)}
        onComplete={fundOut}
        busy={busy}
        error={pinErr}
        title={`Send ${money(Number(amount) || 0)}`}
        subtitle={`Enter your PIN to send to ${target?.bank_name || 'your bank'}`}
      />
    </>
  );
};
