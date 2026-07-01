import React, { useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router } from 'expo-router';
import { apiPost } from '@/lib/api';
import { saveTransactionPin, hasTransactionPin } from '@/lib/secureStore';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import { isTrivialPin } from '@/lib/format';

// Change the transaction PIN for a signed-in user. Changing an existing PIN
// requires the CURRENT PIN by default (so a stolen session token alone can't
// overwrite it); if the user has forgotten it, they can switch to confirming
// with their account password as the recovery path.
const ResetPin = () => {
  const { c } = useTheme();
  const [useOldPin, setUseOldPin] = useState(true);   // false => verify with password (forgot-PIN)
  const [oldPin, setOldPin] = useState('');
  const [password, setPassword] = useState('');
  const [pin, setPin] = useState('');
  const [pin2, setPin2] = useState('');
  const [busy, setBusy] = useState(false);

  const authOk = useOldPin ? oldPin.length >= 4 : password.length >= 8;
  const canSubmit = authOk && pin.length >= 4 && pin === pin2;

  const submit = async () => {
    if (pin !== pin2) {
      notify('Error', 'PINs do not match');
      return;
    }
    if (isTrivialPin(pin)) {
      notify('Error', 'Choose a less guessable PIN (avoid 0000, 1234, repeated or sequential digits).');
      return;
    }
    setBusy(true);
    try {
      const body = useOldPin ? { pin, old_pin: oldPin } : { pin, password };
      const response = await apiPost('/api/set-transaction-pin/', body);
      const result = await response.json();
      if (response.ok) {
        // Only refresh the cached keychain copy if the user already uses biometric
        // pay; otherwise changing the PIN must NOT start caching the spending secret.
        if (await hasTransactionPin()) await saveTransactionPin(pin);
        notify('Done', 'Your transaction PIN has been changed.');
        router.back();
      } else {
        notify('Error', result.message || 'Could not change your PIN');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <Header title="Change transaction PIN" onBack={() => router.back()} />
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 2, marginBottom: 22, fontFamily: font.regular }}>
        {useOldPin
          ? 'Enter your current 4-digit PIN, then choose a new one.'
          : 'Confirm your account password, then choose a new 4-digit PIN.'}
      </Text>

      <View style={{ gap: 16 }}>
        {useOldPin ? (
          <Field
            label="Current PIN"
            value={oldPin}
            onChangeText={(v) => setOldPin(v.replace(/\D/g, '').slice(0, 4))}
            secureTextEntry
            keyboardType="number-pad"
            maxLength={4}
            placeholder="Enter your current PIN"
            prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
          />
        ) : (
          <Field
            label="Account password"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            placeholder="Enter your password"
            prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
          />
        )}
        <Field
          label="New PIN"
          value={pin}
          onChangeText={(v) => setPin(v.replace(/\D/g, '').slice(0, 4))}
          secureTextEntry
          keyboardType="number-pad"
          maxLength={4}
          placeholder="4-digit PIN"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
        <Field
          label="Confirm new PIN"
          value={pin2}
          onChangeText={(v) => setPin2(v.replace(/\D/g, '').slice(0, 4))}
          secureTextEntry
          keyboardType="number-pad"
          maxLength={4}
          placeholder="Re-enter PIN"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
      </View>

      <Pressable onPress={() => setUseOldPin((v) => !v)} style={{ marginTop: 16, alignSelf: 'flex-start' }}>
        <Text style={{ fontSize: 13, color: c.brand, fontFamily: font.semibold }}>
          {useOldPin ? 'Forgot your current PIN? Use your password' : 'Use your current PIN instead'}
        </Text>
      </Pressable>

      <View style={{ marginTop: 24 }}>
        <Btn label={busy ? 'Saving…' : 'Change PIN'} onPress={submit} disabled={!canSubmit || busy} />
      </View>
    </Screen>
  );
};

export default ResetPin;
