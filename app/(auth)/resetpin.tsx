import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { apiPost } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import { clearTransactionPin, getSessionGeneration, saveTransactionPin } from '@/lib/secureStore';
import { isBiometricTxnEnabled } from '@/lib/biometrics';
import AuthGuard from '@/components/AuthGuard';
import { usePinScreenProtection } from '@/lib/screenCapture';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import { TRANSACTION_PIN_LENGTH } from '@/lib/transactionPin';

// Change the transaction PIN for a signed-in user. The backend requires the
// account password to change an existing PIN, so a stolen session token alone
// can't overwrite it — that's also the recovery path for a forgotten PIN.
const ResetPin = () => {
  const { c } = useTheme();
  const [password, setPassword] = useState('');
  const [pin, setPin] = useState('');
  const [pin2, setPin2] = useState('');
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const screenGeneration = useRef(getSessionGeneration()).current;
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  usePinScreenProtection();

  const canSubmit = password.length > 0
    && pin.length === TRANSACTION_PIN_LENGTH
    && pin === pin2;

  const submit = async () => {
    if (!canSubmit || inFlight.current) return;
    if (pin !== pin2) {
      notify('Error', 'PINs do not match');
      return;
    }
    inFlight.current = true;
    setBusy(true);
    const generation = screenGeneration;
    const current = () => mounted.current && generation === getSessionGeneration();
    try {
      if (!current()) return;
      const response = await apiPost(EP.auth.setTransactionPin, { pin, password });
      const result = await response.json();
      if (!current()) return;
      if (response.ok) {
        // Remove the old PIN even if the customer cancels the OS prompt for the
        // replacement. A successful server change must not leave a stale secret.
        const biometricPay = await isBiometricTxnEnabled();
        if (!current()) return;
        await clearTransactionPin();
        if (!current()) return;
        if (biometricPay) {
          try { await saveTransactionPin(pin); }
          catch { if (current()) notify('Biometric payments paused', 'Your PIN changed. Enable biometric payments again from Security when you are ready.'); }
        }
        if (!current()) return;
        notify('Done', 'Your transaction PIN has been changed.');
        router.back();
      } else {
        notify('Error', result.message || 'Could not change your PIN');
      }
    } catch {
      if (current()) notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <Screen>
      <Header title="Change transaction PIN" onBack={() => router.back()} />
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 2, marginBottom: 22, fontFamily: font.regular }}>
        For your security, confirm your account password to set a new {TRANSACTION_PIN_LENGTH}-digit transaction PIN.
      </Text>

      <View style={{ gap: 16 }}>
        <Field
          label="Account password"
          value={password}
          onChangeText={setPassword}
          secureTextEntry
          editable={!busy}
          autoComplete="current-password"
          textContentType="password"
          placeholder="Enter your password"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
        <Field
          label="New PIN"
          value={pin}
          onChangeText={(v) => setPin(v.replace(/\D/g, '').slice(0, TRANSACTION_PIN_LENGTH))}
          secureTextEntry
          editable={!busy}
          keyboardType="number-pad"
          maxLength={TRANSACTION_PIN_LENGTH}
          placeholder={`${TRANSACTION_PIN_LENGTH}-digit PIN`}
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
        <Field
          label="Confirm new PIN"
          value={pin2}
          onChangeText={(v) => setPin2(v.replace(/\D/g, '').slice(0, TRANSACTION_PIN_LENGTH))}
          secureTextEntry
          editable={!busy}
          returnKeyType="done"
          onSubmitEditing={submit}
          keyboardType="number-pad"
          maxLength={TRANSACTION_PIN_LENGTH}
          placeholder="Re-enter PIN"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
      </View>
      {pin2.length > 0 && pin !== pin2 && <Text accessibilityLiveRegion="polite" style={{ fontSize: 12.5, color: c.red, marginTop: 8, fontFamily: font.regular }}>PINs do not match.</Text>}

      <View style={{ marginTop: 26 }}>
        <Btn label={busy ? 'Saving…' : 'Change PIN'} onPress={submit} disabled={!canSubmit || busy} />
      </View>
    </Screen>
  );
};

export default function GuardedResetPin() { return <AuthGuard fresh><ResetPin /></AuthGuard>; }
