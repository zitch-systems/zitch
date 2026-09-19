import React, { useState } from 'react';
import { View, Text, Pressable, ScrollView, Alert } from 'react-native';
import { router } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import { Card, Sheet, money, NText } from '@/components/design/ui';
import { SectionLabel } from '@/components/design/widgets';
import { Monogram, AmountField } from '@/components/design/flowkit';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { apiJson } from '@/lib/api';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
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

// ---- Wallet: connected-accounts carousel + fund/refresh/unlink --------------
export const ConnectedAccounts = () => {
  const { c } = useTheme();
  const { linked, showBal, reload, reloadLinked } = useWallet();

  const [busyId, setBusyId] = useState<number | null>(null);
  const [fundingOpen, setFundingOpen] = useState(false);
  const [target, setTarget] = useState<LinkedAccount | null>(null);
  const [amount, setAmount] = useState('');
  const [busy, setBusy] = useState(false);

  const closeFunding = () => { setFundingOpen(false); setTarget(null); setAmount(''); };

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

  const openFund = (b: LinkedAccount) => { setTarget(b); setFundingOpen(true); setAmount(''); };

  // Fund Zitch FROM the bank (Mono DirectPay) — wallet credited via webhook.
  const fundIn = async () => {
    if (!target) return;
    const amt = Number(amount);
    if (!Number.isFinite(amt) || amt < 100) { notify('Error', 'Minimum amount is ₦100'); return; }
    const scope = 'banklink-fund';
    // Match the backend's material binding: one linked account and one canonical
    // two-decimal amount. Changing either acquires a separate durable marker,
    // while reopening this exact authorization after an app restart reuses it.
    const fingerprint = [String(target.id), amt.toFixed(2)].join('|');
    let requestKey = '';
    let deliveryStarted = false;
    setBusy(true);
    try {
      requestKey = await acquireSpendAttempt(scope, fingerprint);
      deliveryStarted = true;
      const r = await apiJson<{
        success?: boolean;
        pending?: boolean;
        duplicate?: boolean;
        funded?: boolean;
        authorization_url?: string;
        mock?: boolean;
        message?: string;
        reference?: string;
        offline?: boolean;
        _httpOk?: boolean;
        _httpStatus?: number;
      }>('/api/banklink/fund/', {
        linked_id: target.id,
        amount: String(amt),
        idempotency_key: requestKey,
      });
      const outcome = classifySpendResponse(r);

      if (outcome === 'pending' || outcome === 'unknown') {
        notify(
          'Not confirmed',
          r.message || 'We could not confirm this bank funding request. Do not start it again; retrying will safely resume the same attempt.',
        );
        return;
      }
      if (outcome === 'failed') {
        await clearSpendAttempt(scope, fingerprint, requestKey);
        notify('Error', r.message || 'Could not start bank funding.');
        return;
      }

      const recovered = isRecoveredSpendResponse(r);
      if (r.funded === true) {
        await clearSpendAttempt(scope, fingerprint, requestKey);
        closeFunding();
        await Promise.all([reload(), reloadLinked()]);
        notify(
          recovered ? 'Earlier funding confirmed' : 'Funding confirmed',
          `${money(amt)} was credited to your Zitch wallet. Start a new request if you want to fund it again.`,
        );
        return;
      }
      if (r.mock) {
        await clearSpendAttempt(scope, fingerprint, requestKey);
        closeFunding();
        notify('Test mode', 'Bank funding is in test mode — no real debit was made.');
        return;
      }
      if (!r.authorization_url || !/^https?:/.test(r.authorization_url)) {
        // `success` here only means initialization succeeded; without a usable
        // authorization URL it is not evidence that the bank debit failed.
        notify('Not confirmed', 'Your bank funding request was started, but its authorization link was not confirmed. Retry to safely resume the same attempt.');
        return;
      }

      // Do not clear yet: an authorization URL is only an initialized debit,
      // not a bank-confirmed wallet credit. A later identical action first
      // replays this key and reports the earlier outcome accurately.
      closeFunding();
      await WebBrowser.openBrowserAsync(r.authorization_url);
      notify(
        recovered ? 'Continue earlier authorization' : 'Authorize in your bank',
        'Finish there — your Zitch wallet is credited only after your bank confirms.',
      );
    } catch {
      notify(
        deliveryStarted ? 'Not confirmed' : 'Unable to start funding',
        deliveryStarted
          ? 'We could not confirm this bank funding request. Retry to safely resume the same attempt.'
          : 'Could not safely prepare this request. Please try again.',
      );
    }
    finally { setBusy(false); }
  };

  return (
    <>
      <View style={{ paddingHorizontal: 20, paddingTop: 6 }}>
        <SectionLabel action="+ Add" onAction={() => router.push('/linkbank')}>Connected accounts</SectionLabel>
      </View>

      {linked.length === 0 ? (
        <Pressable onPress={() => router.push('/linkbank')} style={{ marginHorizontal: 20, flexDirection: 'row', alignItems: 'center', gap: 12, padding: 16, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line }}>
          <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="link" size={20} color={c.brand} />
          </View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={{ fontSize: 14.5, fontFamily: font.semibold, color: c.ink1 }}>Connect a bank</Text>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>See its balance & fund your Zitch wallet</Text>
          </View>
          <ZIcon name="right" size={18} color={c.ink3} />
        </Pressable>
      ) : (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 20, gap: 12 }}>
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
                  <Pressable onPress={() => refreshOne(b)} hitSlop={8} accessibilityRole="button" accessibilityLabel={`Refresh ${b.bank_name} balance`} style={{ width: 32, height: 32, borderRadius: 10, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
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
                  <Pressable onPress={() => openFund(b)} hitSlop={2} accessibilityRole="button" accessibilityLabel={`Fund Zitch from ${b.bank_name}`} style={{ flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5, height: 40, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.12)' }}>
                    <ZIcon name="deposit" size={15} color={c.brand} />
                    <Text style={{ fontSize: 12.5, color: c.brand, fontFamily: font.bold }}>Fund Zitch</Text>
                  </Pressable>
                </View>
              </Pressable>
            );
          })}
        </ScrollView>
      )}

      {/* Mono DirectPay amount sheet (linked bank -> Zitch wallet). */}
      <Sheet open={fundingOpen} onClose={() => !busy && closeFunding()} title={`Fund Zitch from ${target?.bank_name || 'bank'}`}>
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 16, marginTop: -6, fontFamily: font.regular }}>
          {`We’ll open ${target?.bank_name || 'your bank'} to authorize the debit. Your wallet is credited once it’s confirmed.`}
        </Text>
        <AmountField value={amount} onChangeText={setAmount} />
        <View style={{ height: 16 }} />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Confirm linked-bank funding"
          onPress={fundIn}
          disabled={busy || Number(amount) < 100}
          style={{ height: 54, borderRadius: 16, backgroundColor: Number(amount) >= 100 && !busy ? c.brand : c.surface3, alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ color: Number(amount) >= 100 && !busy ? '#fff' : c.ink3, fontFamily: font.bold, fontSize: 15 }}>
            {busy ? 'Starting…' : Number(amount) >= 100 ? `Fund ${money(Number(amount))}` : 'Fund Zitch'}
          </Text>
        </Pressable>
      </Sheet>
    </>
  );
};
