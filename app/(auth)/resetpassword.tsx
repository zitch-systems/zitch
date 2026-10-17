import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { publicPost } from '@/lib/api';
import { notify } from '@/components/design/Notify';
import { unlockSession } from '@/lib/session';
import { usePinScreenProtection } from '@/lib/screenCapture';
import { getSessionGeneration, storeSession } from '@/lib/secureStore';
import { registerForPushNotifications } from '@/lib/notifications';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

// Step 2 of password recovery: enter the reset code + a new password. On success
// the backend revokes every old session and returns a fresh token, so we save it
// and drop the user straight into the app.
const ResetPassword = ({ ident }: { ident: string }) => {
  const { c } = useTheme();
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  usePinScreenProtection();
  const [otp, setOtp] = useState('');
  const [p1, setP1] = useState('');
  const [p2, setP2] = useState('');
  const [busy, setBusy] = useState(false);

  const strong = p1.length >= 8 && /[A-Za-z]/.test(p1) && /[0-9]/.test(p1) && /[^A-Za-z0-9]/.test(p1);
  const match = p1 !== '' && p1 === p2;
  const canSubmit = !!ident && otp.length === 6 && strong && match;

  const reset = async () => {
    if (!canSubmit || inFlight.current) return;
    if (!match) {
      notify('Error', 'Passwords do not match');
      return;
    }
    inFlight.current = true;
    setBusy(true);
    const generation = getSessionGeneration();
    try {
      const response = await publicPost('/api/password/reset/', { email_or_phone: ident, otp, password: p1 });
      const result = await response.json();
      if (!mounted.current || generation !== getSessionGeneration()) return;
      if (response.ok && result.access_token) {
        await storeSession(result, generation);
        const authenticatedGeneration = getSessionGeneration();
        if (!mounted.current) return;
        await unlockSession();
        if (!mounted.current || authenticatedGeneration !== getSessionGeneration()) return;
        // Recovery also creates a new server session. Rebind already-authorized
        // alerts just as password sign-in does; otherwise this phone can remain
        // unsubscribed after the reset revokes its previous session.
        registerForPushNotifications(false).catch(() => {});
        router.replace('/home');
      } else {
        notify('Error', result.message || 'Could not reset your password');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  const masked = !ident
    ? 'your phone'
    : ident.includes('@')
      ? ident
      : `${ident.slice(0, 4)} *** ${ident.slice(-4)}`;

  if (!ident) return (
    <Screen>
      <Header onBack={() => router.replace('/signin')} />
      <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1 }}>Request a reset code</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 8, marginBottom: 24, fontFamily: font.regular }}>
        Start with the email or phone number on your account so we can match your reset code.
      </Text>
      <Btn label="Request reset code" onPress={() => router.replace('/forgotpassword')} />
    </Screen>
  );

  return (
    <Screen>
      <Header onBack={() => router.back()} />
      <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, marginTop: 6 }}>Enter reset code</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, marginBottom: 22, fontFamily: font.regular }}>
        Reset requested for <Text style={{ fontFamily: font.bold, color: c.ink1 }}>{masked}</Text>. If that account exists, use the 6-digit code sent to its registered phone or verified email.
      </Text>

      <View style={{ gap: 16 }}>
        <Field
          label="Reset code"
          value={otp}
          onChangeText={(v) => setOtp(v.replace(/\D/g, '').slice(0, 6))}
          keyboardType="number-pad"
          placeholder="6-digit code"
          maxLength={6}
          editable={!busy}
          autoComplete="sms-otp"
          textContentType="oneTimeCode"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
        <Field
          label="New password"
          value={p1}
          onChangeText={setP1}
          secureTextEntry
          editable={!busy}
          placeholder="Enter new password"
          autoComplete="new-password"
          textContentType="newPassword"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
        <Field
          label="Confirm password"
          value={p2}
          onChangeText={setP2}
          secureTextEntry
          editable={!busy}
          returnKeyType="done"
          onSubmitEditing={reset}
          placeholder="Re-enter new password"
          autoComplete="new-password"
          textContentType="newPassword"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
      </View>

      {p2.length > 0 && !match && <Text accessibilityLiveRegion="polite" style={{ fontSize: 12.5, color: c.red, marginTop: 8, fontFamily: font.regular }}>Passwords do not match.</Text>}

      <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 12, fontFamily: font.regular }}>
        At least 8 characters, with a letter, a number and a special character.
      </Text>

      <View style={{ marginTop: 22 }}>
        <Btn label={busy ? 'Resetting…' : 'Reset password'} onPress={reset} disabled={!canSubmit || busy} />
      </View>
    </Screen>
  );
};

export default function ResetPasswordRoute() {
  const params = useLocalSearchParams<{ ident?: string; phone?: string }>();
  const rawIdent = params.ident ?? params.phone;
  const ident = typeof rawIdent === 'string' && rawIdent.length <= 254 ? rawIdent.trim() : '';
  // A new recovery target must start a new form; a delayed response for the
  // previous target cannot sign it in over the current recovery attempt.
  return <ResetPassword key={ident} ident={ident} />;
}
