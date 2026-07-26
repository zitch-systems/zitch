import React, { useState } from 'react';
import { View, Text } from 'react-native';
import { router, Link } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { publicPost } from '@/lib/api';
import { notify } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { Stepper } from '@/components/design/Stepper';
import { TERMS_URL, PRIVACY_URL } from '@/components/configFiles/links';
import { useTheme, font } from '@/lib/theme';

// Split a typed full name into first / last for the account record. The first
// token is the first name; everything after it is the last name (so middle names
// stay attached to the surname). One-word names keep an empty last name.
const splitName = (full: string): { first: string; last: string } => {
  const parts = full.trim().replace(/\s+/g, ' ').split(' ');
  return { first: parts[0] || '', last: parts.slice(1).join(' ') };
};

const Register = () => {
  const { c } = useTheme();
  const [isRegistering, setIsRegistering] = useState(false);
  const [form, setForm] = useState({ name: '', email: '', phone: '' });

  // Require a real name (first + last) and a full 11-digit Nigerian number. The
  // name is mandatory: the customer's dedicated funding account is opened in it,
  // and an unnamed account can't be safely funded by transfer.
  const { first, last } = splitName(form.name);
  const valid = form.phone.trim().length === 11 && !!first && !!last;

  const handleSignup = async () => {
    if (form.phone.trim() === '') {
      notify('Error', 'Phone cannot be empty');
      return;
    }
    if (!first || !last) {
      notify('Enter your full name', 'Please enter your first and last name.');
      return;
    }
    setIsRegistering(true);
    try {
      const response = await publicPost('/api/phone_verification/', { email: form.email, phone: form.phone });
      const result = await response.json();
      if (response.ok) {
        await AsyncStorage.setItem('UserEmail', form.email);
        await AsyncStorage.setItem('UserPhone', form.phone);
        // Carry the name forward to OTP verification, which creates the account
        // (so it's named from the very first moment). Same pattern as email/phone.
        await AsyncStorage.multiSet([
          ['UserFirstName', first],
          ['UserLastName', last],
        ]);
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
      <Stepper step={1} total={4} label="Step 1 of 4 · Your details" />
      <Text style={{ fontSize: 26, fontFamily: font.extrabold, color: c.ink1, marginTop: 6 }}>Create your account</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, marginBottom: 26, fontFamily: font.regular }}>
        Open your free account in minutes
      </Text>

      <View style={{ gap: 16 }}>
        <Field
          label="Full name"
          value={form.name}
          onChangeText={(e) => setForm({ ...form, name: e })}
          autoCapitalize="words"
          placeholder="e.g. Ada Okafor"
          textContentType="name"
          autoComplete="name"
          prefix={<ZIcon name="user" size={18} color={c.ink3} />}
        />
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
      <Text style={{ fontSize: 12, color: c.ink3, lineHeight: 18, marginTop: 12, fontFamily: font.regular }}>
        Use your real name as it appears on your BVN — your Zitch account for
        receiving money is opened in this name.
      </Text>
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

