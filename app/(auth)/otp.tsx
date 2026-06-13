import React, { useState, useEffect, useCallback, useRef } from 'react';
import { View, Text, Alert, TextInput, Pressable } from 'react-native';
import { router } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import baseUrl from '@/components/configFiles/apiConfig';
import { saveToken } from '@/lib/secureStore';
import { Screen, Header, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

const OTP_LEN = 6;

const OTPVerification = () => {
  const { c } = useTheme();
  const [otp, setOtp] = useState('');
  const [isCheckingOtp, setIsCheckingOtp] = useState(false);
  const [userPhone, setUserPhone] = useState('');
  const [seconds, setSeconds] = useState(24);
  const inputRef = useRef<TextInput>(null);

  useEffect(() => {
    AsyncStorage.getItem('UserPhone').then((p) => p && setUserPhone(p));
  }, []);

  useEffect(() => {
    if (seconds <= 0) return;
    const t = setTimeout(() => setSeconds((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [seconds]);

  const handleCheckOtp = useCallback(async () => {
    setIsCheckingOtp(true);
    try {
      const response = await fetch(`${baseUrl}/api/verify_otp/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ otp, phone: userPhone }),
      });
      const result = await response.json();
      if (response.ok) {
        await saveToken(result.access_token);
        router.push('/setup');
      } else {
        Alert.alert('Error', result.message || 'Failed to verify OTP');
        setOtp('');
      }
    } catch (error) {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsCheckingOtp(false);
    }
  }, [otp, userPhone]);

  // Auto-submit once all digits are entered.
  useEffect(() => {
    if (otp.length === OTP_LEN && !isCheckingOtp) handleCheckOtp();
  }, [otp, isCheckingOtp, handleCheckOtp]);

  const handleResendOtp = async () => {
    if (seconds > 0) return;
    try {
      const response = await fetch(`${baseUrl}/api/resend_verify_otp/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: userPhone }),
      });
      const result = await response.json();
      if (response.ok) {
        setSeconds(24);
        Alert.alert('Success', 'OTP has been resent');
      } else {
        Alert.alert('Error', result.message || 'Failed to resend OTP');
      }
    } catch (error) {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    }
  };

  const masked = userPhone ? userPhone.replace(/(\d{4})(\d{3})(\d{0,4})/, '$1 $2 $3') : 'your phone';

  return (
    <Screen scroll={false}>
      <Header onBack={() => router.replace('/register')} />
      <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, marginTop: 6 }}>Verify your number</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, fontFamily: font.regular }}>
        Enter the {OTP_LEN}-digit code sent to <Text style={{ fontFamily: font.bold, color: c.ink1 }}>{masked}</Text>
      </Text>

      {/* The boxes are display-only; a hidden numeric TextInput captures input
          from the device keypad (and OS SMS autofill). Tapping the boxes focuses it. */}
      <Pressable onPress={() => inputRef.current?.focus()}>
        <View style={{ flexDirection: 'row', gap: 10, marginTop: 28, marginBottom: 18 }}>
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
          value={otp}
          onChangeText={(t) => setOtp(t.replace(/\D/g, '').slice(0, OTP_LEN))}
          keyboardType="number-pad"
          inputMode="numeric"
          maxLength={OTP_LEN}
          autoFocus
          caretHidden
          editable={!isCheckingOtp}
          textContentType="oneTimeCode"
          autoComplete="sms-otp"
          importantForAutofill="yes"
          // Cover the boxes so taps focus it; invisible so only the boxes show.
          style={{ position: 'absolute', top: 28, left: 0, right: 0, height: 58, opacity: 0 }}
        />
      </Pressable>

      <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular }}>
        Didn't get it?{' '}
        <Text onPress={handleResendOtp} style={{ color: c.brand, fontFamily: font.bold }}>
          {seconds > 0 ? `Resend in 0:${String(seconds).padStart(2, '0')}` : 'Resend code'}
        </Text>
      </Text>

      <View style={{ flex: 1 }} />
      {isCheckingOtp && <Btn label="Verifying…" disabled onPress={() => {}} style={{ marginBottom: 12 }} />}
    </Screen>
  );
};

export default OTPVerification;
