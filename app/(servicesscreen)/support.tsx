import React from 'react';
import { View, Text, Linking, Alert } from 'react-native';
import { router } from 'expo-router';
import { Screen, Header, Card, ZItem } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { SUPPORT_EMAIL, SUPPORT_PHONE, SUPPORT_WHATSAPP, FAQ_URL } from '@/components/configFiles/links';

const open = async (url: string) => {
  try {
    const ok = await Linking.canOpenURL(url);
    if (ok) await Linking.openURL(url);
    else Alert.alert('Unavailable', 'No app is available to handle this action.');
  } catch {
    Alert.alert('Error', 'Could not open this link.');
  }
};

const Support = () => {
  const { c } = useTheme();
  const chev = <ZIcon name="right" size={18} color={c.ink3} />;

  const channels = [
    { icon: 'chat', title: 'Chat on WhatsApp', sub: 'Fastest response, 8am–8pm', go: () => open(`https://wa.me/${SUPPORT_WHATSAPP}`) },
    { icon: 'phone', title: 'Call us', sub: SUPPORT_PHONE, go: () => open(`tel:${SUPPORT_PHONE}`) },
    { icon: 'mail', title: 'Email support', sub: SUPPORT_EMAIL, go: () => open(`mailto:${SUPPORT_EMAIL}`) },
    { icon: 'help', title: 'Help center / FAQ', sub: 'Answers to common questions', go: () => open(FAQ_URL) },
  ];

  return (
    <Screen pad={false}>
      <View style={{ paddingHorizontal: 20 }}>
        <Header title="Customer Service" sub="We're here to help" onBack={() => router.back()} />
      </View>

      <View style={{ paddingHorizontal: 16 }}>
        <Card style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
          <View style={{ width: 48, height: 48, borderRadius: 15, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="insurance" size={24} color={c.brand} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ fontFamily: font.bold, color: c.ink1, fontSize: 15 }}>Average reply under 5 min</Text>
            <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>Our team typically responds quickly on WhatsApp.</Text>
          </View>
        </Card>

        <Card style={{ marginTop: 14 }} pad={0}>
          <View style={{ paddingHorizontal: 16 }}>
            {channels.map((r, i) => (
              <ZItem key={r.title} icon={r.icon} title={r.title} sub={r.sub} onPress={r.go} last={i === channels.length - 1} right={chev} />
            ))}
          </View>
        </Card>

        <Text style={{ fontSize: 12, color: c.ink3, textAlign: 'center', marginTop: 18, paddingHorizontal: 20, fontFamily: font.regular }}>
          Zitch will never ask for your PIN, password or OTP. Keep them private.
        </Text>
      </View>
    </Screen>
  );
};

export default Support;
