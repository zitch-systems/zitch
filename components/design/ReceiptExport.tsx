import React from 'react';
import { Pressable, Text, View } from 'react-native';
import ZIcon from '@/components/design/ZIcon';
import { Sheet } from '@/components/design/ui';
import { flash, notifyError } from '@/components/design/Notify';
import {
  ReceiptFormat, ReceiptSource, outcomeMessage, saveReceipt, shareReceipt,
} from '@/lib/receipt';
import { useTheme, font } from '@/lib/theme';

export type ExportAction = 'save' | 'share';

// One entry per export format. Two screens offer these (the post-purchase
// receipt and the transaction-history detail), and a chooser that drifts
// between them would read as two different features — so it lives here, once.
const FORMATS: [ReceiptFormat, string, string, string][] = [
  ['jpeg', 'image', 'JPEG image', 'Best for WhatsApp and chat'],
  ['pdf', 'file', 'PDF document', 'Best for email, print and records'],
];

/**
 * The "Save/Share receipt as…" sheet plus the export it triggers.
 *
 * `source` is a function, not a value: the capture ref and HTML are read at the
 * moment the user picks a format, so the export always reflects the card as it
 * is on screen right then — never a snapshot from an earlier render.
 */
const ReceiptExport = ({
  action,
  onClose,
  source,
  onBusy,
}: {
  action: ExportAction | null;
  onClose: () => void;
  source: () => ReceiptSource;
  onBusy?: (busy: boolean) => void;
}) => {
  const { c } = useTheme();

  const run = async (fmt: ReceiptFormat) => {
    const act = action ?? 'save';
    onClose();
    onBusy?.(true);
    // Let this sheet finish its dismiss animation before presenting the next
    // system UI. On iOS, presenting the share sheet (or the SAF picker on
    // Android) while a modal is still animating out fails — the same race every
    // other screen avoids with a ~320ms pause after closing a sheet.
    await new Promise((resolve) => setTimeout(resolve, 350));
    try {
      const outcome = await (act === 'save' ? saveReceipt : shareReceipt)(fmt, source());
      const line = outcomeMessage(outcome, fmt);
      if (!line) return;                                    // user backed out
      // Titled by what actually happened, not what was asked for: saving a PDF
      // on iOS goes through the system sheet, so "Saved" there would be a claim
      // we can't make.
      if (outcome === 'saved' || outcome === 'shared') flash(outcome === 'saved' ? 'Saved' : 'Ready', line);
      else notifyError(outcome === 'denied' ? 'Permission needed' : 'Receipt', line);
    } finally {
      onBusy?.(false);
    }
  };

  return (
    <Sheet
      open={action !== null}
      onClose={onClose}
      title={action === 'share' ? 'Share receipt as' : 'Save receipt as'}
    >
      {FORMATS.map(([fmt, icon, label, hint]) => (
        <Pressable
          key={fmt}
          onPress={() => run(fmt)}
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
  );
};

export default ReceiptExport;
