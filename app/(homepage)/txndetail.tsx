import React, { useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Btn, money } from '@/components/design/ui';
import { Monogram } from '@/components/design/flowkit';
import ReceiptExport, { ExportAction } from '@/components/design/ReceiptExport';
import { ReceiptRow, receiptHtml } from '@/lib/receipt';
import { useTheme, font } from '@/lib/theme';

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
  }>();

  const inflow = p.dir === 'in';
  const amount = Number(p.amount || 0);
  const mono = (p.type || 'TX').split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  // Status badge reflects the real status — not always "success". The exported
  // file carries the same status: a failed transaction must never leave this
  // screen as a document stamped Successful.
  const status = p.status || 'Successful';
  const sl = status.toLowerCase();
  const statusColor = sl === 'failed' ? c.red : sl === 'pending' ? c.amber : c.lime;
  const statusIcon = sl === 'failed' ? 'x' : sl === 'pending' ? 'history' : 'check';

  const rows: ReceiptRow[] = [
    ['Description', p.type || 'Transaction'],
    ...(p.detail ? ([['Date', p.detail]] as ReceiptRow[]) : []),
    ['Reference', p.reference || '—'],
    ['Channel', 'Zitch Wallet'],
    ['Amount', (inflow ? '+' : '-') + money(Math.abs(amount)), true],
  ];

  return (
    <Screen>
      <Header title="Transaction details" onBack={() => router.back()} />

      {/* The capture target for the JPEG export — explicit background so the
          shared image isn't transparent-turned-black on Android. */}
      <View ref={card} collapsable={false} style={{ backgroundColor: c.bg }}>
        <View style={{ alignItems: 'center', paddingTop: 16 }}>
          <Monogram text={mono} color={inflow ? c.lime : c.brand} size={64} />
          <Text style={{ fontSize: 32, fontFamily: font.extrabold, color: inflow ? c.lime : c.ink1, marginTop: 14, fontVariant: ['tabular-nums'] }}>
            {(inflow ? '+' : '-') + money(Math.abs(amount))}
          </Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 8, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999, backgroundColor: `${statusColor}1F` }}>
            <ZIcon name={statusIcon} size={13} color={statusColor} />
            <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: statusColor }}>{status}</Text>
          </View>
        </View>

        <View style={{ marginTop: 22, marginBottom: 4, borderRadius: 18, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, paddingHorizontal: 16, paddingBottom: 8 }}>
          <Row2 k="Description" v={p.type || 'Transaction'} />
          {p.detail ? <Row2 k="Date" v={p.detail} /> : null}
          <Row2 k="Reference" v={p.reference || '—'} />
          <Row2 k="Channel" v="Zitch Wallet" />
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
            return captureRef(card, { format: 'jpg', quality: 0.95, result: 'tmpfile' });
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
