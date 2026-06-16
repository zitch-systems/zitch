import React, { useEffect, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { SafeAreaView } from 'react-native-safe-area-context';
import { router, Link } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import ZIcon from '@/components/design/ZIcon';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { Loading } from '@/components/design/Loading';
import { NText } from '@/components/design/Naira';
import { getToken } from '@/lib/secureStore';
import { font } from '@/lib/theme';

const SLIDES = [
  { icon: 'send', t: 'Send money instantly', d: 'Free transfers to any Nigerian bank in seconds — with saved beneficiaries.' },
  { wa: true, t: 'Bank on WhatsApp', d: 'Check your balance, send money and pay bills right from your chats — no download needed.' },
  { icon: 'wallet', t: 'Everything in one app', d: 'Airtime, data, bills, dollar cards, savings and loans — all in one place.' },
];

const GRADIENT = ['#DDF3EF', '#EFF7F5', '#F5FAF9'] as const;

const Index = () => {
  const [ready, setReady] = useState(false);
  const [i, setI] = useState(0);
  const s = SLIDES[i];
  const last = i === SLIDES.length - 1;

  // Returning user (a session token is on the device) → unlock screen.
  // Mid-registration (no token, but an OTP request is pending and recent) →
  // resume the OTP screen instead of dropping back to onboarding.
  // Otherwise → show the onboarding slides.
  useEffect(() => {
    (async () => {
      const t = await getToken();
      if (t) { router.replace('/signin'); return; }
      const pend = await AsyncStorage.getItem('otpPending');
      if (pend && Date.now() - Number(pend) < 60 * 60 * 1000) { router.replace('/otp'); return; }
      if (pend) await AsyncStorage.removeItem('otpPending'); // stale — clear it
      setReady(true);
    })();
  }, []);

  // Animated brand loader on open (instead of a blank flash) while we decide.
  if (!ready) {
    return (
      <LinearGradient colors={GRADIENT} style={{ flex: 1 }}>
        <Loading />
      </LinearGradient>
    );
  }

  return (
    <LinearGradient colors={GRADIENT} style={{ flex: 1 }}>
      <SafeAreaView style={{ flex: 1 }}>
        {/* skip */}
        <View style={{ flexDirection: 'row', justifyContent: 'flex-end', paddingHorizontal: 20, paddingTop: 8 }}>
          <Pressable onPress={() => router.replace('/signin')}>
            <Text style={{ fontSize: 14, fontFamily: font.semibold, color: '#6E8B86' }}>Skip</Text>
          </Pressable>
        </View>

        {/* slide */}
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32 }}>
          <LinearGradient
            colors={['#0C5249', '#00847B', '#0FA295']}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={{ width: 150, height: 150, borderRadius: 44, alignItems: 'center', justifyContent: 'center', shadowColor: '#00847B', shadowOpacity: 0.5, shadowRadius: 26, shadowOffset: { width: 0, height: 22 }, elevation: 8 }}
          >
            {s.wa ? <WhatsAppGlyph size={66} color="#fff" /> : <ZIcon name={s.icon!} size={64} color="#fff" />}
          </LinearGradient>
          <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: '#000000', marginTop: 38, textAlign: 'center' }}>{s.t}</Text>
          <NText style={{ fontSize: 14.5, color: '#6E8B86', marginTop: 12, lineHeight: 22, textAlign: 'center', fontFamily: font.regular }}>{s.d}</NText>
        </View>

        {/* dots */}
        <View style={{ flexDirection: 'row', justifyContent: 'center', gap: 8, marginBottom: 24 }}>
          {SLIDES.map((_, k) => (
            <View key={k} style={{ width: k === i ? 24 : 8, height: 8, borderRadius: 999, backgroundColor: k === i ? '#0FA295' : '#E2EEEB' }} />
          ))}
        </View>

        {/* cta */}
        <View style={{ paddingHorizontal: 22 }}>
          <Pressable
            onPress={() => (last ? router.replace('/register') : setI(i + 1))}
            style={{ height: 56, borderRadius: 999, backgroundColor: '#0FA295', alignItems: 'center', justifyContent: 'center' }}
          >
            <Text style={{ color: '#fff', fontSize: 16, fontFamily: font.bold }}>{last ? 'Get Started' : 'Next'}</Text>
          </Pressable>
        </View>
        <View style={{ flexDirection: 'row', justifyContent: 'center', gap: 6, paddingVertical: 22 }}>
          <Text style={{ fontSize: 14, color: '#6E8B86', fontFamily: font.regular }}>Already have an account?</Text>
          <Link href="/signin">
            <Text style={{ fontFamily: font.bold, color: '#0FA295', fontSize: 14 }}>Sign in</Text>
          </Link>
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
};

export default Index;
