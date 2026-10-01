import React, { useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router, Link } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { notify } from '@/components/design/Notify';
import { publicPost } from '@/lib/api';
import { isCompleteRegistrationName, splitRegistrationName } from '@/lib/registration';
import ZIcon from '@/components/design/ZIcon';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

const Register = () => {
  const { c } = useTheme();
  const [isRegistering, setIsRegistering] = useState(false);
  const submitting = useRef(false);
  const [form, setForm] = useState({ name: '', email: '', phone: '' });

  const nameOk = isCompleteRegistrationName(form.name);
  const phoneOk = /^0\d{10}$/.test(form.phone);
  const emailValue = form.email.trim();
  const emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailValue);
  const valid = nameOk && phoneOk && emailOk;

  const handleSignup = async () => {
    if (!valid || submitting.current) return;
    submitting.current = true;
    setIsRegistering(true);
    try {
      const { firstName, lastName } = splitRegistrationName(form.name);
      const email = form.email.trim().toLowerCase();
      const response = await publicPost('/api/phone_verification/', {
        email,
        phone: form.phone,
      });
      const result = await response.json().catch(() => ({}));
      if (response.ok) {
        // OTP verification creates the account and reads these exact fields.
        // Persist them before navigating so SMS autofill cannot race the write.
        await AsyncStorage.multiSet([
          ['UserEmail', email],
          ['UserPhone', form.phone],
          ['UserFirstName', firstName],
          ['UserLastName', lastName],
          ['otpPending', Date.now().toString()],
        ]);
        // Mark OTP as pending so reopening the app mid-verification resumes here
        // instead of dropping back to onboarding (cleared on verify / going back).
        router.push('/otp');
      } else {
        notify('Error', result.message || 'Failed to register an account');
      }
    } catch (error) {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      submitting.current = false;
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
        Open your Zitch account in a few simple steps
      </Text>

      <View style={{ gap: 16 }}>
        <View>
          <Field
            label="Full name"
            value={form.name}
            onChangeText={(e) => setForm({ ...form, name: e })}
            placeholder="William Adeyemi"
            autoCapitalize="words"
            prefix={<ZIcon name="user" size={18} color={c.ink3} />}
          />
          {form.name.length > 0 && !nameOk && (
            <Text style={{ fontSize: 12, color: c.red, marginTop: 6, marginLeft: 2, fontFamily: font.regular }}>Enter your first and last name</Text>
          )}
        </View>
        <View>
          <Field
            label="Phone number"
            value={form.phone}
            onChangeText={(e) => setForm({ ...form, phone: e.replace(/\D/g, '').slice(0, 11) })}
            keyboardType="number-pad"
            placeholder="0801 234 5678"
            prefix={<ZIcon name="airtime" size={18} color={c.ink3} />}
          />
          {form.phone.length > 0 && !phoneOk && (
            <Text style={{ fontSize: 12, color: c.red, marginTop: 6, marginLeft: 2, fontFamily: font.regular }}>Enter a valid 11-digit number (e.g. 0801 234 5678)</Text>
          )}
        </View>
        <View>
          <Field
            label="Email address"
            value={form.email}
            onChangeText={(e) => setForm({ ...form, email: e })}
            keyboardType="email-address"
            autoCapitalize="none"
            placeholder="you@email.com"
            prefix={<ZIcon name="remita" size={18} color={c.ink3} />}
          />
          {form.email.length > 0 && !emailOk && (
            <Text style={{ fontSize: 12, color: c.red, marginTop: 6, marginLeft: 2, fontFamily: font.regular }}>Enter a valid email address</Text>
          )}
        </View>
      </View>
      <Text style={{ fontSize: 12, color: c.ink3, lineHeight: 18, marginTop: 14, fontFamily: font.regular }}>
        By continuing you agree to Zitch&apos;s <Text style={{ color: c.brand, fontFamily: font.semibold }}>Terms</Text> &{' '}
        <Text style={{ color: c.brand, fontFamily: font.semibold }}>Privacy Policy</Text>.
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
