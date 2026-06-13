import React from 'react';
import { View, Text, Pressable } from 'react-native';
import { router } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { ZMark } from '@/components/design/Brand';
import { Screen } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

const Setup = () => {
  const { c } = useTheme();

  const Row = ({ icon, title, sub, to }: { icon: string; title: string; sub: string; to: string }) => (
    <Pressable
      onPress={() => router.push(to as any)}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, borderRadius: 18, padding: 16, marginTop: 12 }}
    >
      <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name={icon} size={22} color={c.brand} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={{ fontFamily: font.bold, color: c.ink1, fontSize: 15 }}>{title}</Text>
        <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{sub}</Text>
      </View>
      <ZIcon name="right" size={20} color={c.ink3} />
    </Pressable>
  );

  return (
    <Screen>
      <View style={{ alignItems: 'center', marginTop: 14 }}>
        <ZMark size={48} />
      </View>
      <View style={{ width: 78, height: 78, borderRadius: 24, backgroundColor: 'rgba(0,181,29,.14)', alignItems: 'center', justifyContent: 'center', alignSelf: 'center', marginTop: 24 }}>
        <ZIcon name="check" size={38} color={c.lime} stroke={2.6} />
      </View>
      <Text style={{ fontSize: 22, fontFamily: font.extrabold, color: c.ink1, textAlign: 'center', marginTop: 18 }}>Account created 🎉</Text>
      <Text style={{ fontSize: 14, color: c.ink3, textAlign: 'center', marginTop: 8, marginBottom: 12, fontFamily: font.regular }}>
        Complete your password & PIN setup to secure your account
      </Text>

      <Row icon="lock" title="Password" sub="Set your account password" to="/setpassword" />
      <Row icon="qr" title="Transaction PIN" sub="Authorize payments securely" to="/setpin" />
      <Row icon="fingerprint" title="Thumbprint" sub="Enable biometric sign-in" to="/setthumbprint" />
    </Screen>
  );
};

export default Setup;
