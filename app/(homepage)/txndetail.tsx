import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Btn, money } from '@/components/design/ui';
import { Monogram } from '@/components/design/flowkit';
import ReceiptExport, { ExportAction } from '@/components/design/ReceiptExport';
import WhatsAppBankingPromo from '@/components/design/whatsapp-banking-promo';
import { ReceiptRow, receiptHtml, senderRows } from '@/lib/receipt';
import { apiJson } from '@/lib/api';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const Row2 = ({ k, v }: { k: string; v: string }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 11, borderTopWidth: 1, borderTopColor: c.line }}>
      <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>{k}</Text>
      <Text style={{ fontSize: 14, fontFamily: font.semibold, color: c.ink1, maxWidth: '62%', textAlign: 'right' }}>{v}</Text>
    </View>
  );
};

// The receipt a user comes back for, days later — the only evidence of a payment
// they still have once the success screen is gone. It exports the same JPEG/PDF
// files as the post-purchase receipt: a receipt that behaves differently
// depending on which door you entered through reads as two different features.
const TxnDetail = () => {
  const { c } = useTheme();
  const card = useRef<View>(null);
  const [action, setAction] = useState<ExportAction | null>(null);
  const [busy, setBusy] = useState(false);
  const p = useLocalSearchParams<{
    type?: string; amount?: string; status?: string; dir?: string; detail?: string; reference?: string; icon?: string;
    narration?: string;
  }>();

  const inflow = p.dir === 'in';
  const amount = Number(p.amount || 0);
  const mono = (p.type || 'TX').split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  // Status badge reflects the real status — not always "success". The exported
  // file carries the same status: a failed transaction must never leave this
  // screen as a document stamped Successful.
  //
  // LIVE STATUS. Everything on this screen used to come from the route params
  // captured when the row was tapped, so a transfer that was Pending at that
  // moment stayed Pending on screen forever — the reconciler settles it minutes
  // later and nothing here ever asked again. A payment that reads "Pending"
  // permanently is indistinguishable, to the person who sent it, from money that
  // vanished. So: ask the server on focus, and keep asking while it is pending.
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const status = liveStatus || p.status || 'Successful';
  const sl = status.toLowerCase();
  const statusColor = sl === 'failed' ? c.red : sl === 'pending' ? c.amber : c.lime;
  const statusIcon = sl === 'failed' ? 'x' : sl === 'pending' ? 'history' : 'check';

  // Sender is the wallet this transaction ran through — same rows the
  // post-purchase receipt prints, because a receipt that reads differently
  // depending on which door you entered through is two different documents.
  // On an inflow the wallet is the party receiving, so the lines would be
  // actively wrong; they are only printed for money leaving.
  const { accountName, firstName, accountNumber, bankName, reload } = useWallet();

  // Poll only while the transaction is unresolved, and only for a bounded time:
  // a settled row has nothing left to say, and an indefinite timer on a screen
  // someone leaves open is a battery cost with no payoff. ~2 minutes covers the
  // ordinary settle; anything slower is the reconciler's job, and History will
  // show it on the next pull-to-refresh.
  const POLL_MS = 12_000;
  const MAX_POLLS = 10;
  const polls = useRef(0);

  const refreshStatus = useCallback(async (): Promise<string | null> => {
    const reference = p.reference;
    if (!reference) return null;
    try {
      const res = await apiJson<{ success?: boolean; transaction?: { transaction_status?: string } }>(
        '/api/transaction/status/', { reference },
      );
      const next = res?.success ? String(res.transaction?.transaction_status || '') : '';
      if (next) {
        setLiveStatus(next);
        return next;
      }
    } catch {
      // Offline or a transient failure: keep showing the last known status
      // rather than blanking a receipt the customer may be reading right now.
    }
    return null;
  }, [p.reference]);

  useFocusEffect(useCallback(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    polls.current = 0;

    const tick = async () => {
      const next = await refreshStatus();
      if (!alive) return;
      if (next != null && next.toLowerCase() !== 'pending') {
        // Settled. The list this screen was opened from is stale now too, so
        // refresh it — backing out should not show the old Pending row.
        void reload();
        return;
      }
      // Still pending, or the request failed (next === null). A failed poll is
      // deliberately retried rather than treated as terminal: the usual cause is
      // a dropped connection, and giving up on the first one would strand the
      // screen on exactly the status we are trying to move off.
      if (polls.current >= MAX_POLLS) return;
      polls.current += 1;
      timer = setTimeout(tick, POLL_MS);
    };
    void tick();

    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [refreshStatus, reload]));
  const from = inflow ? [] : senderRows({
    name: accountName || firstName,
    account: accountNumber,
    bank: bankName,
  });

  // The customer's own note, when they gave one — absent rather than blank
  // otherwise, because an empty "Narration —" row on an exported receipt reads
  // as something that failed to render. It sits with the description, not after
  // the reference and the channel, which are for us rather than for whoever is
  // being shown the receipt.
  const note = (p.narration || '').trim();
  const rows: ReceiptRow[] = [
    ['Description', p.type || 'Transaction'],
    ...(note ? ([['Narration', note]] as ReceiptRow[]) : []),
    ...(p.detail ? ([['Date', p.detail]] as ReceiptRow[]) : []),
    ['Reference', p.reference || '—'],
    ['Channel', 'Zitch Wallet'],
    ...from,
    ['Amount', (inflow ? '+' : '-') + money(Math.abs(amount)), true],
  ];

  return (
    <Screen tab>
      <Header title="Transaction details" onBack={() => router.back()} />

      {/* The capture target for the JPEG export — explicit background so the
          shared image isn't transparent-turned-black on Android. */}
      <View ref={card} collapsable={false} style={{ backgroundColor: c.bg }}>
        <View style={{ alignItems: 'center', paddingTop: 16 }}>
          <Monogram text={mono} color={inflow ? c.lime : c.brand} size={64} />
          <Text numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7} style={{ maxWidth: '100%', fontSize: 32, fontFamily: font.extrabold, color: inflow ? c.lime : c.ink1, marginTop: 14, fontVariant: ['tabular-nums'] }}>
            {(inflow ? '+' : '-') + money(Math.abs(amount))}
          </Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 8, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999, backgroundColor: `${statusColor}1F` }}>
            <ZIcon name={statusIcon} size={13} color={statusColor} />
            <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: statusColor }}>{status}</Text>
          </View>
          {/* A Pending badge on its own says nothing about whether anyone is
              still working on it. This line says the screen is actively watching
              — which is the difference between "processing" and "abandoned" —
              and gives a tap to check now for someone who does not want to wait
              out the poll. */}
          {sl === 'pending' ? (
            <View style={{ alignItems: 'center', marginTop: 10, gap: 8 }}>
              <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular, textAlign: 'center', paddingHorizontal: 24, lineHeight: 18 }}>
                Still with the bank. This updates by itself — your money has not left your balance twice.
              </Text>
              <Text
                onPress={() => { void refreshStatus(); }}
                accessibilityRole="button"
                style={{ fontSize: 13, color: c.brand, fontFamily: font.semibold }}
              >
                Check now
              </Text>
            </View>
          ) : null}
        </View>

        <View style={{ marginTop: 22, marginBottom: 4, borderRadius: 18, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, paddingHorizontal: 16, paddingBottom: 8 }}>
          <Row2 k="Description" v={p.type || 'Transaction'} />
          {note ? <Row2 k="Narration" v={note} /> : null}
          {p.detail ? <Row2 k="Date" v={p.detail} /> : null}
          <Row2 k="Reference" v={p.reference || '—'} />
          <Row2 k="Channel" v="Zitch Wallet" />
          {/* Same sender lines the exported file carries — the JPEG is a capture
              of this card, so anything missing here is missing from the share. */}
          {from.map(([k, v]) => <Row2 key={k} k={k} v={v} />)}
        </View>

        {/* Kept inside the capture target so receipts exported later from History
            carry the same WhatsApp banking advert as immediate success receipts. */}
        <View style={{ marginTop: 12, marginBottom: 4 }}>
          <WhatsAppBankingPromo receipt onPress={() => router.push('/linkwhatsapp')} />
        </View>
      </View>

      <View style={{ marginTop: 16, flexDirection: 'row', gap: 10 }}>
        <View style={{ flex: 1 }}>
          <Btn label="Save receipt" icon="download" variant="outline" disabled={busy} onPress={() => setAction('save')} />
        </View>
        <View style={{ flex: 1 }}>
          <Btn label="Share receipt" icon="share" variant="outline" disabled={busy} onPress={() => setAction('share')} />
        </View>
      </View>

      <ReceiptExport
        action={action}
        onClose={() => setAction(null)}
        onBusy={setBusy}
        source={() => ({
          capture: async () => {
            const { captureRef } = await import('react-native-view-shot');
            if (!card.current) throw new Error('Receipt is not ready to capture');
            return captureRef(card.current, { format: 'jpg', quality: 0.95, result: 'tmpfile' });
          },
          html: receiptHtml({
            title: p.type || 'Transaction',
            message: `${inflow ? 'Received' : 'Paid'} ${money(Math.abs(amount))}`,
            rows,
            status,
          }),
          reference: p.reference || '',
        })}
      />
    </Screen>
  );
};

export default TxnDetail;
