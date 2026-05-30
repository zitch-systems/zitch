import React from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { Screen, Card, Btn } from '@/components/design/ui';
import { Hero, SectionLabel } from '@/components/design/widgets';
import { useTheme, font } from '@/lib/theme';

const Loans = () => {
  const { c } = useTheme();
  return (
    <Screen pad={false} tab>
      <Text style={{ paddingHorizontal: 20, paddingTop: 6, fontSize: 26, fontFamily: font.extrabold, color: c.ink1 }}>Loans</Text>

      <Hero style={{ margin: 16 }}>
        <Text style={{ fontSize: 13, color: 'rgba(255,255,255,.85)', fontFamily: font.regular }}>Available credit</Text>
        <Text style={{ fontSize: 32, fontFamily: font.extrabold, color: '#fff', marginTop: 4, fontVariant: ['tabular-nums'] }}>₦500,000</Text>
        <View style={{ height: 6, borderRadius: 4, backgroundColor: 'rgba(255,255,255,.25)', marginTop: 14, overflow: 'hidden' }}>
          <View style={{ width: '64%', height: '100%', backgroundColor: '#fff' }} />
        </View>
        <Text style={{ fontSize: 12, color: 'rgba(255,255,255,.85)', marginTop: 8, fontFamily: font.regular }}>₦320,000 of ₦500,000 limit used</Text>
      </Hero>

      <View style={{ marginHorizontal: 16 }}>
        <Card>
          <Btn label="Get a new loan" icon="loan" onPress={() => router.push('/getloan')} />
        </Card>
      </View>

      <View style={{ paddingHorizontal: 18, paddingTop: 22 }}>
        <SectionLabel>Active loans</SectionLabel>
        <View style={{ borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, padding: 16 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
            <View>
              <Text style={{ fontFamily: font.bold, color: c.ink1 }}>Quick loan</Text>
              <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>Due Jun 26 · 30 days</Text>
            </View>
            <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1, fontVariant: ['tabular-nums'] }}>₦156,750</Text>
          </View>
          <View style={{ marginTop: 14 }}>
            <Btn label="Repay now" onPress={() => router.push('/comingsoon')} />
          </View>
        </View>
      </View>
    </Screen>
  );
};

export default Loans;
