import React, { useState } from 'react';
import { View, Text } from 'react-native';
import { router, Link } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { publicPost } from '@/lib/api';
import { notify } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { TERMS_URL, PRIVACY_URL } from '@/components/configFiles/links';
import { useTheme, font } from '@/lib/theme';

const Register = () => {
  const { c } = useTheme();
  const [isRegistering, setIsRegistering] = useState(false);
  const [form, setForm] = useState({ email: '', phone: '' });

  // Nigerian mobile numbers are 11 digits (the input is capped at 11). Require
  // the full length so an incomplete number never reaches the OTP endpoint.
  const valid = form.phone.trim().length === 11;

  const handleSignup = async () => {
    if (form.phone.trim() === '') {
      notify('Error', 'Phone cannot be empty');
      return;
    }
    setIsRegistering(true);
    try {
      const response = await publicPost('/api/phone_verification/', { email: form.email, phone: form.phone });
      const result = await response.json();
      if (response.ok) {
        await AsyncStorage.setItem('UserEmail', form.email);
        await AsyncStorage.setItem('UserPhone', form.phone);
        // Mark OTP as pending so reopening the app mid-verification resumes here
        // instead of dropping back to onboarding (cleared on verify / going back).
        await AsyncStorage.setItem('otpPending', Date.now().toString());
        router.push('/otp');
      } else {
        notify('Error', result.message || 'Failed to register an account');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsRegistering(false);
    }
  };

  if (isRegistering) {
    return (
      <Screen scroll={false}>
        <Loading label="Creating your account…" />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header onBack={() => router.replace('/signin')} />
      <Text style={{ fontSize: 26, fontFamily: font.extrabold, color: c.ink1, marginTop: 6 }}>Create your account</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, marginBottom: 26, fontFamily: font.regular }}>
        Open your free account in minutes
      </Text>

      <View style={{ gap: 16 }}>
        <Field
          label="Phone number"
          value={form.phone}
          onChangeText={(e) => setForm({ ...form, phone: e.replace(/\D/g, '').slice(0, 11) })}
          keyboardType="number-pad"
          placeholder="0801 234 5678"
          prefix={<ZIcon name="airtime" size={18} color={c.ink3} />}
        />
        <Field
          label="Email (optional)"
          value={form.email}
          onChangeText={(e) => setForm({ ...form, email: e })}
          keyboardType="email-address"
          placeholder="you@email.com"
          prefix={<ZIcon name="mail" size={18} color={c.ink3} />}
        />
      </View>
      <Text style={{ fontSize: 12, color: c.ink3, lineHeight: 18, marginTop: 14, fontFamily: font.regular }}>
        By continuing you agree to Zitch’s{' '}
        <Link href={TERMS_URL as any}><Text style={{ color: c.brand, fontFamily: font.semibold }}>Terms</Text></Link> &{' '}
        <Link href={PRIVACY_URL as any}><Text style={{ color: c.brand, fontFamily: font.semibold }}>Privacy Policy</Text></Link>.
      </Text>

      <View style={{ marginTop: 26 }}>
        <Btn label="Continue" disabled={!valid || isRegistering} onPress={handleSignup} />
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 16 }}>
        <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>Already have an account?</Text>
        <Link href="/signin">
          <Text style={{ fontFamily: font.bold, color: c.brand, fontSize: 14 }}>Sign in</Text>
        </Link>
      </View>
    </Screen>
  );
};

export default Register;

