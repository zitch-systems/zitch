import React, { useCallback, useState } from 'react';
import { View, Text } from 'react-native';
import { useFocusEffect } from 'expo-router';
import { Screen, Header, Card, Field, Btn, PinSheet, money } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { apiJson } from '@/lib/api';
import { useTheme, font } from '@/lib/theme';
import AuthGuard from '@/components/AuthGuard';

type LimitState = {
  tier: number;
  tier_transaction_limit: string;
  self_txn_limit: string | null;
  transaction_limit: string;
  daily_transfer_limit: string;
  daily_bill_limit: string;
};

/**
 * Your own transaction limit.
 *
 * The distinction this screen has to make legible: the TIER limit is what
 * verification allows, and the customer cannot raise it here — that is a KYC
 * bound, earned by verifying identity. What they can do is set themselves a
 * LOWER one, as a guardrail. So the screen always shows both numbers, and says
 * plainly which is which; a single "limit" figure would make the refusal look
 * arbitrary when they try to exceed it.
 *
 * Changing it needs the PIN, for the same reason paying does. A self-imposed
 * limit is the first control a thief holding a live session would want gone, and
 * lifting it back to the ceiling is worth as much to them as a transfer.
 */
const Limits = () => {
  const { c } = useTheme();
  const [limits, setLimits] = useState<LimitState | null>(null);
  const [draft, setDraft] = useState('');
  const [pinOpen, setPinOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingClear, setPendingClear] = useState(false);

  const load = useCallback(async () => {
    const res = await apiJson<any>('/api/limits/', {});
    if (res?.success) {
      setLimits(res);
      setDraft(res.self_txn_limit ? String(Math.trunc(Number(res.self_txn_limit))) : '');
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const apply = async (pin: string) => {
    setBusy(true);
    try {
      const res = await apiJson<any>('/api/limits/transaction/', {
        limit: pendingClear ? 'off' : draft,
        pin,
      });
      setPinOpen(false);
      if (!res?.success) {
        notify('Not changed', res?.message || 'Could not update your limit.');
        return;
      }
      notify('Saved', res.message || 'Transaction limit updated');
      await load();
    } finally {
      setBusy(false);
      setPendingClear(false);
    }
  };

  const ceiling = limits ? Number(limits.tier_transaction_limit) : 0;
  const typed = Number(String(draft).replace(/[^0-9.]/g, ''));
  const tooHigh = !!draft && Number.isFinite(typed) && typed > ceiling;

  return (
    <AuthGuard>
      <Screen>
        <Header title="Transaction limit" sub="How much you can send in one go" />

        <Card>
          <Row label="Your limit per transaction" value={limits ? money(Number(limits.transaction_limit)) : '—'} strong />
          <Row label={`Tier ${limits?.tier ?? '—'} maximum`} value={limits ? money(ceiling) : '—'} />
          <Row label="Daily transfers" value={limits ? money(Number(limits.daily_transfer_limit)) : '—'} />
          <Row label="Daily bills" value={limits ? money(Number(limits.daily_bill_limit)) : '—'} last />
        </Card>

        <View style={{ height: 14 }} />

        <Card>
          <Text style={{ fontSize: 14.5, fontFamily: font.semibold, color: c.ink1, marginBottom: 6 }}>
            Set your own lower limit
          </Text>
          <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular, marginBottom: 12 }}>
            A limit you choose applies on top of your tier maximum. It can only be
            lower — to raise your tier maximum, verify more of your identity.
          </Text>
          <Field
            value={draft}
            onChangeText={setDraft}
            keyboardType="number-pad"
            placeholder={limits ? `Up to ${money(ceiling)}` : 'Amount'}
          />
          {tooHigh ? (
            <Text style={{ fontSize: 12, color: c.red, fontFamily: font.regular, marginTop: 8 }}>
              Your Tier {limits?.tier} maximum is {money(ceiling)}. Verify more of your identity to go higher.
            </Text>
          ) : null}
          <View style={{ height: 12 }} />
          <Btn
            label="Save limit"
            size="md"
            disabled={busy || !draft || tooHigh}
            onPress={() => { setPendingClear(false); setPinOpen(true); }}
          />
          {limits?.self_txn_limit ? (
            <>
              <View style={{ height: 10 }} />
              {/* Offered only when one is actually set: a "remove" button for a
                  limit that does not exist reads as though the tier maximum could
                  be removed too. */}
              <Btn
                label="Remove my limit"
                size="md"
                variant="outline"
                disabled={busy}
                onPress={() => { setPendingClear(true); setPinOpen(true); }}
              />
            </>
          ) : null}
        </Card>

        <PinSheet
          open={pinOpen}
          onClose={() => { setPinOpen(false); setPendingClear(false); }}
          onComplete={apply}
          busy={busy}
          title="Confirm it's you"
          subtitle={pendingClear
            ? 'Enter your 6-digit PIN to remove your own limit.'
            : 'Enter your 6-digit PIN to change your transaction limit.'}
        />
      </Screen>
    </AuthGuard>
  );
};

const Row = ({ label, value, strong, last }: { label: string; value: string; strong?: boolean; last?: boolean }) => {
  const { c } = useTheme();
  return (
    <View style={{
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      paddingVertical: 12,
      borderBottomWidth: last ? 0 : 1, borderBottomColor: c.line,
    }}>
      <Text style={{ fontSize: 13.5, color: c.ink2, fontFamily: font.regular, flex: 1, paddingRight: 12 }}>{label}</Text>
      <Text style={{ fontSize: strong ? 16 : 14, color: strong ? c.brand : c.ink1, fontFamily: strong ? font.bold : font.semibold }}>
        {value}
      </Text>
    </View>
  );
};

export default Limits;
