import React, { useCallback, useRef, useState } from 'react';
import { Text, View } from 'react-native';
import { useFocusEffect } from 'expo-router';
import { Sheet, Field, Btn } from '@/components/design/ui';
import { kycService } from '@/lib/services/kyc';
import { getSessionGeneration } from '@/lib/secureStore';
import { useTheme, font } from '@/lib/theme';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Asks a signed-in customer to verify their email, once per visit to the screen, until they do. */
export default function EmailVerifyPrompt() {
  const { c } = useTheme();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const dismissed = useRef(false);
  const generation = useRef(getSessionGeneration());
  const current = () => generation.current === getSessionGeneration();

  useFocusEffect(useCallback(() => {
    generation.current = getSessionGeneration();
    let active = true;
    kycService.getStatus().then((status) => {
      if (!active || !current() || dismissed.current) return;
      if (status.success && status.email_verified === false) {
        setEmail(status.email || '');
        setOpen(true);
      }
    }).catch(() => {});
    return () => { active = false; };
  }, []));

  const send = async () => {
    if (busy || !EMAIL.test(email.trim())) return;
    setBusy(true);
    try {
      const result = await kycService.startEmail(email.trim());
      if (!current()) return;
      setMessage(result.success ? '' : result.message || 'We could not send the code. Please try again.');
      if (result.success) { setSent(true); setCode(''); }
    } catch { if (current()) setMessage('Could not reach Zitch. Please try again.'); }
    finally { if (current()) setBusy(false); }
  };
  const confirm = async () => {
    if (busy || !/^[0-9]{6}$/.test(code)) return;
    setBusy(true);
    try {
      const result = await kycService.confirmEmail(code);
      if (!current()) return;
      if (result.success) { setOpen(false); setMessage(''); }
      else { setMessage(result.message || 'That code is not right. Please try again.'); setCode(''); }
    } catch { if (current()) setMessage('Could not reach Zitch. Please try again.'); }
    finally { if (current()) setBusy(false); }
  };
  const later = () => { dismissed.current = true; setOpen(false); };

  return <Sheet open={open} onClose={later} title="Verify your email">
    <View style={{ gap: 14, paddingBottom: 8 }}>
      {!!message && <Text accessibilityLiveRegion="polite" style={{ color: c.brand, fontFamily: font.regular }}>{message}</Text>}
      <Field label="Email address" value={email} keyboardType="email-address" autoCapitalize="none" autoCorrect={false}
        editable={!busy && !sent} onChangeText={setEmail} />
      {sent ? <>
        <Field label="Six-digit code" value={code} maxLength={6} keyboardType="number-pad" editable={!busy}
          onChangeText={(v) => setCode(v.replace(/\D/g, '').slice(0, 6))} />
        <Btn label={busy ? 'Please wait…' : 'Verify email'} disabled={busy || code.length !== 6} onPress={() => void confirm()} />
        <Btn label="Resend code" variant="ghost" disabled={busy} onPress={() => void send()} />
      </> : <Btn label={busy ? 'Please wait…' : 'Send code'} disabled={busy || !EMAIL.test(email.trim())} onPress={() => void send()} />}
      <Btn label="Later" variant="ghost" disabled={busy} onPress={later} />
    </View>
  </Sheet>;
}
