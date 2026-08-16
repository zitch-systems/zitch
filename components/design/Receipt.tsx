import React, { useRef, useState } from 'react';
import { View, Text, Pressable, ActivityIndicator, ScrollView } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import ZIcon from '@/components/design/ZIcon';
import { Btn } from '@/components/design/ui';
import { NText } from '@/components/design/Naira';
import { flash } from '@/components/design/Notify';
import ReceiptExport, { ExportAction } from '@/components/design/ReceiptExport';
import Watermark from '@/components/design/Watermark';
import { ReceiptRow, receiptHtml, receiptStamp, senderRows } from '@/lib/receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

// Full-screen success receipt shown after a completed purchase.
//
// The rows a caller passes describe WHAT happened; the rows this component adds
// describe WHO paid (sender) and WHICH transaction (date, time, reference), and
// every screen needs them, so they live here rather than being re-typed ten
// times. Sender in particular has to be central: a receipt is forwarded to the
// person who was paid, and one that names only the recipient answers half the
// question it exists to answer — the reader cannot see which account the money
// actually left. The stamp is taken once, when the receipt first renders, so it
// can't tick forward while the user is looking at it or differ between the
// screen and the exported file.
const Receipt = ({
  title,
  message,
  rows,
  reference,
  status = 'Successful',
  onDone,
}: {
  title: string;
  message: string;
  rows: ReceiptRow[];
  reference?: string;
  /** Stamped on the exported PDF's badge. Pass the real state for a pending
   *  transaction — a shared document must never claim success early. */
  status?: string;
  onDone: () => void;
}) => {
  const { c } = useTheme();
  const card = useRef<View>(null);
  const [action, setAction] = useState<ExportAction | null>(null);
  const [busy, setBusy] = useState(false);
  // Lazy initial state, never set again: the clock is read once, on the render
  // that first shows the receipt, and that reading is what every copy of it —
  // screen, JPEG, PDF — reports.
  const [stamp] = useState(receiptStamp);

  // The wallet is the sender on every receipt this component renders — these
  // screens all spend from it. senderRows drops whatever the wallet hasn't
  // loaded yet rather than printing a blank row onto a document people treat as
  // proof. accountName is the bank's legal name for the NUBAN; firstName is a
  // greeting and only stands in when the fuller name isn't there.
  const { accountName, firstName, accountNumber, bankName } = useWallet();
  const from = senderRows({
    name: accountName || firstName,
    account: accountNumber,
    bank: bankName,
  });

  // Date and time always; the reference only when the server actually gave us one
  // — an invented reference on a receipt is worse than no reference at all.
  const allRows: ReceiptRow[] = [
    ...rows,
    ...from,
    ['Date', stamp.date],
    ['Time', stamp.time],
    ...(reference ? ([['Reference', reference]] as ReceiptRow[]) : []),
  ];

  // Plain-text rendering of the whole receipt, used by "Copy ref" when there is no
  // reference to copy.
  const asText = [title, message, '', ...allRows.map(([k, v]) => `${k}: ${v}`), '', 'Zitch'].join('\n');

  const onCopyRef = async () => {
    await Clipboard.setStringAsync(reference || asText);
    flash('Copied', reference ? 'Reference copied' : 'Receipt copied');
  };

  const actions: [string, string, () => void][] = [
    ['download', 'Save', () => setAction('save')],
    ['share', 'Share', () => setAction('share')],
    ['copy', 'Copy ref', onCopyRef],
  ];

  return (
    <View style={{ flex: 1, paddingHorizontal: 22 }}>
      {/* Scrollable now that every receipt carries date, time and reference: nine
          rows plus the success mark overflows a small phone, and a receipt whose
          last line is cut off is the one line someone needed. */}
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: 8 }} showsVerticalScrollIndicator={false}>
        {/* Everything inside this view is what gets captured as the JPEG. An
            explicit background matters: capturing a transparent tree renders
            black on Android. */}
        <View ref={card} collapsable={false} style={{ backgroundColor: c.bg, paddingBottom: 4, position: 'relative' }}>
          <View style={{ alignItems: 'center', paddingTop: 40 }}>
            <View style={{ width: 110, height: 110, borderRadius: 55, backgroundColor: 'rgba(0,181,29,.14)', alignItems: 'center', justifyContent: 'center' }}>
              <View style={{ width: 78, height: 78, borderRadius: 39, backgroundColor: c.lime, alignItems: 'center', justifyContent: 'center' }}>
                <ZIcon name="check" size={40} color="#fff" stroke={3} />
              </View>
            </View>
            <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, marginTop: 22 }}>{title}</Text>
            <Text style={{ fontSize: 14, color: c.ink3, marginTop: 8, textAlign: 'center', maxWidth: 290, fontFamily: font.regular }}>{message}</Text>
          </View>

          <View style={{ marginTop: 28, borderRadius: 22, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, paddingHorizontal: 18, paddingVertical: 6 }}>
            {allRows.map((r, i) => (
              <View key={i} style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 11, borderTopWidth: i === 0 ? 0 : 1, borderTopColor: c.line }}>
                <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>{r[0]}</Text>
                <NText style={{ fontSize: r[2] ? 16 : 14, fontFamily: r[2] ? font.extrabold : font.semibold, color: c.ink1, fontVariant: ['tabular-nums'], maxWidth: '60%', textAlign: 'right' }}>{r[1]}</NText>
              </View>
            ))}
          </View>

          {/* Inside the captured view: the saved JPEG is forwarded on its own, with
              no app around it, so without this the artifact carried no indication
              of where it came from — the PDF had a wordmark and footer, the image
              had neither. */}
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 16 }}>
            <Text style={{ fontSize: 15, fontFamily: font.extrabold, color: c.brand, letterSpacing: -0.3 }}>zitch</Text>
            <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3 }}>· zitch.ng</Text>
          </View>

          {/* Last child so it lies over the rows — see Watermark for why that is
              deliberate. Inside the captured view, so it rides along on the JPEG. */}
          <Watermark />
        </View>

        <View style={{ flexDirection: 'row', gap: 10, marginTop: 16 }}>
          {actions.map(([ic, lb, fn]) => (
            <Pressable
              key={ic}
              onPress={fn}
              disabled={busy}
              accessibilityRole="button"
              accessibilityLabel={lb}
              accessibilityState={{ disabled: busy }}
              style={({ pressed }) => ({ flex: 1, alignItems: 'center', gap: 6, paddingVertical: 14, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, opacity: pressed || busy ? 0.85 : 1 })}
            >
              {busy && ic !== 'copy'
                ? <ActivityIndicator size="small" color={c.brand} style={{ height: 20 }} />
                : <ZIcon name={ic} size={20} color={c.brand} />}
              <Text style={{ fontSize: 12, fontFamily: font.semibold, color: c.ink2 }}>{lb}</Text>
            </Pressable>
          ))}
        </View>
      </ScrollView>

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
          html: receiptHtml({ title, message, rows: allRows, status }),
          reference: reference || '',
        })}
      />

      <View style={{ paddingBottom: 24, paddingTop: 12 }}>
        <Btn label="Back to Dashboard" onPress={onDone} />
      </View>
    </View>
  );
};

export default Receipt;
