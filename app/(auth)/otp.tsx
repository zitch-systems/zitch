import React, { useState, useEffect, useCallback, useRef } from 'react';
import { View, Text, TextInput, Pressable } from 'react-native';
import { notify } from '@/components/design/Notify';
import { router } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { publicPost } from '@/lib/api';
import { unlockSession } from '@/lib/session';
import { getSessionGeneration, storeSession } from '@/lib/secureStore';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Btn } from '@/components/design/ui';
import { Stepper } from '@/components/design/Stepper';
import { useTheme, font } from '@/lib/theme';

const OTP_LEN = 6;

const OTPVerification = () => {
  const { c } = useTheme();
  const [otp, setOtp] = useState('');
  const [isCheckingOtp, setIsCheckingOtp] = useState(false);
  const [isResending, setIsResending] = useState(false);
  const [userPhone, setUserPhone] = useState('');
  const [phoneLoaded, setPhoneLoaded] = useState(false);
  const [seconds, setSeconds] = useState(24);
  const inputRef = useRef<TextInput>(null);
  const mounted = useRef(true);
  const resending = useRef(false);
  const verifying = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    const generation = getSessionGeneration();
    AsyncStorage.getItem('UserPhone').then((p) => {
      if (mounted.current && generation === getSessionGeneration() && p && /^0[0-9]{10}$/.test(p)) setUserPhone(p);
    }).catch(() => {}).finally(() => { if (mounted.current) setPhoneLoaded(true); });
  }, []);

  useEffect(() => {
    if (seconds <= 0) return;
    const t = setTimeout(() => setSeconds((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [seconds]);

  const submittedRef = useRef('');
  const handleCheckOtp = useCallback(async () => {
    // SMS autofill can finish before AsyncStorage has restored the phone. Keep
    // the complete code on screen; the effect retries automatically when the
    // phone arrives because this callback depends on userPhone.
    if (!userPhone || otp.length !== OTP_LEN || resending.current || verifying.current) return;
    // Verify each complete code at most once. Without this, the auto-submit
    // effect re-fires after isCheckingOtp resets and re-sends the (now-consumed)
    // code, producing a false "invalid OTP" right after a successful verify.
    if (submittedRef.current === otp) return;
    submittedRef.current = otp;
    verifying.current = true;
    setIsCheckingOtp(true);
    const generation = getSessionGeneration();
    try {
      // Read the name (captured at register) at submit time rather than from
      // async-loaded state, so an instant SMS autofill can't race the load and
      // send an unnamed verify — the account is created here, and it's opened in
      // this name.
      const [first, last] = await Promise.all([
        AsyncStorage.getItem('UserFirstName'),
        AsyncStorage.getItem('UserLastName'),
      ]);
      if (!mounted.current || generation !== getSessionGeneration()) return;
      const response = await publicPost('/api/verify_otp/', {
        otp, phone: userPhone, first_name: first || '', last_name: last || '',
      });
      const result = await response.json();
      if (!mounted.current || generation !== getSessionGeneration()) return;
      if (response.ok && result.access_token) {
        await storeSession(result, generation);
        const authenticatedGeneration = getSessionGeneration();
        if (!mounted.current) return;
        await unlockSession();
        if (!mounted.current || authenticatedGeneration !== getSessionGeneration()) return;
        await AsyncStorage.removeItem('otpPending'); // verification done
        if (!mounted.current || authenticatedGeneration !== getSessionGeneration()) return;
        router.replace('/setpassword');
      } else {
        notify('Error', result.message || 'Failed to verify OTP');
        setOtp('');
        submittedRef.current = ''; // let them try a fresh code
      }
    } catch {
      if (!mounted.current || generation !== getSessionGeneration()) return;
      notify('Error', 'Something went wrong. Please try again later.');
      submittedRef.current = '';
      setOtp('');
    } finally {
      verifying.current = false;
      if (mounted.current) setIsCheckingOtp(false);
    }
  }, [otp, userPhone]);

  // Auto-submit once all digits are entered (guarded above against re-runs).
  useEffect(() => {
    if (otp.length !== OTP_LEN) return;
    const timer = setTimeout(() => void handleCheckOtp(), 0);
    return () => clearTimeout(timer);
  }, [otp, handleCheckOtp]);

  const handleResendOtp = async () => {
    if (seconds > 0 || resending.current || verifying.current) return;
    if (!userPhone) {
      notify('Error', 'Your phone number is still loading. Please try again.');
      return;
    }
    setIsResending(true);
    resending.current = true;
    const generation = getSessionGeneration();
    const current = () => mounted.current && generation === getSessionGeneration();
    try {
      const response = await publicPost('/api/resend_verify_otp/', { phone: userPhone });
      const result = await response.json();
      if (!current()) return;
      if (response.ok) {
        // A resent code is a fresh attempt. Clear any partial/previous digits,
        // release the one-submit guard, and return focus to the native input so
        // typing and Android/iOS SMS autofill work immediately.
        setOtp('');
        submittedRef.current = '';
        setSeconds(24);
        requestAnimationFrame(() => inputRef.current?.focus());
        notify('Code sent', 'A new verification code has been sent by SMS.');
      } else {
        notify('Error', result.message || 'Failed to resend OTP');
      }
    } catch {
      if (current()) notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      resending.current = false;
      if (mounted.current) setIsResending(false);
    }
  };

  const masked = userPhone ? `${userPhone.slice(0, 4)} *** ${userPhone.slice(-4)}` : 'your phone';

  const leave = (route: '/register' | '/signin') => {
    // Leaving invalidates pending responses immediately, before navigation commits.
    mounted.current = false;
    void AsyncStorage.removeItem('otpPending').catch(() => {});
    router.replace(route);
  };

  if (phoneLoaded && !userPhone) return (
    <Screen>
      <Header onBack={() => leave('/signin')} />
      <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1 }}>Start phone verification</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 8, marginBottom: 24, fontFamily: font.regular }}>Enter your details to request an SMS code. Your previous phone verification could not be restored.</Text>
      <Btn label="Enter my details" onPress={() => leave('/register')} />
    </Screen>
  );

  if (isCheckingOtp) {
    return (
      <Screen scroll={false}>
        <Loading label="Verifying your code…" />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header onBack={() => leave('/register')} />
      <Stepper step={2} total={4} label="Step 2 of 4 · Verify phone" />
      <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, marginTop: 6 }}>Verify your number</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, fontFamily: font.regular }}>
        Enter the {OTP_LEN}-digit SMS code sent to <Text style={{ fontFamily: font.bold, color: c.ink1 }}>{masked}</Text>
      </Text>

      {/* The boxes are display-only; a hidden numeric TextInput captures input
          from the device keypad (and OS SMS autofill). Tapping the boxes focuses it. */}
      <Pressable onPress={() => inputRef.current?.focus()}>
        <View accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ flexDirection: 'row', gap: 10, marginTop: 28, marginBottom: 18 }}>
          {Array.from({ length: OTP_LEN }).map((_, k) => (
            <View
              key={k}
              style={{
                flex: 1,
                height: 58,
                borderRadius: 14,
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: c.surface,
                borderWidth: 2,
                borderColor: otp.length === k ? c.brand : c.line,
              }}
            >
              <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1 }}>{otp[k] || ''}</Text>
            </View>
          ))}
        </View>

        <TextInput
          ref={inputRef}
          accessibilityLabel="6-digit SMS verification code"
          accessibilityHint="Your code is verified automatically when all six digits are entered."
          value={otp}
          onChangeText={(t) => setOtp(t.replace(/\D/g, '').slice(0, OTP_LEN))}
          keyboardType="number-pad"
          inputMode="numeric"
          maxLength={OTP_LEN}
          autoFocus
          caretHidden
          editable={!isCheckingOtp && !isResending}
          textContentType="oneTimeCode"
          autoComplete="sms-otp"
          importantForAutofill="yes"
          // Cover the boxes so taps focus it; invisible so only the boxes show.
          // Keep the native field visually hidden without opacity:0. Some Android
          // autofill services ignore a fully transparent OTP target after resend.
          style={{ position: 'absolute', top: 28, left: 0, right: 0, height: 58, color: 'transparent', backgroundColor: 'transparent' }}
        />
      </Pressable>

      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
        <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular }}>
          Didn’t get it?{' '}
        </Text>
        <Pressable
          onPress={handleResendOtp}
          disabled={seconds > 0 || isResending || !userPhone}
          hitSlop={10}
          accessibilityRole="button"
          accessibilityLabel="Resend verification code"
          accessibilityState={{ disabled: seconds > 0 || isResending || !userPhone }}
        >
          <Text style={{ color: seconds > 0 || isResending || !userPhone ? c.ink3 : c.brand, fontFamily: font.bold, fontSize: 13.5 }}>
            {isResending ? 'Sending…' : seconds > 0 ? `Resend in 0:${String(seconds).padStart(2, '0')}` : 'Resend code'}
          </Text>
        </Pressable>
      </View>
      <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular, marginTop: 12 }}>
        Already have an account?{' '}
        <Text
          accessibilityRole="link"
          onPress={() => leave('/signin')}
          style={{ color: c.brand, fontFamily: font.bold }}
        >
          Sign in
        </Text>
      </Text>

      <View style={{ flex: 1 }} />
    </Screen>
  );
};

export default OTPVerification;
