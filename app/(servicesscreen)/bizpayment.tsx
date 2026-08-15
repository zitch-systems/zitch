import React from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { Screen, Header, Card, Btn, HeaderLink } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

// Payment requests require a server-issued, signed, expiring request tied to a
// real payee. The previous client-only query string was tamperable and had no
// receiving endpoint, so keep this deep-link-safe placeholder until that backend
// contract exists and do not surface it in navigation.
const BizPayment = () => {
  const { c } = useTheme();
  return (
    <Screen>
      <Header title="Receive Payment" onBack={() => router.back()} right={<HeaderLink label="History" onPress={() => router.push('/history')} />} />
      <Card style={{ alignItems: 'center', paddingVertical: 28 }}>
        <View style={{ width: 56, height: 56, borderRadius: 18, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="shield" size={28} color={c.brand} />
        </View>
        <Text style={{ fontSize: 17, fontFamily: font.bold, color: c.ink1, marginTop: 14 }}>Secure payment links are coming soon</Text>
        <Text style={{ fontSize: 13, color: c.ink3, marginTop: 8, textAlign: 'center', lineHeight: 20, fontFamily: font.regular }}>
          We’ll enable this after every request can be signed, tied to your account and safely expire.
        </Text>
      </Card>
      <View style={{ height: 18 }} />
      <Btn label="Go back" variant="outline" onPress={() => router.back()} />
    </Screen>
  );
};

export default BizPayment;
