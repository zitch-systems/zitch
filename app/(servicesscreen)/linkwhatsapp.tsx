import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, Linking, ActivityIndicator, AppState, Alert } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { Screen, Header, Card, Btn, PinSheet } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { apiJson } from '@/lib/api';
import { useTheme, font } from '@/lib/theme';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { safeWhatsAppUrl } from '@/lib/externalLinks';
import { BANK_WHATSAPP } from '@/components/configFiles/links';

const WA_GREEN = '#25D366';

type Stage = 'loading' | 'error' | 'unlinked' | 'code' | 'linked';

// Open WhatsApp at the Zitch banking number, optionally with prefilled text.
const openWa = (text?: string, link?: string) => {
  const url = safeWhatsAppUrl(link) || `https://wa.me/${BANK_WHATSAPP}${text ? `?text=${encodeURIComponent(text)}` : ''}`;
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
  const [pinOpen, setPinOpen] = useState(false);
  const [expiresAt, setExpiresAt] = useState(0);
  const [expired, setExpired] = useState(false);
  const [error, setError] = useState('');
  const [focused, setFocused] = useState(false);
  const [checking, setChecking] = useState(false);
  const focusedRef = useRef(false);
  const statusBusy = useRef(false);
  const actionBusy = useRef(false);

  const refreshStatus = useCallback(async (manual = false): Promise<boolean> => {
    if (statusBusy.current || !focusedRef.current) return false;
    statusBusy.current = true;
    setChecking(true);
    try {
      const res = await apiJson<{ success?: boolean; linked?: boolean; masked_number?: string; message?: string }>(
        '/api/whatsapp/link/status/', {}, 10000,
      );
      if (!focusedRef.current) return false;
      if (!res?.success) {
        setError(res?.message || 'Could not check your WhatsApp connection.');
        setStage((s) => s === 'loading' ? 'error' : s);
        return false;
      }
      setError('');
      if (res.linked) {
        setMasked(res.masked_number || '');
        setCode('');
        setWaLink('');
        setStage('linked');
        return true;
      }
      setStage((s) => s === 'code' ? s : 'unlinked');
      if (manual) notify('Not linked yet', 'Send the link code in your Zitch WhatsApp chat, then check again.');
      return false;
    } catch {
      if (focusedRef.current) {
        setError('Could not check your WhatsApp connection. Please try again.');
        setStage((s) => s === 'loading' ? 'error' : s);
      }
      return false;
    } finally {
      statusBusy.current = false;
      if (focusedRef.current) setChecking(false);
    }
  }, []);

  useFocusEffect(useCallback(() => {
    focusedRef.current = true;
    setFocused(true);
    void refreshStatus();
    return () => { focusedRef.current = false; setFocused(false); };
  }, [refreshStatus]));

  useEffect(() => {
    if (!focused || stage !== 'code' || !expiresAt || expired) return;
    const checkExpiry = () => {
      if (Date.now() >= expiresAt) setExpired(true);
    };
    checkExpiry();
    // The status endpoint allows 30 checks per five minutes. Leave headroom
    // for foreground/manual checks and never overlap slow network requests.
    const poll = setInterval(() => {
      checkExpiry();
      if (Date.now() < expiresAt && AppState.currentState === 'active') void refreshStatus();
    }, 15000);
    const expiry = setTimeout(checkExpiry, Math.max(0, expiresAt - Date.now()));
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') { checkExpiry(); void refreshStatus(); }
    });
    return () => { clearInterval(poll); clearTimeout(expiry); subscription.remove(); };
  }, [focused, stage, expiresAt, expired, refreshStatus]);

  const generate = async (pin: string) => {
    if (actionBusy.current) return;
    actionBusy.current = true;
    setBusy(true);
    try {
      const res = await apiJson<{ success?: boolean; code?: string; wa_link?: string; expires_in?: number; message?: string }>(
        '/api/whatsapp/link/start/', { transaction_pin: pin }, 15000,
      );
      if (!focusedRef.current) return;
      setPinOpen(false);
      if (res?.success && res.code) {
        setCode(res.code);
        setWaLink(res.wa_link || '');
        const seconds = Number(res.expires_in);
        setExpiresAt(Date.now() + (Number.isFinite(seconds) && seconds > 0 ? seconds : 600) * 1000);
        setExpired(false);
        setError('');
        setStage('code');
      } else notify('Could not link', res?.message || 'Could not generate a code. Please try again.');
    } catch {
      if (focusedRef.current) notify('Could not link', 'Check your connection and try again.');
    } finally {
      actionBusy.current = false;
      setBusy(false);
    }
  };

  const copyCode = async () => {
    if (Date.now() >= expiresAt) { setExpired(true); return; }
    try {
      await Clipboard.setStringAsync(`LINK ${code}`);
      notify('Copied', 'Paste it into your WhatsApp chat with Zitch.');
    } catch { notify('Could not copy', 'Open WhatsApp to use the prefilled link code.'); }
  };

  // Unlinking also cancels any payment started on that number but not yet
  // confirmed, so it is not a one-tap action.
  const confirmUnlink = () => {
    if (actionBusy.current) return;
    Alert.alert(
      'Unlink WhatsApp?',
      'You will no longer be able to bank from that WhatsApp number. Any payment started there that you have not confirmed will be cancelled.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Unlink', style: 'destructive', onPress: () => { void unlink(); } },
      ],
    );
  };

  const unlink = async () => {
    if (actionBusy.current) return;
    actionBusy.current = true;
    setBusy(true);
    try {
      const res = await apiJson<{ success?: boolean; message?: string }>('/api/whatsapp/link/unlink/', {}, 15000);
      if (!focusedRef.current) return;
      if (res?.success) {
        setCode(''); setWaLink(''); setMasked(''); setStage('unlinked');
        notify('Unlinked', 'Your WhatsApp has been disconnected.');
      } else notify('Could not unlink', res?.message || 'Please try again.');
    } catch {
      if (focusedRef.current) notify('Could not unlink', 'Check your connection and try again.');
    } finally {
      actionBusy.current = false;
      setBusy(false);
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

      {error ? <Text accessibilityRole="alert" style={{ color: c.red, marginBottom: 14 }}>{error}</Text> : null}
      {stage === 'error' && <Btn label="Try again" disabled={checking} onPress={() => void refreshStatus()} />}

      {stage === 'unlinked' && (
        <>
          <Card>
            <Step n={1} text="Tap the button below to confirm your PIN and get a one-time link code." />
            <Step n={2} text="Tap Open WhatsApp, then send the prefilled code." />
            <Step n={3} text="You're linked. This screen updates on its own." />
          </Card>
          <View style={{ height: 18 }} />
          <Btn label={busy ? 'Generating…' : 'Generate link code'} variant="primary" onPress={() => setPinOpen(true)} disabled={busy} />
        </>
      )}

      {stage === 'code' && (
        <>
          <Card style={{ alignItems: 'center' }}>
            <Text style={{ fontFamily: font.medium, fontSize: 12, color: c.ink3, textTransform: 'uppercase', letterSpacing: 1 }}>Your link code</Text>
            <Text style={{ fontFamily: font.bold, fontSize: 20, color: c.ink1, letterSpacing: 1, marginTop: 8, textAlign: 'center' }}>{code}</Text>
            <Pressable disabled={expired} accessibilityRole="button" accessibilityLabel="Copy link code" onPress={copyCode} style={{ marginTop: 10, paddingVertical: 7, paddingHorizontal: 15, borderRadius: 999, backgroundColor: c.surface3 }}>
              <Text style={{ fontFamily: font.semibold, fontSize: 12.5, color: c.brandDeep }}>Copy “LINK {code}”</Text>
            </Pressable>
            <Text style={{ fontFamily: font.regular, fontSize: 12.5, color: c.ink3, textAlign: 'center', marginTop: 14, lineHeight: 19 }}>
              Send <Text style={{ fontFamily: font.semibold, color: c.ink2 }}>LINK {code}</Text> to the Zitch WhatsApp number from the WhatsApp account you want to connect. Keep this code private.
            </Text>
          </Card>
          <View style={{ height: 16 }} />
          <Btn label="Open WhatsApp" variant="primary" disabled={expired || busy} onPress={() => {
            if (Date.now() >= expiresAt) { setExpired(true); return; }
            openWa(`LINK ${code}`, waLink);
          }} />
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, height: 34, marginTop: 4 }}>
            {!expired && <ActivityIndicator size="small" color={c.ink3} />}
            {!expired && <Text style={{ fontFamily: font.regular, fontSize: 12.5, color: c.ink3 }}>Waiting for the code…</Text>}
          </View>
          {expired ? <Text style={{ color: c.red, textAlign: 'center', marginBottom: 12 }}>This code has expired. Generate a new one to continue.</Text> : null}
          <Btn label={checking ? 'Checking…' : "I've sent it — check now"} variant="outline" disabled={checking || busy} onPress={() => void refreshStatus(true)} />
          <View style={{ height: 10 }} />
          <Btn label="Generate a new code" variant="outline" disabled={busy} onPress={() => setPinOpen(true)} />
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
          <Btn label={busy ? 'Unlinking…' : 'Unlink WhatsApp'} variant="outline" onPress={confirmUnlink} disabled={busy} />
        </>
      )}
      <PinSheet open={pinOpen} onClose={() => { if (!busy) setPinOpen(false); }} onComplete={generate}
        busy={busy} title="Connect WhatsApp" subtitle="Enter your 6-digit transaction PIN to create a private link code." />
    </Screen>
  );
};

export default LinkWhatsApp;
