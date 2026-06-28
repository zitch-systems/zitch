import React, { useRef, useState } from 'react';
import { View, Text, Pressable, Share, ScrollView } from 'react-native';
import { router } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import * as Sharing from 'expo-sharing';
import * as MediaLibrary from 'expo-media-library';
import * as Print from 'expo-print';
import { captureRef } from 'react-native-view-shot';
import { LinearGradient } from 'expo-linear-gradient';
import ZIcon from '@/components/design/ZIcon';
import { Btn, Sheet, Tap } from '@/components/design/ui';
import { ZMark } from '@/components/design/Brand';
import { NText } from '@/components/design/Naira';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';

// Full-screen success receipt shown after a completed purchase. Mirrors the v2
// SuccessReceipt: teal gradient header with the Zitch badge + check, a detail
// card watermarked with "ZITCH", a generated reference, working Save/Share/Copy
// actions (each Save/Share opens an image-PNG / PDF chooser, rendered from the
// captured receipt view), and a Bank-on-WhatsApp promo above "Back to Dashboard".
const Receipt = ({
  title,
  message,
  rows,
  reference,
  onDone,
}: {
  title: string;
  message: string;
  rows: [string, string, boolean?][];
  reference?: string;
  onDone: () => void;
}) => {
  const { c } = useTheme();
  // Stable reference: use the one the API returned, else generate once so it
  // doesn't change between renders.
  const refNo = useRef(reference || `ZT${Date.now().toString(36).toUpperCase()}`).current;
  // The captured area (header + detail card) for the PNG/PDF export.
  const shotRef = useRef<View>(null);
  // null = closed; otherwise which action opened the format chooser.
  const [chooser, setChooser] = useState<null | 'save' | 'share'>(null);

  const receiptText = () =>
    [title, '', ...rows.map(([k, v]) => `${k}: ${v}`), '', `Reference: ${refNo}`, '', 'Sent with Zitch'].join('\n');

  const onCopyRef = async () => {
    await Clipboard.setStringAsync(refNo);
    notify('Copied', 'Reference copied to clipboard');
  };

  // ---- Export: PNG via view-shot, PDF via expo-print ----
  const exportPng = async (mode: 'save' | 'share') => {
    const uri = await captureRef(shotRef, { format: 'png', quality: 1 });
    if (mode === 'save') {
      const perm = await MediaLibrary.requestPermissionsAsync();
      if (!perm.granted) {
        notify('Permission needed', 'Allow photo access to save the receipt', 'error');
        return;
      }
      await MediaLibrary.saveToLibraryAsync(uri);
      notify('Saved', 'Receipt image saved to your gallery');
    } else if (await Sharing.isAvailableAsync()) {
      await Sharing.shareAsync(uri, { mimeType: 'image/png', dialogTitle: 'Share receipt' });
    } else {
      await Share.share({ message: receiptText() });
    }
  };

  const pdfHtml = () => `
    <html><head><meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      *{font-family:-apple-system,Roboto,'Helvetica Neue',sans-serif;box-sizing:border-box}
      body{margin:0;color:#06231F}
      .head{background:linear-gradient(135deg,#0C5249,#00847B 60%,#0FA295);color:#fff;
        text-align:center;padding:40px 24px;border-radius:0 0 24px 24px}
      .head h1{font-size:22px;margin:14px 0 4px}.head p{opacity:.9;margin:0;font-size:13px}
      .badge{font-weight:800;letter-spacing:.18em;font-size:14px}
      .card{margin:22px;border:1px solid #E2EEEB;border-radius:18px;padding:6px 18px}
      .row{display:flex;justify-content:space-between;padding:12px 0;border-top:1px solid #E2EEEB;font-size:14px}
      .row:first-child{border-top:0}.k{color:#6B7A77}.v{font-weight:700}
      .foot{text-align:center;color:#6B7A77;font-size:12px;margin-top:8px}
    </style></head><body>
      <div class="head"><div class="badge">ZITCH</div><h1>${title}</h1><p>${message}</p></div>
      <div class="card">
        ${rows.map(([k, v]) => `<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('')}
        <div class="row"><span class="k">Reference</span><span class="v">${refNo}</span></div>
      </div>
      <div class="foot">Sent with Zitch · Pay. Send. Grow.</div>
    </body></html>`;

  const exportPdf = async (mode: 'save' | 'share') => {
    const { uri } = await Print.printToFileAsync({ html: pdfHtml() });
    if (await Sharing.isAvailableAsync()) {
      await Sharing.shareAsync(uri, {
        mimeType: 'application/pdf',
        dialogTitle: mode === 'save' ? 'Save receipt PDF' : 'Share receipt',
        UTI: 'com.adobe.pdf',
      });
    } else {
      await Share.share({ message: receiptText() });
    }
  };

  const pick = async (fmt: 'png' | 'pdf') => {
    const mode = chooser ?? 'share';
    setChooser(null);
    try {
      if (fmt === 'png') await exportPng(mode);
      else await exportPdf(mode);
    } catch {
      // Any native/export failure degrades gracefully to the OS text share.
      try { await Share.share({ message: receiptText() }); } catch { /* dismissed */ }
    }
  };

  const actions: [string, string, () => void][] = [
    ['download', 'Save', () => setChooser('save')],
    ['share', 'Share', () => setChooser('share')],
    ['copy', 'Copy ref', onCopyRef],
  ];

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 12 }} showsVerticalScrollIndicator={false}>
        {/* Captured region: header + detail card (clean PNG/PDF without the buttons) */}
        <View ref={shotRef} collapsable={false} style={{ backgroundColor: c.bg }}>
          {/* teal gradient header */}
          <LinearGradient
            colors={c.heroGradient}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={{ alignItems: 'center', paddingTop: 44, paddingBottom: 34, borderBottomLeftRadius: 28, borderBottomRightRadius: 28 }}
          >
            <ZMark size={40} badge glow />
            <View style={{ width: 86, height: 86, borderRadius: 43, backgroundColor: 'rgba(255,255,255,.16)', alignItems: 'center', justifyContent: 'center', marginTop: 16 }}>
              <View style={{ width: 60, height: 60, borderRadius: 30, backgroundColor: c.lime, alignItems: 'center', justifyContent: 'center' }}>
                <ZIcon name="check" size={32} color="#fff" stroke={3} />
              </View>
            </View>
            <Text style={{ fontSize: 23, fontFamily: font.extrabold, color: '#fff', marginTop: 18 }}>{title}</Text>
            <Text style={{ fontSize: 13.5, color: 'rgba(255,255,255,.88)', marginTop: 6, textAlign: 'center', maxWidth: 300, fontFamily: font.regular }}>{message}</Text>
          </LinearGradient>

          <View style={{ paddingHorizontal: 22 }}>
            {/* detail card with a faint tiled ZITCH watermark */}
            <View style={{ marginTop: 18, borderRadius: 22, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, paddingHorizontal: 18, paddingVertical: 6, overflow: 'hidden' }}>
              <View pointerEvents="none" style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' }}>
                <Text style={{ fontSize: 40, fontFamily: font.extrabold, color: c.ink1, opacity: 0.035, transform: [{ rotate: '-24deg' }], letterSpacing: 6 }}>
                  ZITCH  ZITCH
                </Text>
              </View>
              {rows.map((r, i) => (
                <View key={i} style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 11, borderTopWidth: i === 0 ? 0 : 1, borderTopColor: c.line }}>
                  <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>{r[0]}</Text>
                  <NText style={{ fontSize: r[2] ? 16 : 14, fontFamily: r[2] ? font.extrabold : font.semibold, color: c.ink1, fontVariant: ['tabular-nums'], maxWidth: '60%', textAlign: 'right' }}>{r[1]}</NText>
                </View>
              ))}
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 11, borderTopWidth: 1, borderTopColor: c.line }}>
                <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>Reference</Text>
                <Text style={{ fontSize: 13, color: c.ink2, fontFamily: font.semibold, fontVariant: ['tabular-nums'] }}>{refNo}</Text>
              </View>
            </View>
          </View>
        </View>

        <View style={{ paddingHorizontal: 22 }}>
          {/* actions */}
          <View style={{ flexDirection: 'row', gap: 10, marginTop: 16 }}>
            {actions.map(([ic, lb, fn]) => (
              <Tap key={lb} onPress={fn} style={{ flex: 1 }}>
                <View style={{ alignItems: 'center', gap: 6, paddingVertical: 14, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line }}>
                  <ZIcon name={ic} size={20} color={c.brand} />
                  <Text style={{ fontSize: 12, fontFamily: font.semibold, color: c.ink2 }}>{lb}</Text>
                </View>
              </Tap>
            ))}
          </View>

          {/* Bank on WhatsApp promo */}
          <Tap onPress={() => router.push('/linkwhatsapp')} style={{ marginTop: 16 }}>
            <LinearGradient colors={['#25D366', '#128C7E']} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 18 }}>
              <View style={{ width: 38, height: 38, borderRadius: 12, backgroundColor: 'rgba(255,255,255,.2)', alignItems: 'center', justifyContent: 'center' }}>
                <WhatsAppGlyph size={22} color="#fff" />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={{ fontSize: 13.5, fontFamily: font.bold, color: '#fff' }}>Bank on WhatsApp</Text>
                <Text style={{ fontSize: 11.5, color: 'rgba(255,255,255,.9)', fontFamily: font.regular }}>Pay & check balance from your chats</Text>
              </View>
              <View style={{ paddingVertical: 6, paddingHorizontal: 14, borderRadius: 999, backgroundColor: '#fff' }}>
                <Text style={{ color: '#128C7E', fontFamily: font.bold, fontSize: 12 }}>Chat</Text>
              </View>
            </LinearGradient>
          </Tap>
        </View>
      </ScrollView>

      <View style={{ paddingHorizontal: 22, paddingBottom: 24, paddingTop: 12 }}>
        <Btn label="Back to Dashboard" onPress={onDone} />
      </View>

      {/* Save / Share format chooser */}
      <Sheet open={chooser !== null} onClose={() => setChooser(null)} title={chooser === 'save' ? 'Save receipt as' : 'Share receipt as'}>
        {([
          ['save', 'Image (PNG)', 'A shareable picture of your receipt', 'png'],
          ['download', 'PDF document', 'A printable PDF file', 'pdf'],
        ] as [string, string, string, 'png' | 'pdf'][]).map(([ic, lb, sub, fmt]) => (
          <Tap key={fmt} onPress={() => pick(fmt)} style={{ marginBottom: 12 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14, padding: 16, borderRadius: 16, backgroundColor: c.surface2, borderWidth: 1, borderColor: c.line }}>
              <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
                <ZIcon name={ic} size={22} color={c.brand} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>{lb}</Text>
                <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{sub}</Text>
              </View>
              <ZIcon name="right" size={18} color={c.ink3} />
            </View>
          </Tap>
        ))}
      </Sheet>
    </View>
  );
};

export default Receipt;
