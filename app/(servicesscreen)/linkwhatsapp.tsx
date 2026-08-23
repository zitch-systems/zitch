import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, Linking, ActivityIndicator, AppState } from 'react-native';
import { router } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { Screen, Header, Card, Btn, PinSheet } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { apiJson } from '@/lib/api';
import { useTheme, font } from '@/lib/theme';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { BANK_WHATSAPP } from '@/components/configFiles/links';
import { safeWhatsAppUrl } from '@/lib/externalLinks';

const WA_GREEN = '#25D366';

type Stage = 'loading' | 'unlinked' | 'code' | 'linked';

// Open WhatsApp at the Zitch banking number, optionally with prefilled text.
const openWa = (text?: string, link?: string) => {
  const fallback = `https://wa.me/${BANK_WHATSAPP}${text ? `?text=${encodeURIComponent(text)}` : ''}`;
  const url = safeWhatsAppUrl(link) || fallback;
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

//: Auto-detect cadence. /api/whatsapp/link/status/ allows 30 requests per 300s;
//: fast-then-slow keeps the worst 5-minute window at 10 + 12 = 22.
const POLL_FAST_MS = 6000;
const POLL_SLOW_MS = 20000;
const POLL_FAST_FOR_MS = 60000;

const LinkWhatsApp = () => {
  const { c } = useTheme();
  const [stage, setStage] = useState<Stage>('loading');
  const [masked, setMasked] = useState('');
  const [code, setCode] = useState('');
  const [waLink, setWaLink] = useState('');
  const [busy, setBusy] = useState(false);
  const [pinOpen, setPinOpen] = useState(false);
  const [polling, setPolling] = useState(false);
  // A self-rescheduling timeout, not setInterval: the cadence changes as the wait
  // goes on (see POLL_FAST_MS / POLL_SLOW_MS) and a fixed interval cannot do that.
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollDeadlineRef = useRef(0);
  const pollStartedRef = useRef(0);

  const stopPoll = useCallback(() => {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    setPolling(false);
  }, []);

  // Check whether this account already has an active WhatsApp link.
  const refreshStatus = useCallback(async (silent = false): Promise<boolean> => {
    try {
      const res = await apiJson<{ linked?: boolean; masked_number?: string }>('/api/whatsapp/link/status/');
      if (res?.linked) {
        setMasked(res.masked_number || '');
        setStage('linked');
        stopPoll();
        return true;
      }
      if (!silent) setStage((s) => (s === 'loading' ? 'unlinked' : s));
      return false;
    } catch {
      if (!silent) {
        setStage((s) => (s === 'loading' ? 'unlinked' : s));
        notify('Connection error', 'Could not check your WhatsApp link. Please try again.');
      }
      return false;
    }
  }, [stopPoll]);

  /* Auto-detect cadence, sized to the SERVER'S budget.
   *
   * /api/whatsapp/link/status/ is rate-limited to 30 requests per 300s. The old
   * loop polled every 4 seconds — 75 requests per 5 minutes, two and a half times
   * over — so after roughly two minutes every poll came back 429. refreshStatus
   * swallows errors when silent, so nothing surfaced: auto-detect simply stopped
   * working, and a customer who took longer than two minutes to send the code sat
   * there until the 30-minute deadline told them it had expired, even when the
   * link had actually succeeded.
   *
   * Fast for the first minute (the window where someone is actually switching to
   * WhatsApp and sending), then slow. Worst case in any 5-minute window is
   * 10 + 12 = 22 requests, comfortably inside the budget with room for the
   * mount-time check and a manual refresh.
   */
  // Self-reference for the recursive scheduler: a useCallback cannot call itself
  // (it is not in scope inside its own initialiser), and a ref keeps the loop
  // pointing at the CURRENT closure rather than the one captured on first render.
  const scheduleRef = useRef<() => void>(() => {});

  const schedulePoll = useCallback(() => {
    if (pollRef.current) clearTimeout(pollRef.current);
    const elapsed = Date.now() - pollStartedRef.current;
    const delay = elapsed < POLL_FAST_FOR_MS ? POLL_FAST_MS : POLL_SLOW_MS;
    pollRef.current = setTimeout(async () => {
      if (Date.now() >= pollDeadlineRef.current) {
        stopPoll();
        notify('Code expired', 'Generate a new WhatsApp link code to continue.');
        setStage('unlinked');
        setCode('');
        setWaLink('');
        return;
      }
      // Nothing to detect while the app is in the background — the customer is
      // in WhatsApp. Polling on anyway spent battery and the request budget on
      // exactly the minutes we cannot use them.
      if (AppState.currentState === 'active') {
        const linked = await refreshStatus(true);
        if (linked) return;            // refreshStatus already stopped the poll
      }
      if (pollRef.current) scheduleRef.current();
    }, delay);
  }, [refreshStatus, stopPoll]);

  useEffect(() => { scheduleRef.current = schedulePoll; }, [schedulePoll]);

  // Coming back from WhatsApp is the single most likely moment for the link to
  // have completed, so check immediately on foreground rather than waiting out
  // the next tick.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      if (next === 'active' && pollRef.current) void refreshStatus(true);
    });
    return () => sub.remove();
  }, [refreshStatus]);

  useEffect(() => {
    const timer = setTimeout(() => void refreshStatus(), 0);
    return () => { clearTimeout(timer); stopPoll(); };
  }, [refreshStatus, stopPoll]);

  // Linking grants a channel that can move money, so the PIN is required before
  // a code is issued — an unlocked phone must not be enough to bind a stranger's
  // WhatsApp to this account.
  const generate = async (transaction_pin: string) => {
    setPinOpen(false);
    setBusy(true);
    const res = await apiJson<{ success?: boolean; code?: string; wa_link?: string; message?: string }>('/api/whatsapp/link/start/', { transaction_pin });
    setBusy(false);
    if (res?.success && res.code) {
      setCode(res.code);
      setWaLink(res.wa_link || '');
      setStage('code');
      // Auto-detect the moment the user sends the code from WhatsApp.
      stopPoll();
      setPolling(true);
      pollStartedRef.current = Date.now();
      pollDeadlineRef.current = pollStartedRef.current + (30 * 60 * 1000);  // matches LINK_CODE_TTL
      schedulePoll();
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
      <Header title="Link WhatsApp" onBack={() => router.back()} />

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
          <Btn label={busy ? 'Generating…' : 'Generate link code'} variant="primary" onPress={() => setPinOpen(true)} disabled={busy} />
        </>
      )}

      {stage === 'code' && (
        <>
          <Card style={{ alignItems: 'center' }}>
            <Text style={{ fontFamily: font.medium, fontSize: 12, color: c.ink3, textTransform: 'uppercase', letterSpacing: 1 }}>Your link code</Text>
            <Text selectable numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.55} style={{ fontFamily: font.bold, fontSize: 22, color: c.ink1, letterSpacing: 2, marginTop: 8, width: '100%', textAlign: 'center' }}>{code}</Text>
            <Pressable onPress={copyCode} style={{ marginTop: 10, paddingVertical: 7, paddingHorizontal: 15, borderRadius: 999, backgroundColor: c.surface3 }}>
              <Text style={{ fontFamily: font.semibold, fontSize: 12.5, color: c.brandDeep }}>Copy “LINK {code}”</Text>
            </Pressable>
            <Text style={{ fontFamily: font.regular, fontSize: 12.5, color: c.ink3, textAlign: 'center', marginTop: 14, lineHeight: 19 }}>
              Send <Text style={{ fontFamily: font.semibold, color: c.ink2 }}>LINK {code}</Text> to the Zitch WhatsApp number from this phone. The code expires in 30 minutes and can only be used once.
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
      <PinSheet
        open={pinOpen}
        onClose={() => setPinOpen(false)}
        onComplete={generate}
        title="Confirm it's you"
        subtitle="Enter your 6-digit PIN to generate a WhatsApp link code."
      />
    </Screen>
  );
};

export default LinkWhatsApp;
