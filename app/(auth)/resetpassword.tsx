import React, { useState } from 'react';
import { View, Text, Alert } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { saveToken } from '@/lib/secureStore';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

// Step 2 of password recovery: enter the reset code + a new password. On success
// the backend revokes every old session and returns a fresh token, so we save it
// and drop the user straight into the app.
const ResetPassword = () => {
  const { c } = useTheme();
  const params = useLocalSearchParams<{ phone?: string }>();
  const phone = params.phone ?? '';
  const [otp, setOtp] = useState('');
  const [p1, setP1] = useState('');
  const [p2, setP2] = useState('');
  const [busy, setBusy] = useState(false);

  const strong = p1.length >= 8 && /[A-Za-z]/.test(p1) && /[0-9]/.test(p1);
  const match = p1 !== '' && p1 === p2;
  const canSubmit = otp.length === 6 && strong && match;

  const reset = async () => {
    if (!match) {
      Alert.alert('Error', 'Passwords do not match');
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${baseUrl}/api/password/reset/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, otp, password: p1 }),
      });
      const result = await response.json();
      if (response.ok && result.access_token) {
        await saveToken(result.access_token);
        router.replace('/home');
      } else {
        Alert.alert('Error', result.message || 'Could not reset your password');
      }
    } catch {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setBusy(false);
    }
  };

  const masked = phone ? phone.replace(/(\d{4})(\d{3})(\d{0,4})/, '$1 $2 $3') : 'your phone';

  return (
    <Screen>
      <Header onBack={() => router.back()} />
      <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, marginTop: 6 }}>Enter reset code</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, marginBottom: 22, fontFamily: font.regular }}>
        Enter the 6-digit code sent to <Text style={{ fontFamily: font.bold, color: c.ink1 }}>{masked}</Text> and choose a new password.
      </Text>

      <View style={{ gap: 16 }}>
        <Field
          label="Reset code"
          value={otp}
          onChangeText={(v) => setOtp(v.replace(/\D/g, '').slice(0, 6))}
          keyboardType="number-pad"
          placeholder="6-digit code"
          maxLength={6}
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
        <Field
          label="New password"
          value={p1}
          onChangeText={setP1}
          secureTextEntry
          placeholder="Enter new password"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
        <Field
          label="Confirm password"
          value={p2}
          onChangeText={setP2}
          secureTextEntry
          placeholder="Re-enter new password"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
      </View>

      <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 12, fontFamily: font.regular }}>
        At least 8 characters, with a letter and a number.
      </Text>

      <View style={{ marginTop: 22 }}>
        <Btn label={busy ? 'Resetting…' : 'Reset password'} onPress={reset} disabled={!canSubmit || busy} />
      </View>
    </Screen>
  );
};

export default ResetPassword;
