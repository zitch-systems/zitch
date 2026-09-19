import React, { useCallback, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import { loansService } from '@/lib/services/loans';
import { Screen, Card, Btn, Sheet, PinPad, money } from '@/components/design/ui';
import { Hero, SectionLabel } from '@/components/design/widgets';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

type ActiveLoan = {
  reference: string;
  principal: string;
  interest: string;
  tenure_days: number;
  total_repayment: string;
  outstanding: string;
  amount_repaid: string;
  due_date: string;
};

const Loans = () => {
  const { c } = useTheme();
  const { reload: reloadWallet } = useWallet();
  const [limit, setLimit] = useState(500000);
  const [available, setAvailable] = useState(500000);
  const [active, setActive] = useState<ActiveLoan | null>(null);
  const [pinOpen, setPinOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pinError, setPinError] = useState('');
  const [repaymentPending, setRepaymentPending] = useState(false);

  const load = useCallback(async () => {
    const t = await getToken();
    if (!t) return;
    try {
      const res = await loansService.getStatus();
      if (res.limit != null) setLimit(Number(res.limit));
      if (res.available != null) setAvailable(Number(res.available));
      setActive(res.active_loan ?? null);
    } catch {
      // keep last-known state
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const usedPct = limit > 0 ? Math.min(100, Math.round(((limit - available) / limit) * 100)) : 0;

  const repay = async (pin: string) => {
    if (!active) return;
    // The repayment endpoint binds idempotency to the amount. Persisting the key
    // under the same fingerprint makes an app-restart retry replay that request.
    const fingerprint = String(Number(active.outstanding));
    let requestKey = '';
    let deliveryStarted = false;
    setBusy(true);
    try {
      requestKey = await acquireSpendAttempt('loan-repay', fingerprint);
      deliveryStarted = true;
      const res = await loansService.repay(active.outstanding, pin, requestKey);
      const outcome = classifySpendResponse(res);
      if (outcome === 'success') {
        await clearSpendAttempt('loan-repay', fingerprint, requestKey);
        setPinOpen(false);
        setPinError('');
        setRepaymentPending(false);
        if (isRecoveredSpendResponse(res)) {
          notify(
            'Earlier repayment confirmed',
            'This confirms the earlier repayment; no new repayment was made. Authorize a new repayment to pay again.',
            'info',
          );
        } else {
          notify('Success', 'Loan repaid');
        }
        reloadWallet();
        load();
      } else if (outcome === 'pending' || outcome === 'unknown') {
        setPinOpen(false);
        setPinError('');
        setRepaymentPending(true);
        notify(
          'Repayment processing',
          outcome === 'pending'
            ? (res.message || 'Its final status will update only after provider confirmation.')
            : 'We could not confirm this repayment. Check History before trying again.',
          'info',
        );
        reloadWallet();
        load();
      } else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') {
        setPinError(res.message || 'Incorrect PIN');
      } else {
        await clearSpendAttempt('loan-repay', fingerprint, requestKey);
        setPinOpen(false);
        notify('Error', res.message || 'Repayment failed');
      }
    } catch {
      if (deliveryStarted) {
        setPinOpen(false);
        setRepaymentPending(true);
        notify('Repayment processing', 'We could not confirm this repayment. Check History before trying again.', 'info');
      } else {
        notify('Unable to start repayment', 'Could not safely prepare this request. Please try again.');
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen pad={false} tab>
      <Text style={{ paddingHorizontal: 20, paddingTop: 6, fontSize: 26, fontFamily: font.extrabold, color: c.ink1 }}>Loans</Text>

      <Hero style={{ margin: 16 }}>
        <Text style={{ fontSize: 13, color: 'rgba(255,255,255,.85)', fontFamily: font.regular }}>Available credit</Text>
        <Text style={{ fontSize: 32, fontFamily: font.extrabold, color: '#fff', marginTop: 4, fontVariant: ['tabular-nums'] }}>{money(available)}</Text>
        <View style={{ height: 6, borderRadius: 4, backgroundColor: 'rgba(255,255,255,.25)', marginTop: 14, overflow: 'hidden' }}>
          <View style={{ width: `${usedPct}%`, height: '100%', backgroundColor: '#fff' }} />
        </View>
        <Text style={{ fontSize: 12, color: 'rgba(255,255,255,.85)', marginTop: 8, fontFamily: font.regular }}>
          {money(limit - available)} of {money(limit)} limit used
        </Text>
      </Hero>

      {!active && (
        <View style={{ marginHorizontal: 16 }}>
          <Card>
            <Btn label="Get a new loan" icon="loan" onPress={() => router.push('/getloan')} />
          </Card>
        </View>
      )}

      <View style={{ paddingHorizontal: 18, paddingTop: 22 }}>
        <SectionLabel>Active loans</SectionLabel>
        {active ? (
          <View style={{ borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, padding: 16 }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <View>
                <Text style={{ fontFamily: font.bold, color: c.ink1 }}>Quick loan</Text>
                <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>Due {active.due_date} · {active.tenure_days} days</Text>
              </View>
              <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1, fontVariant: ['tabular-nums'] }}>{money(Number(active.outstanding))}</Text>
            </View>
            <View style={{ marginTop: 14 }}>
              <Btn
                label={repaymentPending ? 'Repayment processing' : `Repay ${money(Number(active.outstanding))}`}
                disabled={repaymentPending}
                onPress={() => { setPinError(''); setPinOpen(true); }}
              />
            </View>
          </View>
        ) : (
          <Text style={{ color: c.ink3, fontFamily: font.regular, paddingVertical: 8 }}>No active loans</Text>
        )}
      </View>

      <Sheet open={pinOpen} onClose={() => !busy && setPinOpen(false)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Processing…' : `Repay ${active ? money(Number(active.outstanding)) : ''} from your wallet`}
        </Text>
        <PinPad onComplete={(p) => repay(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default Loans;
