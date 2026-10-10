import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { notify } from '@/components/design/Notify';
import { router, Link } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { storeSession, getToken, getRememberedIdentifier, rememberIdentifier } from '@/lib/secureStore';
import { enforceHardExpiry, unlockSession } from '@/lib/session';
import { pendingWhatsAppApproval } from '@/lib/pendingApproval';
import { registerForPushNotifications } from '@/lib/notifications';
import { publicPost } from '@/lib/api';
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
  const signinInFlight = useRef(false);

  // Offer instant sign-in only if the user enabled biometrics, the device
  // supports them, and a previous session token is still on the device.
  useEffect(() => {
    (async () => {
      await enforceHardExpiry();
      const remembered = await getRememberedIdentifier();
      if (remembered) setForm((current) => ({ ...current, email: current.email || remembered }));
      const [enabled, available, token] = await Promise.all([
        isBiometricEnabled(),
        isBiometricAvailable(),
        getToken(),
      ]);
      setBioReady(enabled && available && !!token);
    })().catch(() => setBioReady(false));
  }, []);

  const enterAccount = async () => {
    await unlockSession();
    // Bind this phone's push token to whoever is signing in now. Signup was the
    // only registration, so a phone that changed hands kept delivering the
    // previous customer's alerts, and a new phone never received any. No
    // permission prompt here: it binds only where alerts are already allowed.
    registerForPushNotifications(false).catch(() => {});
    const token = await pendingWhatsAppApproval();
    router.replace(token ? { pathname: '/waapprove', params: { token } } : '/home');
  };

  const handleBiometricSignin = async () => {
    if (signinInFlight.current) return;
    if (!bioReady) {
      notify('Biometric sign-in', 'Enable biometrics from Me → Face ID / Fingerprint after signing in with your password.');
      return;
    }
    signinInFlight.current = true;
    try {
      if (await enforceHardExpiry() || !(await getToken())) {
        setBioReady(false);
        notify('Sign in again', 'Enter your password to start a new session.');
        return;
      }
      if (await authenticate('Sign in to Zitch')) await enterAccount();
    } catch {
      notify('Sign in again', 'Please sign in with your password.');
    } finally { signinInFlight.current = false; }
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
    if (signinInFlight.current) return;
    signinInFlight.current = true;
    setIsChecking(true);
    if (form.email.trim() === '') {
      notify('Error', 'Email or phone cannot be empty');
      signinInFlight.current = false;
      setIsChecking(false);
      return;
    }
    if (form.password.trim() === '') {
      notify('Error', 'Password cannot be empty');
      signinInFlight.current = false;
      setIsChecking(false);
      return;
    }
    try {
      const response = await publicPost('/api/sigin/', {
        email_or_phone: form.email.trim(),
        password: form.password,
      });
      const result = await response.json().catch(() => ({}));
      if (response.ok && result.access_token) {
        // Persist the session BEFORE navigating so the auth guard sees a token.
        await storeSession(result);
        await AsyncStorage.setItem('userID', form.email);
        await AsyncStorage.setItem('sessionExpiration', Date.now().toString());
        await rememberIdentifier(form.email);
        await enterAccount();
      } else {
        notify('Error', result.message || 'Incorrect Details');
      }
    } catch (error) {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      signinInFlight.current = false;
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
        <ZMark size={56} badge glow />
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
          autoCapitalize="none"
          autoCorrect={false}
          autoComplete="username"
          placeholder="Email or phone number"
          prefix={<ZIcon name="user" size={18} color={c.ink3} />}
        />
        <Field
          label="Password"
          value={form.password}
          onChangeText={(e) => setForm({ ...form, password: e })}
          secureTextEntry
          autoComplete="current-password"
          textContentType="password"
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
