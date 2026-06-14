import React, { useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { ZMark } from '@/components/design/Brand';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

// Step 1 of password recovery: ask for the account phone and request a reset
// code. The backend always replies 200 with a generic message (it never reveals
// whether the number is registered), so we advance to the code step regardless.
const ForgotPassword = () => {
  const { c } = useTheme();
  const [phone, setPhone] = useState('');
  const [busy, setBusy] = useState(false);

  const valid = phone.trim().length >= 10;

  const requestCode = async () => {
    if (!valid) {
      notify('Error', 'Enter the phone number on your account');
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${baseUrl}/api/password/forgot/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phone.trim() }),
      });
      if (response.ok) {
        router.push({ pathname: '/resetpassword', params: { phone: phone.trim() } });
      } else {
        const result = await response.json().catch(() => ({} as any));
        notify('Error', result.message || 'Could not send a reset code. Please try again.');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <Header onBack={() => router.back()} />
      <View style={{ marginTop: 6, marginBottom: 8 }}>
        <ZMark size={44} />
      </View>
      <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1 }}>Reset password</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, marginBottom: 24, fontFamily: font.regular }}>
        Enter your phone number and we'll send a code to reset your password.
      </Text>

      <Field
        label="Phone number"
        value={phone}
        onChangeText={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))}
        keyboardType="number-pad"
        placeholder="0801 234 5678"
        prefix={<ZIcon name="user" size={18} color={c.ink3} />}
      />

      <View style={{ marginTop: 26 }}>
        <Btn label={busy ? 'Sending…' : 'Send reset code'} onPress={requestCode} disabled={busy || !valid} />
      </View>
    </Screen>
  );
};

export default ForgotPassword;
