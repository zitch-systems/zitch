import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Linking, Text, View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Btn, Header, PinPad, Screen } from '@/components/design/ui';
import { apiJson } from '@/lib/api';
import { font, useTheme } from '@/lib/theme';
import AuthGuard from '@/components/AuthGuard';
import { usePinScreenProtection } from '@/lib/screenCapture';
import { clearPendingWhatsAppApproval, rememberWhatsAppApproval } from '@/lib/pendingApproval';

/**
 * Deep-link approval for a WhatsApp-armed payment.
 *
 * Opened by `zitch://waapprove?token=…` — the link the chat's confirm message
 * carries (via the https bounce page, since WhatsApp only renders http(s) links
 * as tappable). The flow is preview → authorise → done:
 *
 * 1. The summary shown is fetched from the backend and is the SAME text the
 *    Flow PIN screen displays — what the person approves here is verbatim what
 *    the chat armed, not a re-derivation that could disagree with it.
 * 2. PinPad handles the authorisation exactly as every in-app payment does:
 *    biometric-first when transaction-biometrics is enabled (a successful scan
 *    releases the cached PIN from the OS keychain), keypad fallback otherwise.
 *    The server verifies the PIN either way — "a biometric happened" is never
 *    an argument the backend accepts.
 * 3. AuthGuard means a signed-out or idle-locked device authenticates first,
 *    so a token arriving on someone else's phone dead-ends at sign-in — and
 *    the backend additionally refuses any session that is not the action's
 *    owner, so even a signed-in wrong account gets "expired", nothing more.
 *
 * The WhatsApp thread receives the receipt itself (executors reply to the
 * msisdn), so this screen only needs to close the loop locally.
 */
const WaApprove = () => {
  const { c } = useTheme();
  const params = useLocalSearchParams<{ token?: string }>();
  const token = typeof params.token === 'string' ? params.token : '';

  const [phase, setPhase] = useState<'loading' | 'confirm' | 'busy' | 'done' | 'dead'>('loading');
  const [summary, setSummary] = useState('');
  const [outcome, setOutcome] = useState('');
  const [deadReason, setDeadReason] = useState('');
  // Where the customer came FROM. They approved a chat payment with a fingerprint
  // and should land back in that chat, not stranded on a screen in the app they
  // never meant to open. Blank when the business number isn't configured, and the
  // fallback below just opens WhatsApp.
  const [returnTo, setReturnTo] = useState('');
  const [pinError, setPinError] = useState('');
  usePinScreenProtection(phase === 'confirm' || phase === 'busy');

  /** Send them back to the thread they approved from.
   *
   * `wa.me` opens the Zitch chat itself; `whatsapp://` only opens the app, which
   * is the fallback when the business number isn't configured. Failing to open
   * either is not worth an error — they are already looking at "Payment approved",
   * and the receipt is in the chat whenever they get back to it.
   */
  const backToWhatsApp = async () => {
    try {
      await Linking.openURL(returnTo || 'whatsapp://');
    } catch {
      router.replace('/home');
    }
  };

  useEffect(() => {
    let alive = true;
    (async () => {
      if (!token) {
        setDeadReason('This approval link is incomplete. Open it again from your WhatsApp chat.');
        setPhase('dead');
        return;
      }
      await rememberWhatsAppApproval(token);
      const res = await apiJson('/api/whatsapp/approve/preview/', { token });
      if (!alive) return;
      if (res?.success) {
        setSummary(res.summary || 'Confirm your payment');
        setReturnTo(typeof res.return_to === 'string' ? res.return_to : '');
        setPhase('confirm');
      } else {
        // Expired, already completed, or not this account's action — the
        // backend deliberately words these identically.
        setDeadReason(res?.message || 'This request has expired or was already completed.');
        setPhase('dead');
        clearPendingWhatsAppApproval().catch(() => {});
      }
    })();
    return () => { alive = false; };
  }, [token]);

  const approve = async (pin: string) => {
    setPhase('busy');
    setPinError('');
    const res = await apiJson('/api/whatsapp/approve/execute/', { token, transaction_pin: pin });
    if (res?.success) {
      setOutcome(res.message || 'Done ✅');
      setPhase('done');
      clearPendingWhatsAppApproval().catch(() => {});
      return;
    }
    if (res?.offline) {
      // Delivery unknown. The executors are idempotent (the pending action is
      // the dedupe key), so re-submitting the same approval cannot double-pay —
      // let the user retry rather than guessing at what happened.
      setPinError('Network problem — check your connection and try again.');
      setPhase('confirm');
      return;
    }
    // A wrong PIN keeps the sheet up with the error; anything terminal (lockout,
    // expiry mid-flight) ends the flow with the backend's own words.
    const message = res?.message || 'That didn’t work. Please try again.';
    if (/locked|expired|completed|unavailable/i.test(message)) {
      setDeadReason(message);
      setPhase('dead');
    } else {
      setPinError(message);
      setPhase('confirm');
    }
  };

  return (
    <Screen>
      <Header title="Approve payment" sub="Started in WhatsApp" onBack={() => router.back()} />

      {phase === 'loading' && (
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          <ActivityIndicator size="large" color={c.brand} />
        </View>
      )}

      {(phase === 'confirm' || phase === 'busy') && (
        <View style={{ paddingHorizontal: 22, flex: 1 }}>
          <View style={{ borderRadius: 18, borderWidth: 1.5, borderColor: c.line, backgroundColor: c.surface, padding: 18, marginBottom: 20, flexDirection: 'row', gap: 12, alignItems: 'center' }}>
            <ZIcon name="chat" size={22} color={c.brand} />
            <Text style={{ flex: 1, fontSize: 15, color: c.ink1, fontFamily: font.semibold, lineHeight: 22 }}>{summary}</Text>
          </View>
          <Text style={{ fontSize: 13, color: c.ink3, fontFamily: font.regular, marginBottom: 16 }}>
            Approving here completes the payment you set up in WhatsApp. The receipt will arrive in that chat.
          </Text>
          <PinPad onComplete={(p) => approve(p)} busy={phase === 'busy'} error={pinError} />
        </View>
      )}

      {phase === 'done' && (
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 30 }}>
          <View style={{ width: 84, height: 84, borderRadius: 42, backgroundColor: c.lime, alignItems: 'center', justifyContent: 'center', marginBottom: 20 }}>
            <ZIcon name="check" size={40} color="#fff" stroke={3} />
          </View>
          <Text style={{ fontSize: 19, fontFamily: font.extrabold, color: c.ink1, textAlign: 'center' }}>Payment approved</Text>
          <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular, marginTop: 10, textAlign: 'center', lineHeight: 21 }}>{outcome}</Text>
          <View style={{ marginTop: 26, alignSelf: 'stretch', gap: 10 }}>
            <Btn label="Back to WhatsApp" icon="chat" onPress={backToWhatsApp} />
            <Btn label="Stay in Zitch" variant="outline" onPress={() => router.replace('/home')} />
          </View>
        </View>
      )}

      {phase === 'dead' && (
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 30 }}>
          <ZIcon name="help" size={44} color={c.ink3} />
          <Text style={{ fontSize: 17, fontFamily: font.bold, color: c.ink1, marginTop: 16, textAlign: 'center' }}>Nothing to approve</Text>
          <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular, marginTop: 10, textAlign: 'center', lineHeight: 21 }}>{deadReason}</Text>
          <View style={{ marginTop: 26, alignSelf: 'stretch' }}>
            <Btn label="Back to home" onPress={() => router.replace('/home')} />
          </View>
        </View>
      )}
    </Screen>
  );
};

// Guarded like its (auth) siblings: this screen is reachable by deep link, and
// an idle-locked session keeps its token on the device — without the guard, an
// OS-unlocked phone could open straight onto a live payment approval.
const Guarded = () => (
  <AuthGuard fresh>
    <WaApprove />
  </AuthGuard>
);

export default Guarded;
