import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, Animated, Easing, ScrollView } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import ZIcon from '@/components/design/ZIcon';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { ZMark } from '@/components/design/Brand';
import { NText } from '@/components/design/Naira';
import { Btn } from '@/components/design/ui';
import { getSessionGeneration, getToken } from '@/lib/secureStore';
import { useTheme, font } from '@/lib/theme';

const SLIDES = [
  { icon: 'send', t: 'Send money with confidence', d: 'Send money to Zitch and Nigerian bank accounts when your account is enabled.' },
  { wa: true, t: 'Bank on WhatsApp', d: 'Check your balance, send money and pay bills right inside your WhatsApp chats.' },
  { icon: 'more', t: 'Everyday payments, made simple', d: 'Airtime, data and supported bill payments, with your account status in one place.' },
];

// Dark brand gradient for the splash (design AuthShell `dark`).
const SPLASH_GRADIENT = ['#0C4D47', '#063A34', '#04221F'] as const;

// Coin-flip loader: the badge mark flips in 3D, rotateY 0→360 looping ~1.9s.
const CoinFlip = () => {
  const spin = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.timing(spin, {
        toValue: 1,
        duration: 1900,
        easing: Easing.inOut(Easing.ease),
        useNativeDriver: true,
      })
    );
    loop.start();
    return () => loop.stop();
  }, [spin]);
  const rotateY = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  return (
    <Animated.View style={{ transform: [{ perspective: 600 }, { rotateY }] }}>
      <ZMark size={104} badge glow />
    </Animated.View>
  );
};

const Index = () => {
  const { c } = useTheme();
  const [ready, setReady] = useState(false);
  const [i, setI] = useState(0);
  const s = SLIDES[i];
  const last = i === SLIDES.length - 1;

  // Returning user (a session token is on the device) → unlock screen.
  // Mid-registration (no token, but an OTP request is pending and recent) →
  // resume the OTP screen instead of dropping back to onboarding.
  // Otherwise → show the onboarding slides.
  useEffect(() => {
    let active = true;
    const generation = getSessionGeneration();
    const current = () => active && generation === getSessionGeneration();
    (async () => {
      const t = await getToken();
      if (!current()) return;
      if (t) { router.replace('/signin'); return; }
      const pend = await AsyncStorage.getItem('otpPending');
      if (!current()) return;
      if (pend && Number(pend) <= Date.now() && Date.now() - Number(pend) < 60 * 60 * 1000) { router.replace('/otp'); return; }
      if (pend) await AsyncStorage.removeItem('otpPending'); // stale — clear it
      if (!current()) return;
      setReady(true);
    })().catch(() => { if (current()) setReady(true); });
    return () => { active = false; };
  }, []);

  // Coin-flip brand loader on open (instead of a blank flash) while we decide.
  if (!ready) {
    return (
      <LinearGradient colors={SPLASH_GRADIENT} style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
        <CoinFlip />
        <View style={{ height: 22 }} />
        <Text style={{ fontSize: 30, fontFamily: font.extrabold, letterSpacing: 30 * 0.18, color: '#fff' }}>ZITCH</Text>
        <Text style={{ fontSize: 14, color: 'rgba(255,255,255,.7)', marginTop: 8, fontFamily: font.regular }}>Pay. Send. Grow.</Text>
        <Text style={{ position: 'absolute', bottom: 36, fontSize: 11.5, color: 'rgba(255,255,255,.45)', fontFamily: font.regular }}>Your account. Protected.</Text>
      </LinearGradient>
    );
  }

  return (
    <LinearGradient colors={c.bgGradient} style={{ flex: 1 }}>
      <SafeAreaView style={{ flex: 1 }}>
        {/* skip */}
        <View style={{ width: '100%', maxWidth: 720, alignSelf: 'center', flexDirection: 'row', justifyContent: 'flex-end', paddingHorizontal: 20, paddingTop: 8 }}>
          <Pressable accessibilityRole="button" accessibilityLabel="Skip introduction" onPress={() => router.replace('/signin')}
            style={{ minHeight: 44, minWidth: 44, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 8 }}>
            <Text style={{ fontSize: 14, fontFamily: font.semibold, color: c.ink2 }}>Skip</Text>
          </Pressable>
        </View>

        <ScrollView showsVerticalScrollIndicator={false}
          contentContainerStyle={{ flexGrow: 1, width: '100%', maxWidth: 720, alignSelf: 'center', paddingBottom: 8 }}>
        {/* slide */}
        <View style={{ flexGrow: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 24, paddingTop: 24, paddingBottom: 28 }}>
          {s.wa ? (
            <View
              style={{ width: 150, height: 150, borderRadius: 44, alignItems: 'center', justifyContent: 'center', backgroundColor: '#25D366', shadowColor: '#25D366', shadowOpacity: 0.6, shadowRadius: 26, shadowOffset: { width: 0, height: 22 }, elevation: 8 }}
            >
              <WhatsAppGlyph size={66} color="#fff" />
            </View>
          ) : (
            <LinearGradient
              colors={c.heroGradient}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={{ width: 150, height: 150, borderRadius: 44, alignItems: 'center', justifyContent: 'center', shadowColor: '#00847B', shadowOpacity: 0.5, shadowRadius: 26, shadowOffset: { width: 0, height: 22 }, elevation: 8 }}
            >
              <ZIcon name={s.icon!} size={64} color="#fff" />
            </LinearGradient>
          )}
          <Text accessibilityRole="header" accessibilityLiveRegion="polite" style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, marginTop: 32, textAlign: 'center' }}>{s.t}</Text>
          <NText style={{ fontSize: 14.5, color: c.ink2, marginTop: 12, lineHeight: 22, textAlign: 'center', fontFamily: font.regular }}>{s.d}</NText>
        </View>

        {/* dots */}
        <View accessible accessibilityLabel={`Introduction ${i + 1} of ${SLIDES.length}`} style={{ flexDirection: 'row', justifyContent: 'center', gap: 8, marginBottom: 24 }}>
          {SLIDES.map((_, k) => (
            <View key={k} style={{ width: k === i ? 24 : 8, height: 8, borderRadius: 999, backgroundColor: k === i ? c.brand : c.line }} />
          ))}
        </View>

        {/* cta */}
        <View style={{ paddingHorizontal: 22 }}>
          <Btn label={last ? 'Get started' : 'Next'}
            onPress={() => (last ? router.replace('/register') : setI(index => Math.min(index + 1, SLIDES.length - 1)))} />
        </View>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center', gap: 6, paddingHorizontal: 20, paddingVertical: 12 }}>
          <Text style={{ fontSize: 14, color: c.ink2, fontFamily: font.regular }}>Already have an account?</Text>
          <Pressable accessibilityRole="link" accessibilityLabel="Sign in" onPress={() => router.replace('/signin')}
            style={{ minHeight: 44, justifyContent: 'center', paddingHorizontal: 4 }}>
            <Text style={{ fontFamily: font.bold, color: c.brand, fontSize: 14 }}>Sign in</Text>
          </Pressable>
        </View>
        </ScrollView>
      </SafeAreaView>
    </LinearGradient>
  );
};

export default Index;
