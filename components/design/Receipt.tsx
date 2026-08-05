import React, { useRef, useState } from 'react';
import { View, Text, Pressable, ActivityIndicator, ScrollView } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import ZIcon from '@/components/design/ZIcon';
import { Btn, Sheet } from '@/components/design/ui';
import { NText } from '@/components/design/Naira';
import { flash, notifyError } from '@/components/design/Notify';
import {
  ReceiptFormat, ReceiptRow, outcomeMessage, receiptHtml, receiptStamp, saveReceipt, shareReceipt,
} from '@/lib/receipt';
import { useTheme, font } from '@/lib/theme';

// Full-screen success receipt shown after a completed purchase.
//
// The rows a caller passes describe WHAT happened; the three rows this component
// adds — date, time, reference — describe WHICH transaction, and every screen
// needs them, so they live here rather than being re-typed ten times. The stamp is
// taken once, when the receipt first renders, so it can't tick forward while the
// user is looking at it or differ between the screen and the exported file.
const Receipt = ({
  title,
  message,
  rows,
  reference,
  onDone,
}: {
  title: string;
  message: string;
  rows: ReceiptRow[];
  reference?: string;
  onDone: () => void;
}) => {
  const { c } = useTheme();
  const card = useRef<View>(null);
  const [format, setFormat] = useState<null | { action: 'save' | 'share' }>(null);
  const [busy, setBusy] = useState(false);
  // Lazy initial state, never set again: the clock is read once, on the render
  // that first shows the receipt, and that reading is what every copy of it —
  // screen, JPEG, PDF — reports.
  const [stamp] = useState(receiptStamp);

  // Date and time always; the reference only when the server actually gave us one
  // — an invented reference on a receipt is worse than no reference at all.
  const allRows: ReceiptRow[] = [
    ...rows,
    ['Date', stamp.date],
    ['Time', stamp.time],
    ...(reference ? ([['Reference', reference]] as ReceiptRow[]) : []),
  ];

  // Plain-text rendering of the whole receipt, used by "Copy ref" when there is no
  // reference to copy.
  const asText = [title, message, '', ...allRows.map(([k, v]) => `${k}: ${v}`), '', 'Zitch'].join('\n');

  const run = async (action: 'save' | 'share', fmt: ReceiptFormat) => {
    setFormat(null);
    setBusy(true);
    const source = {
      capture: async () => {
        const { captureRef } = await import('react-native-view-shot');
        return captureRef(card, { format: 'jpg', quality: 0.95, result: 'tmpfile' });
      },
      html: receiptHtml({ title, message, rows: allRows }),
      reference: reference || '',
    };
    try {
      const outcome = await (action === 'save' ? saveReceipt : shareReceipt)(fmt, source);
      const line = outcomeMessage(outcome, fmt);
      if (!line) return;                                    // user backed out
      // Titled by what actually happened, not what was asked for: saving a PDF on
      // iOS goes through the system sheet, so "Saved" there would be a claim we
      // can't make.
      if (outcome === 'saved' || outcome === 'shared') flash(outcome === 'saved' ? 'Saved' : 'Ready', line);
      else notifyError(outcome === 'denied' ? 'Permission needed' : 'Receipt', line);
    } finally {
      setBusy(false);
    }
  };

  const onCopyRef = async () => {
    await Clipboard.setStringAsync(reference || asText);
    flash('Copied', reference ? 'Reference copied' : 'Receipt copied');
  };

  const actions: [string, string, () => void][] = [
    ['download', 'Save', () => setFormat({ action: 'save' })],
    ['share', 'Share', () => setFormat({ action: 'share' })],
    ['copy', 'Copy ref', onCopyRef],
  ];

  const FORMATS: [ReceiptFormat, string, string, string][] = [
    ['jpeg', 'image', 'JPEG image', 'Best for WhatsApp and chat'],
    ['pdf', 'file', 'PDF document', 'Best for email, print and records'],
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
        <View ref={card} collapsable={false} style={{ backgroundColor: c.bg, paddingBottom: 4 }}>
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

      <Sheet
        open={format !== null}
        onClose={() => setFormat(null)}
        title={format?.action === 'share' ? 'Share receipt as' : 'Save receipt as'}
      >
        {FORMATS.map(([fmt, icon, label, hint]) => (
          <Pressable
            key={fmt}
            onPress={() => run(format?.action ?? 'save', fmt)}
            accessibilityRole="button"
            accessibilityLabel={label}
            style={({ pressed }) => ({ flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 16, paddingHorizontal: 16, borderRadius: 18, borderWidth: 1.5, borderColor: c.line, backgroundColor: pressed ? c.line : 'transparent', marginBottom: 10 })}
          >
            <ZIcon name={icon} size={22} color={c.brand} />
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>{label}</Text>
              <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.regular, marginTop: 2 }}>{hint}</Text>
            </View>
            <ZIcon name="right" size={18} color={c.ink3} />
          </Pressable>
        ))}
      </Sheet>

      <View style={{ paddingBottom: 24, paddingTop: 12 }}>
        <Btn label="Back to Dashboard" onPress={onDone} />
      </View>
    </View>
  );
};

export default Receipt;
