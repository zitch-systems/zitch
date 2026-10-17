import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { publicPost } from '@/lib/api';
import { getSessionGeneration } from '@/lib/secureStore';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { ZMark } from '@/components/design/Brand';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

// Step 1 of password recovery: ask for the account phone OR email and request a
// reset code. The backend always replies 200 with a generic message (it never
// reveals whether the account exists), so we advance to the code step regardless.
const ForgotPassword = () => {
  const { c } = useTheme();
  const [ident, setIdent] = useState('');
  const [busy, setBusy] = useState(false);
  const requesting = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const identifier = ident.trim();
  const isEmail = identifier.includes('@');
  const valid = identifier.length <= 254 && (isEmail
    ? /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(identifier)
    : /^\+?[0-9]{8,15}$/.test(identifier));

  const requestCode = async () => {
    if (requesting.current) return;
    if (!valid) {
      notify('Error', 'Enter the phone number or email on your account');
      return;
    }
    requesting.current = true;
    setBusy(true);
    const generation = getSessionGeneration();
    const current = () => mounted.current && generation === getSessionGeneration();
    try {
      const response = await publicPost('/api/password/forgot/', { email_or_phone: identifier });
      if (!current()) return;
      if (response.ok) {
        router.push({ pathname: '/resetpassword', params: { ident: identifier } });
      } else {
        const result = await response.json().catch(() => ({} as any));
        if (!current()) return;
        notify('Error', result.message || 'Could not send a reset code. Please try again.');
      }
    } catch {
      if (current()) notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      requesting.current = false;
      if (mounted.current) setBusy(false);
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
        Enter the phone number or email on your account to request a password reset code.
      </Text>

      <Field
        label="Email or phone"
        value={ident}
        onChangeText={setIdent}
        keyboardType="email-address"
        autoCapitalize="none"
        autoCorrect={false}
        autoComplete="username"
        editable={!busy}
        maxLength={254}
        returnKeyType="go"
        onSubmitEditing={requestCode}
        placeholder="Email or phone number"
        prefix={<ZIcon name="user" size={18} color={c.ink3} />}
      />
      {identifier.length > 0 && !valid && <Text accessibilityLiveRegion="polite" style={{ fontSize: 12, color: c.red, marginTop: 8, fontFamily: font.regular }}>
        Enter a valid email or phone number using digits and an optional leading +.
      </Text>}

      <View style={{ marginTop: 26 }}>
        <Btn label={busy ? 'Sending…' : 'Send reset code'} onPress={requestCode} disabled={busy || !valid} />
      </View>
    </Screen>
  );
};

export default ForgotPassword;

