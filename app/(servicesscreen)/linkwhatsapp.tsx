import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, Linking, ActivityIndicator } from 'react-native';
import { router } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { Screen, Header, Card, Btn } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { apiJson } from '@/lib/api';
import { useTheme, font } from '@/lib/theme';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { BANK_WHATSAPP } from '@/components/configFiles/links';

const WA_GREEN = '#25D366';

type Stage = 'loading' | 'unlinked' | 'code' | 'linked';

// Open WhatsApp at the Zitch banking number, optionally with prefilled text.
const openWa = (text?: string, link?: string) => {
  const url = link || `https://wa.me/${BANK_WHATSAPP}${text ? `?text=${encodeURIComponent(text)}` : ''}`;
  Linking.openURL(url).catch(() => notify('WhatsApp', 'Could not open WhatsApp. Make sure it is installed, then try again.'));
};

const Step = ({ n, text }: { n: number; text: string }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', gap: 12, alignItems: 'flex-start', marginBottom: 13 }}>
      <View style={{ width: 24, height: 24, borderRadius: 12, backgroundColor: c.surface3, alignItems: 'center', justifyContent: 'center' }}>
        <Text style={{ fontFamily: font.bold, fontSize: 12, color: c.brandDeep }}>{n}</Text>
      </View>
      <Text style={{ flex: 1, color: c.ink2, fontFamily: font.regular, fontSize: 13.5, lineHeight: 20 }}>{text}</Text>
    </View>
  );
};

const LinkWhatsApp = () => {
  const { c } = useTheme();
  const [stage, setStage] = useState<Stage>('loading');
  const [masked, setMasked] = useState('');
  const [code, setCode] = useState('');
  const [waLink, setWaLink] = useState('');
  const [busy, setBusy] = useState(false);
  const [polling, setPolling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setPolling(false);
  }, []);

  // Check whether this account already has an active WhatsApp link.
  const refreshStatus = useCallback(async (silent = false): Promise<boolean> => {
    const res = await apiJson<{ linked?: boolean; masked_number?: string }>('/api/whatsapp/link/status/');
    if (res?.linked) {
      setMasked(res.masked_number || '');
      setStage('linked');
      stopPoll();
      return true;
    }
    if (!silent) setStage((s) => (s === 'loading' ? 'unlinked' : s));
    return false;
  }, [stopPoll]);

  useEffect(() => { refreshStatus(); return () => stopPoll(); }, [refreshStatus, stopPoll]);

  const generate = async () => {
    setBusy(true);
    const res = await apiJson<{ success?: boolean; code?: string; wa_link?: string; message?: string }>('/api/whatsapp/link/start/');
    setBusy(false);
    if (res?.success && res.code) {
      setCode(res.code);
      setWaLink(res.wa_link || '');
      setStage('code');
      // Auto-detect the moment the user sends the code from WhatsApp.
      stopPoll();
      setPolling(true);
      pollRef.current = setInterval(() => { refreshStatus(true); }, 4000);
    } else {
      notify('Error', res?.message || 'Could not generate a code. Please try again.');
    }
  };

  const copyCode = async () => {
    await Clipboard.setStringAsync(`LINK ${code}`);
    notify('Copied', 'Paste it into your WhatsApp chat with Zitch.');
  };

  const unlink = async () => {
    setBusy(true);
    const res = await apiJson<{ success?: boolean; message?: string }>('/api/whatsapp/link/unlink/');
    setBusy(false);
    if (res?.success) {
      setCode(''); setMasked(''); setStage('unlinked');
      notify('Unlinked', 'Your WhatsApp has been disconnected.');
    } else {
      notify('Error', res?.message || 'Could not unlink. Please try again.');
    }
  };

  return (
    <Screen>
      <Header title="Bank on WhatsApp" onBack={() => router.back()} />

      {/* Hero badge */}
      <View style={{ alignItems: 'center', marginTop: 6, marginBottom: 22 }}>
        <View style={{ width: 76, height: 76, borderRadius: 22, backgroundColor: WA_GREEN, alignItems: 'center', justifyContent: 'center', shadowColor: '#075E54', shadowOpacity: 0.35, shadowRadius: 14, shadowOffset: { width: 0, height: 8 }, elevation: 8 }}>
          <WhatsAppGlyph size={40} color="#fff" />
        </View>
        <Text style={{ fontFamily: font.bold, fontSize: 18, color: c.ink1, marginTop: 14 }}>Bank on WhatsApp</Text>
        <Text style={{ fontFamily: font.regular, fontSize: 13.5, color: c.ink3, textAlign: 'center', marginTop: 6, lineHeight: 20, paddingHorizontal: 12 }}>
          Connect your WhatsApp to send money, buy airtime and check your balance right from your chats.
        </Text>
      </View>

      {stage === 'loading' && (
        <View style={{ paddingVertical: 40, alignItems: 'center' }}><ActivityIndicator color={c.brand} /></View>
      )}

      {stage === 'unlinked' && (
        <>
          <Card>
            <Step n={1} text="Tap the button below to get your one-time link code." />
            <Step n={2} text="WhatsApp opens with the code ready — just hit send." />
            <Step n={3} text="You're linked. This screen updates on its own." />
          </Card>
          <View style={{ height: 18 }} />
          <Btn label={busy ? 'Generating…' : 'Generate link code'} variant="primary" onPress={generate} disabled={busy} />
        </>
      )}

      {stage === 'code' && (
        <>
          <Card style={{ alignItems: 'center' }}>
            <Text style={{ fontFamily: font.medium, fontSize: 12, color: c.ink3, textTransform: 'uppercase', letterSpacing: 1 }}>Your link code</Text>
            <Text style={{ fontFamily: font.bold, fontSize: 34, color: c.ink1, letterSpacing: 6, marginTop: 8 }}>{code}</Text>
            <Pressable onPress={copyCode} style={{ marginTop: 10, paddingVertical: 7, paddingHorizontal: 15, borderRadius: 999, backgroundColor: c.surface3 }}>
              <Text style={{ fontFamily: font.semibold, fontSize: 12.5, color: c.brandDeep }}>Copy “LINK {code}”</Text>
            </Pressable>
            <Text style={{ fontFamily: font.regular, fontSize: 12.5, color: c.ink3, textAlign: 'center', marginTop: 14, lineHeight: 19 }}>
              Send <Text style={{ fontFamily: font.semibold, color: c.ink2 }}>LINK {code}</Text> to the Zitch WhatsApp number from this phone. The code expires in 10 minutes.
            </Text>
          </Card>
          <View style={{ height: 16 }} />
          <Btn label="Open WhatsApp" variant="primary" onPress={() => openWa(`LINK ${code}`, waLink)} />
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, height: 34, marginTop: 4 }}>
            {polling && <ActivityIndicator size="small" color={c.ink3} />}
            {polling && <Text style={{ fontFamily: font.regular, fontSize: 12.5, color: c.ink3 }}>Waiting for the code…</Text>}
          </View>
          <Btn label="I've sent it — check now" variant="outline" onPress={() => refreshStatus(false)} />
        </>
      )}

      {stage === 'linked' && (
        <>
          <Card style={{ alignItems: 'center' }}>
            <View style={{ width: 54, height: 54, borderRadius: 27, backgroundColor: 'rgba(37,211,102,.14)', alignItems: 'center', justifyContent: 'center' }}>
              <WhatsAppGlyph size={28} color={WA_GREEN} />
            </View>
            <Text style={{ fontFamily: font.bold, fontSize: 16, color: c.ink1, marginTop: 12 }}>WhatsApp connected</Text>
            {!!masked && <Text style={{ fontFamily: font.regular, fontSize: 13.5, color: c.ink3, marginTop: 4 }}>{masked}</Text>}
            <Text style={{ fontFamily: font.regular, fontSize: 12.5, color: c.ink3, textAlign: 'center', marginTop: 10, lineHeight: 19 }}>
              Message the Zitch number anytime to bank from your chats.
            </Text>
          </Card>
          <View style={{ height: 18 }} />
          <Btn label="Open WhatsApp" variant="primary" onPress={() => openWa()} />
          <View style={{ height: 10 }} />
          <Btn label={busy ? 'Unlinking…' : 'Unlink WhatsApp'} variant="outline" onPress={unlink} disabled={busy} />
        </>
      )}
    </Screen>
  );
};

export default LinkWhatsApp;
