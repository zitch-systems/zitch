import React, { useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { apiPost } from '@/lib/api';
import { saveTransactionPin } from '@/lib/secureStore';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import { isTrivialPin } from '@/lib/format';

// Change the transaction PIN for a signed-in user. The backend requires the
// account password to change an existing PIN, so a stolen session token alone
// can't overwrite it — that's also the recovery path for a forgotten PIN.
const ResetPin = () => {
  const { c } = useTheme();
  const [password, setPassword] = useState('');
  const [pin, setPin] = useState('');
  const [pin2, setPin2] = useState('');
  const [busy, setBusy] = useState(false);

  const canSubmit = password.length >= 8 && pin.length >= 4 && pin === pin2;

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
      const response = await apiPost('/api/set-transaction-pin/', { pin, password });
      const result = await response.json();
      if (response.ok) {
        await saveTransactionPin(pin); // keep the keychain copy (biometric pay) in sync
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
        For your security, confirm your account password to set a new 4-digit transaction PIN.
      </Text>

      <View style={{ gap: 16 }}>
        <Field
          label="Account password"
          value={password}
          onChangeText={setPassword}
          secureTextEntry
          placeholder="Enter your password"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
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

      <View style={{ marginTop: 26 }}>
        <Btn label={busy ? 'Saving…' : 'Change PIN'} onPress={submit} disabled={!canSubmit || busy} />
      </View>
    </Screen>
  );
};

export default ResetPin;
