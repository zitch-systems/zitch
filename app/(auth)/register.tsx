import React, { useState } from 'react';
import { View, Text } from 'react-native';
import { router, Link } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import baseUrl from '@/components/configFiles/apiConfig';
import { notify } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

const Register = () => {
  const { c } = useTheme();
  const [isRegistering, setIsRegistering] = useState(false);
  const [form, setForm] = useState({ email: '', phone: '' });

  const valid = form.phone.trim().length >= 10;

  const handleSignup = async () => {
    if (form.phone.trim() === '') {
      notify('Error', 'Phone cannot be empty');
      return;
    }
    setIsRegistering(true);
    try {
      const response = await fetch(`${baseUrl}/api/phone_verification/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: form.email, phone: form.phone }),
      });
      const result = await response.json();
      if (response.ok) {
        await AsyncStorage.setItem('UserEmail', form.email);
        await AsyncStorage.setItem('UserPhone', form.phone);
        router.push('/otp');
      } else {
        notify('Error', result.message || 'Failed to register an account');
      }
    } catch (error) {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsRegistering(false);
    }
  };

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
          prefix={<ZIcon name="remita" size={18} color={c.ink3} />}
        />
      </View>
      <Text style={{ fontSize: 12, color: c.ink3, lineHeight: 18, marginTop: 14, fontFamily: font.regular }}>
        By continuing you agree to Zitch's <Text style={{ color: c.brand, fontFamily: font.semibold }}>Terms</Text> &{' '}
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
