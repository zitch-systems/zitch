import React, { useRef } from 'react';
import { View, Text, Pressable, Share, ScrollView } from 'react-native';
import { router } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { LinearGradient } from 'expo-linear-gradient';
import ZIcon from '@/components/design/ZIcon';
import { Btn } from '@/components/design/ui';
import { ZMark } from '@/components/design/Brand';
import { NText } from '@/components/design/Naira';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';

// Full-screen success receipt shown after a completed purchase. Mirrors the v2
// SuccessReceipt: teal gradient header with the Zitch badge + check, a detail
// card watermarked with "ZITCH", a generated reference, working Save/Share/Copy
// actions, and a Bank-on-WhatsApp promo above "Back to Dashboard".
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

  const receiptText = () =>
    [title, '', ...rows.map(([k, v]) => `${k}: ${v}`), '', `Reference: ${refNo}`, '', 'Sent with Zitch'].join('\n');

  const onShare = async () => {
    try {
      await Share.share({ message: receiptText() });
    } catch {
      /* user dismissed */
    }
  };
  const onCopyRef = async () => {
    await Clipboard.setStringAsync(refNo);
    notify('Copied', 'Reference copied to clipboard');
  };

  // Save/Share both go through the native sheet (which offers "Save to Files"),
  // Copy ref uses the clipboard. A pixel-perfect PNG/PDF export would need
  // view-shot + a PDF lib (a follow-up requiring a native rebuild).
  const actions: [string, string, () => void][] = [
    ['download', 'Save', onShare],
    ['share', 'Share', onShare],
    ['copy', 'Copy ref', onCopyRef],
  ];

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 12 }} showsVerticalScrollIndicator={false}>
        {/* teal gradient header */}
        <LinearGradient
          colors={c.heroGradient}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={{ alignItems: 'center', paddingTop: 44, paddingBottom: 34, borderBottomLeftRadius: 28, borderBottomRightRadius: 28 }}
        >
          <ZMark size={40} badge />
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

          {/* actions */}
          <View style={{ flexDirection: 'row', gap: 10, marginTop: 16 }}>
            {actions.map(([ic, lb, fn]) => (
              <Pressable key={lb} onPress={fn} style={{ flex: 1 }}>
                <View style={{ alignItems: 'center', gap: 6, paddingVertical: 14, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line }}>
                  <ZIcon name={ic} size={20} color={c.brand} />
                  <Text style={{ fontSize: 12, fontFamily: font.semibold, color: c.ink2 }}>{lb}</Text>
                </View>
              </Pressable>
            ))}
          </View>

          {/* Bank on WhatsApp promo */}
          <Pressable onPress={() => router.push('/linkwhatsapp')} style={{ marginTop: 16 }}>
            <LinearGradient colors={['#25D366', '#128C7E']} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 18 }}>
              <View style={{ width: 38, height: 38, borderRadius: 12, backgroundColor: 'rgba(255,255,255,.2)', alignItems: 'center', justifyContent: 'center' }}>
                <ZIcon name="chat" size={20} color="#fff" />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={{ fontSize: 13.5, fontFamily: font.bold, color: '#fff' }}>Bank on WhatsApp</Text>
                <Text style={{ fontSize: 11.5, color: 'rgba(255,255,255,.9)', fontFamily: font.regular }}>Pay & check balance from your chats</Text>
              </View>
              <View style={{ paddingVertical: 6, paddingHorizontal: 14, borderRadius: 999, backgroundColor: '#fff' }}>
                <Text style={{ color: '#128C7E', fontFamily: font.bold, fontSize: 12 }}>Chat</Text>
              </View>
            </LinearGradient>
          </Pressable>
        </View>
      </ScrollView>

      <View style={{ paddingHorizontal: 22, paddingBottom: 24, paddingTop: 12 }}>
        <Btn label="Back to Dashboard" onPress={onDone} />
      </View>
    </View>
  );
};

export default Receipt;
