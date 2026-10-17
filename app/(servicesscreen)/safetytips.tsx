import React from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { Screen, Header, Card, Btn } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

const TIPS = [
  { icon: 'lock', title: 'Never share your PIN or OTP', body: 'Zitch staff will never ask for your transaction PIN, password or one-time code. Anyone who does is a scammer.' },
  { icon: 'faceid', title: 'Lock your app', body: 'Enable biometric sign-in in Settings and use a secure phone lock. Keep your transaction PIN private, even when using biometrics.' },
  { icon: 'send', title: 'Double-check before you send', body: 'Confirm the account name, number and amount before every transfer. If you send money by mistake, contact support immediately. Recovery is not guaranteed.' },
  { icon: 'insurance', title: 'Beware of fake offers', body: 'Ignore messages promising free money, refunds or rewards in exchange for a fee or your details.' },
  { icon: 'bell', title: 'Watch your alerts', body: 'Review transaction notifications. If you see anything you didn’t do, contact support immediately.' },
];

const SafetyTips = () => {
  const { c } = useTheme();
  return (
    <Screen pad={false}>
      <View style={{ paddingHorizontal: 20 }}>
        <Header title="5 Safety Tips" sub="Keep your account secure" onBack={() => router.back()} />
      </View>

      <View style={{ paddingHorizontal: 20, gap: 12 }}>
        {TIPS.map((t) => (
          <Card key={t.title} style={{ flexDirection: 'row', gap: 14 }}>
            <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name={t.icon} size={21} color={c.brand} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ fontFamily: font.bold, color: c.ink1, fontSize: 15 }}>{t.title}</Text>
              <Text style={{ fontSize: 13, color: c.ink3, marginTop: 4, lineHeight: 19, fontFamily: font.regular }}>{t.body}</Text>
            </View>
          </Card>
        ))}
        <Btn label="Contact support" variant="outline" onPress={() => router.push('/support')} />
      </View>
    </Screen>
  );
};

export default SafetyTips;
