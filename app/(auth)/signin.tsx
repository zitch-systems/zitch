import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { notify } from '@/components/design/Notify';
import { router, Link } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import baseUrl from '@/components/configFiles/apiConfig';
import { saveToken, getToken } from '@/lib/secureStore';
import { unlockSession } from '@/lib/session';
import { isBiometricAvailable, isBiometricEnabled, authenticate } from '@/lib/biometrics';
import ZIcon from '@/components/design/ZIcon';
import { ZMark } from '@/components/design/Brand';
import { Loading } from '@/components/design/Loading';
import { Screen, Field, Btn } from '@/components/design/ui';
import { Hero } from '@/components/design/widgets';
import { useTheme, font } from '@/lib/theme';

const Signin = () => {
  const { c } = useTheme();
  const [ischecking, setIsChecking] = useState(false);
  const [form, setForm] = useState({ email: '', password: '' });
  const [bioReady, setBioReady] = useState(false);
  const autoPrompted = useRef(false);

  // Offer instant sign-in only if the user enabled biometrics, the device
  // supports them, and a previous session token is still on the device.
  useEffect(() => {
    (async () => {
      const [enabled, available, token] = await Promise.all([
        isBiometricEnabled(),
        isBiometricAvailable(),
        getToken(),
      ]);
      setBioReady(enabled && available && !!token);
    })();
  }, []);

  const handleBiometricSignin = async () => {
    if (!bioReady) {
      notify('Biometric sign-in', 'Enable biometrics from Me → Face ID / Fingerprint after signing in with your password.');
      return;
    }
    const ok = await authenticate('Sign in to Zitch');
    if (ok) {
      // Clear any idle lock and refresh activity before entering the app.
      await unlockSession();
      router.replace('/home');
    }
  };

  // Auto-prompt the OS biometric sheet as soon as the screen opens, when a
  // biometric session is ready (returning user, or one locked by the idle
  // timeout). Fires once per mount; a cancel just leaves the password form.
  useEffect(() => {
    if (bioReady && !autoPrompted.current) {
      autoPrompted.current = true;
      handleBiometricSignin();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bioReady]);

  const handleSignin = async () => {
    setIsChecking(true);
    if (form.email.trim() === '') {
      notify('Error', 'Email or phone cannot be empty');
      setIsChecking(false);
      return;
    }
    if (form.password.trim() === '') {
      notify('Error', 'Password cannot be empty');
      setIsChecking(false);
      return;
    }
    try {
      const response = await fetch(`${baseUrl}/api/sigin/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email_or_phone: form.email, password: form.password }),
      });
      const result = await response.json();
      if (response.ok && result.access_token) {
        // Persist the session BEFORE navigating so the auth guard sees a token.
        await saveToken(result.access_token);
        await AsyncStorage.setItem('userID', form.email);
        await AsyncStorage.setItem('sessionExpiration', Date.now().toString());
        await unlockSession(); // clear any idle lock + stamp activity
        router.replace('/home');
      } else {
        notify('Error', result.message || 'Incorrect Details');
      }
    } catch (error) {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsChecking(false);
    }
  };

  if (ischecking) {
    return (
      <Screen scroll={false}>
        <Loading label="Signing you in…" />
      </Screen>
    );
  }

  return (
    <Screen>
      <View style={{ alignItems: 'center', marginTop: 18, marginBottom: 26 }}>
        <ZMark size={56} />
        <Text style={{ fontSize: 26, fontFamily: font.extrabold, color: c.ink1, marginTop: 16, textAlign: 'center' }}>Welcome back</Text>
        <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, fontFamily: font.regular, textAlign: 'center' }}>
          Sign in to continue to Zitch
        </Text>
      </View>

      <View style={{ gap: 16 }}>
        <Field
          label="Email or phone"
          value={form.email}
          onChangeText={(e) => setForm({ ...form, email: e })}
          keyboardType="email-address"
          placeholder="Email or phone number"
          prefix={<ZIcon name="user" size={18} color={c.ink3} />}
        />
        <Field
          label="Password"
          value={form.password}
          onChangeText={(e) => setForm({ ...form, password: e })}
          secureTextEntry
          placeholder="Enter password"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
      </View>
      <Text
        onPress={() => router.push('/forgotpassword')}
        style={{ textAlign: 'right', marginTop: 10, fontSize: 13, fontFamily: font.semibold, color: c.brand }}
      >
        Forgot password?
      </Text>

      <View style={{ marginTop: 26 }}>
        <Btn label="Sign in" onPress={handleSignin} disabled={ischecking} />
      </View>

      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 22, marginBottom: 18 }}>
        <View style={{ flex: 1, height: 1, backgroundColor: c.line }} />
        <Text style={{ fontSize: 12, fontFamily: font.semibold, color: c.ink3 }}>or sign in instantly</Text>
        <View style={{ flex: 1, height: 1, backgroundColor: c.line }} />
      </View>

      {/* instant biometric sign-in — auto-prompts on open; tap to retry */}
      <Pressable onPress={handleBiometricSignin}>
        <Hero style={{ flexDirection: 'row', alignItems: 'center', gap: 14, padding: 16 }} watermark={0}>
          <View style={{ width: 46, height: 46, borderRadius: 14, backgroundColor: 'rgba(255,255,255,.2)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="faceid" size={26} color="#fff" />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 15, fontFamily: font.bold, color: '#fff' }}>Instant sign in</Text>
            <Text style={{ fontSize: 12.5, color: 'rgba(255,255,255,.85)', fontFamily: font.regular }}>Use Face ID or fingerprint</Text>
          </View>
          <ZIcon name="fingerprint" size={24} color="#fff" />
        </Hero>
      </Pressable>

      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 20 }}>
        <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>New to Zitch?</Text>
        <Link href="/register">
          <Text style={{ fontFamily: font.bold, color: c.brand, fontSize: 14 }}>Create account</Text>
        </Link>
      </View>
    </Screen>
  );
};

export default Signin;
