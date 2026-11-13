import React, { useRef, useState } from 'react';
import { router } from 'expo-router';
import { View, Text, Pressable, ScrollView, Image, StyleSheet } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import Svg, { Path } from 'react-native-svg';
import * as Clipboard from 'expo-clipboard';
import ZIcon from '@/components/design/ZIcon';
import { LoadingMark } from '@/components/design/Loading';
import { Btn } from '@/components/design/ui';
import { NText } from '@/components/design/Naira';
import { flash, notifyError } from '@/components/design/Notify';
import ReceiptExport, { ExportAction } from '@/components/design/ReceiptExport';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { BANK_WHATSAPP_DISPLAY, SUPPORT_EMAIL } from '@/components/configFiles/links';
import {
  RECEIPT_COLORS as C,
  ReceiptRow,
  WHATSAPP_START_URL,
  qrPath,
  receiptHtml,
  receiptLayout,
  receiptStamp,
  senderRows,
  statusTone,
} from '@/lib/receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { transactionStatusPresentation } from '@/lib/transactionStatus';

const MARK = require('@/assets/images/zitch-mark.png');
// The code never changes within a session, so it is encoded once.
const QR = qrPath(WHATSAPP_START_URL);

// The receipt shown after a completed purchase, and the document it exports.
//
// Laid out as the WhatsApp receipt is (backend whatsapp/receipt.py), so a
// customer meets one document wherever they paid. The order is a teal Zitch
// header carrying the receipt's title, then a white ticket. The ticket holds
// the amount, the status pill and the time, a tear line, then the rows as
// `receiptLayout` arranges them. Under the ticket come Zitch WhatsApp banking
// with a code to scan, and where to get help. It keeps the light palette in
// every app theme: it is a document people forward, and a dark screenshot
// reads as someone else's paper.
//
// The rows a caller passes describe WHAT happened. The rows this component adds
// describe WHO paid (sender) and WHICH transaction (date, time, reference).
// Every screen needs those, so they live here rather than being re-typed ten
// times. The stamp is taken once, when the receipt first renders, so it cannot
// tick forward while the user is looking at it, or differ between the screen
// and the exported file.

const StatusPill = ({ status }: { status: string }) => {
  const view = transactionStatusPresentation(status);
  const tone = statusTone(view.state);
  return (
    <View
      accessibilityLabel={`Transaction status: ${view.label}`}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 12, paddingLeft: 6, paddingRight: 13, paddingVertical: 5, borderRadius: 999, backgroundColor: tone.bg }}
    >
      <View style={{ width: 20, height: 20, borderRadius: 10, backgroundColor: tone.mark, alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name={view.icon} size={12} color="#fff" stroke={3} />
      </View>
      <Text style={{ fontSize: 13, fontFamily: font.bold, color: tone.fg }}>{view.label}</Text>
    </View>
  );
};

// A dashed rule between two half-circle bites out of the ticket's edges. The
// dashes are views: Android draws a one-sided dashed border solid.
const TearLine = () => (
  <View style={{ height: 22, justifyContent: 'center' }}>
    <View style={{ flexDirection: 'row', marginHorizontal: 16 }}>
      {Array.from({ length: 34 }).map((_, i) => (
        <View key={i} style={{ flex: 1, height: 1.5, marginHorizontal: 2, backgroundColor: C.tear }} />
      ))}
    </View>
    <View style={{ position: 'absolute', left: -11, width: 22, height: 22, borderRadius: 11, backgroundColor: C.paper }} />
    <View style={{ position: 'absolute', right: -11, width: 22, height: 22, borderRadius: 11, backgroundColor: C.paper }} />
  </View>
);

const Entry = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', paddingVertical: 12, gap: 14 }}>
    <Text style={{ fontSize: 13.5, color: C.muted, fontFamily: font.regular }}>{label}</Text>
    <View style={{ flex: 1, alignItems: 'flex-end' }}>{children}</View>
  </View>
);

// The Z mark tiled faintly over the ticket, the last child so it lies over the
// rows: a crop cannot lift a clean region out of the middle, and it rides along
// on the saved JPEG. Faint enough never to fight the amount. The reference and
// our records are what prove a payment, not this.
const Texture = () => (
  <View pointerEvents="none" style={[StyleSheet.absoluteFill, { opacity: 0.045, overflow: 'hidden' }]}>
    {Array.from({ length: 12 }).map((_, row) => (
      <View key={row} style={{ flexDirection: 'row', gap: 46, marginLeft: row % 2 ? 26 : -6, height: 60 }}>
        {Array.from({ length: 7 }).map((__, col) => (
          <Image key={col} source={MARK} resizeMode="contain" style={{ width: 16, height: 22, tintColor: C.brand }} />
        ))}
      </View>
    ))}
  </View>
);

// Zitch WhatsApp banking, as on the WhatsApp receipt. Every receipt is
// forwarded to someone, which makes it the one place the offer reaches people
// who do not bank with Zitch yet. The code opens a chat with the banking number
// with "Hi" typed; the number is printed for someone reading on the phone they
// would scan with. On screen, a tap opens the account-linking flow.
const WhatsAppPanel = () => (
  <Pressable
    onPress={() => router.push('/linkwhatsapp')}
    accessibilityRole="button"
    accessibilityLabel="Set up Zitch banking on WhatsApp"
    style={({ pressed }) => ({
      marginHorizontal: 12, marginTop: 14, borderRadius: 18, borderWidth: 1.5,
      borderColor: 'rgba(37,211,102,.45)', padding: 12, flexDirection: 'row', alignItems: 'center',
      gap: 11, opacity: pressed ? 0.88 : 1,
    })}
  >
    <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: C.whatsapp, alignItems: 'center', justifyContent: 'center', alignSelf: 'flex-start' }}>
      <WhatsAppGlyph size={22} color="#fff" />
    </View>
    <View style={{ flex: 1, minWidth: 0 }}>
      <Text style={{ color: C.whatsappText, fontFamily: font.bold, fontSize: 9.5, letterSpacing: 0.8 }}>ZITCH WHATSAPP BANKING</Text>
      <Text style={{ color: C.ink, fontFamily: font.extrabold, fontSize: 14, marginTop: 2 }}>Bank wherever you chat</Text>
      <Text style={{ color: C.muted, fontFamily: font.regular, fontSize: 11, lineHeight: 15, marginTop: 2 }}>
        Send money, buy airtime and check your balance on WhatsApp.
      </Text>
      <Text selectable style={{ color: C.muted, fontFamily: font.semibold, fontSize: 11, marginTop: 5 }}>
        <Text style={{ color: C.brandText, fontFamily: font.bold }}>zitch.ng</Text>
        {'  ·  '}
        {BANK_WHATSAPP_DISPLAY}
      </Text>
    </View>
    <View style={{ alignItems: 'center' }} accessibilityLabel="QR code to start Zitch banking on WhatsApp">
      <View style={{ backgroundColor: '#fff', borderRadius: 8, padding: 4 }}>
        <Svg width={68} height={68} viewBox={`0 0 ${QR.size} ${QR.size}`}>
          <Path d={QR.d} fill={C.ink} />
        </Svg>
      </View>
      <Text style={{ fontSize: 9, fontFamily: font.bold, color: C.muted, marginTop: 4 }}>Scan to start</Text>
    </View>
  </Pressable>
);

const Receipt = ({
  title,
  message,
  rows,
  reference,
  status = 'Successful',
  onDone,
  footer,
}: {
  title: string;
  message: string;
  rows: ReceiptRow[];
  reference?: string;
  /** Stamped on the status pill and the exported PDF. Pass the real state for a
   *  pending transaction — a shared document must never claim success early. */
  status?: string;
  onDone: () => void;
  /** An optional offer under the receipt — "save this recipient" and the like.
   *  Kept out of the exported document on purpose: a receipt is forwarded as
   *  proof of payment, and a button the reader cannot press is noise on it. */
  footer?: React.ReactNode;
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
  const layout = receiptLayout({ title, rows: allRows, status });

  // Plain-text rendering of the whole receipt, used by "Copy ref" when there is no
  // reference to copy.
  const asText = [title, message, '', ...allRows.map(([k, v]) => `${k}: ${v}`), '', 'Zitch'].join('\n');

  const onCopyRef = async () => {
    try {
      await Clipboard.setStringAsync(reference || asText);
      flash('Copied', reference ? 'Reference copied' : 'Receipt copied');
    } catch { notifyError('Could not copy', 'Please try again.'); }
  };

  const actions: [string, string, () => void][] = [
    ['download', 'Save', () => setAction('save')],
    ['share', 'Share', () => setAction('share')],
    ['copy', 'Copy ref', onCopyRef],
  ];

  const entries: React.ReactNode[] = [
    ...layout.parties.map((party) => (
      <Entry key={`p-${party.label}`} label={party.label}>
        <Text style={{ fontSize: 14, fontFamily: font.bold, color: C.ink, textAlign: 'right' }}>{party.name}</Text>
        {party.detail ? (
          <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: C.muted, textAlign: 'right', marginTop: 2 }}>{party.detail}</Text>
        ) : null}
      </Entry>
    )),
    ...layout.rows.map(([label, value, strong], i) => (
      <Entry key={`r-${i}`} label={label}>
        <NText
          selectable={label === 'Reference'}
          style={{ fontSize: strong ? 15.5 : 14, fontFamily: strong ? font.extrabold : font.bold, color: C.ink, textAlign: 'right', fontVariant: ['tabular-nums'] }}
        >
          {value}
        </NText>
      </Entry>
    )),
  ];

  return (
    <View style={{ flex: 1, paddingHorizontal: 16 }}>
      {/* Scrollable: every receipt carries the parties, the time and the
          reference, and a receipt whose last line is cut off on a small phone is
          the one line someone needed. */}
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingTop: 12, paddingBottom: 8 }} showsVerticalScrollIndicator={false}>
        {/* Rounded on screen by this wrapper; the captured view inside is square,
            because a JPEG has no transparency and rounded corners would export
            as black ones on Android. */}
        <View style={{ borderRadius: 24, overflow: 'hidden' }}>
          {/* Everything inside this view is what gets captured as the JPEG. An
              explicit background matters: capturing a transparent tree renders
              black on Android. */}
          <View ref={card} collapsable={false} style={{ backgroundColor: C.paper, paddingBottom: 16 }}>
            <LinearGradient
              colors={[C.brand, C.brandDeep]}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={{ height: 150, paddingHorizontal: 18, paddingTop: 18, overflow: 'hidden' }}
            >
              <Image source={MARK} resizeMode="contain" style={{ position: 'absolute', right: -30, top: -36, width: 150, height: 206, tintColor: '#fff', opacity: 0.08 }} />
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <Image source={MARK} resizeMode="contain" style={{ width: 20, height: 27, tintColor: '#fff' }} />
                  <Text style={{ fontSize: 21, fontFamily: font.extrabold, color: '#fff', letterSpacing: -0.3 }}>zitch</Text>
                </View>
                <Text numberOfLines={1} style={{ flexShrink: 1, marginLeft: 12, fontSize: 12.5, fontFamily: font.bold, color: C.brandTint }}>
                  {layout.title}
                </Text>
              </View>
            </LinearGradient>

            {/* The ticket: a shadow on the outer view, the clip on the inner one,
                because iOS drops the shadow of a view that clips its children. */}
            <View style={{ marginTop: -86, marginHorizontal: 12, borderRadius: 20, backgroundColor: C.card, shadowColor: C.brandDeep, shadowOpacity: 0.12, shadowRadius: 14, shadowOffset: { width: 0, height: 6 }, elevation: 3 }}>
              <View style={{ borderRadius: 20, overflow: 'hidden', backgroundColor: C.card }}>
                <View style={{ alignItems: 'center', paddingTop: 22, paddingHorizontal: 18, paddingBottom: 14 }}>
                  <NText
                    numberOfLines={1}
                    adjustsFontSizeToFit
                    style={{ fontSize: layout.amount ? 34 : 22, fontFamily: font.extrabold, color: C.ink, letterSpacing: -0.5, fontVariant: ['tabular-nums'] }}
                  >
                    {layout.amount || layout.title}
                  </NText>
                  <StatusPill status={status} />
                  {layout.when ? (
                    <Text style={{ fontSize: 13, fontFamily: font.medium, color: C.muted, marginTop: 10 }}>{layout.when}</Text>
                  ) : null}
                  {message ? (
                    <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: C.muted, marginTop: 6, textAlign: 'center', lineHeight: 18, maxWidth: 290 }}>
                      {message}
                    </Text>
                  ) : null}
                </View>

                <TearLine />

                <View style={{ paddingHorizontal: 16, paddingBottom: 6 }}>
                  {layout.highlights.map(([label, value], i) => (
                    <View key={`h-${i}`} style={{ backgroundColor: C.brandTint, borderRadius: 14, paddingHorizontal: 14, paddingVertical: 12, marginTop: 6, marginBottom: 4 }}>
                      <Text style={{ fontSize: 10.5, fontFamily: font.extrabold, color: C.brandText, letterSpacing: 1 }}>{label.toUpperCase()}</Text>
                      <Text selectable style={{ fontSize: 19, fontFamily: font.extrabold, color: C.ink, letterSpacing: 0.6, marginTop: 4 }}>{value}</Text>
                    </View>
                  ))}
                  {entries.map((entry, i) => (
                    <React.Fragment key={i}>
                      {i > 0 ? <View style={{ height: 1, backgroundColor: C.line }} /> : null}
                      {entry}
                    </React.Fragment>
                  ))}
                </View>

                <Texture />
              </View>
            </View>

            <WhatsAppPanel />

            <View style={{ alignItems: 'center', marginTop: 14, gap: 4 }}>
              <Text style={{ fontSize: 12, fontFamily: font.regular, color: C.muted }}>Need help? {SUPPORT_EMAIL}</Text>
              <Text style={{ fontSize: 11, fontFamily: font.regular, color: C.muted }}>Keep this receipt for your records.</Text>
            </View>
          </View>
        </View>
      </ScrollView>

      {/* Receipt actions stay pinned above the dashboard button. The details can
          scroll independently, but Save/Share must be visible the moment the
          success page opens, including on short phones. */}
      <View style={{ flexDirection: 'row', gap: 10, marginTop: 12 }}>
        {actions.map(([ic, lb, fn]) => (
          <Pressable
            key={ic}
            onPress={fn}
            disabled={busy}
            accessibilityRole="button"
            accessibilityLabel={lb}
            accessibilityState={{ disabled: busy }}
            style={({ pressed }) => ({ flex: 1, alignItems: 'center', gap: 6, paddingVertical: 12, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, opacity: pressed || busy ? 0.85 : 1 })}
          >
            {busy && ic !== 'copy'
              ? <LoadingMark size={20} />
              : <ZIcon name={ic} size={20} color={c.brand} />}
            <Text style={{ fontSize: 12, fontFamily: font.semibold, color: c.ink2 }}>{lb}</Text>
          </Pressable>
        ))}
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
          html: receiptHtml({ title, message, rows: allRows, status }),
          reference: reference || '',
        })}
      />

      {footer}

      <View style={{ paddingBottom: 24, paddingTop: 10 }}>
        <Btn label="Back to Dashboard" onPress={onDone} />
      </View>
    </View>
  );
};

export default Receipt;
