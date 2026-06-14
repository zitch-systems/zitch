import React from 'react';
import { View, Text, Linking, Platform } from 'react-native';
import { router } from 'expo-router';
import { Screen, Header, Card } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

const BASE = '*000#'; // TODO: replace with the real Zitch USSD shortcode.

const CODES = [
  { label: 'Open Zitch USSD menu', code: BASE },
  { label: 'Check wallet balance', code: '*000*1#' },
  { label: 'Buy airtime', code: '*000*2#' },
  { label: 'Buy data', code: '*000*3#' },
  { label: 'Send money', code: '*000*4#' },
  { label: 'Pay a bill', code: '*000*5#' },
];

const dial = (code: string) => {
  // USSD strings contain * and #; # must be encoded for the tel: URI.
  const uri = `tel:${code.replace(/#/g, Platform.OS === 'android' ? encodeURIComponent('#') : '%23')}`;
  Linking.openURL(uri).catch(() =>
    notify('Can’t dial here', `Dial ${code} from your phone's keypad to use Zitch USSD.`)
  );
};

const Ussd = () => {
  const { c } = useTheme();
  return (
    <Screen pad={false}>
      <View style={{ paddingHorizontal: 20 }}>
        <Header title="Zitch USSD" sub="Bank without internet" onBack={() => router.back()} />
      </View>

      <View style={{ paddingHorizontal: 16 }}>
        <Card style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
          <View style={{ width: 48, height: 48, borderRadius: 15, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="phone" size={24} color={c.brand} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ fontFamily: font.bold, color: c.ink1, fontSize: 15 }}>Dial {BASE} on any phone</Text>
            <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>Works on every network, no data needed.</Text>
          </View>
        </Card>

        <Card style={{ marginTop: 14 }} pad={0}>
          <View style={{ paddingHorizontal: 16 }}>
            {CODES.map((it, i) => (
              <View key={it.code} style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 14, borderBottomWidth: i === CODES.length - 1 ? 0 : 1, borderBottomColor: c.line }}>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontFamily: font.semibold, color: c.ink1 }}>{it.label}</Text>
                  <Text style={{ fontSize: 13, color: c.brand, marginTop: 2, fontFamily: font.bold, letterSpacing: 1 }}>{it.code}</Text>
                </View>
                <Text onPress={() => dial(it.code)} style={{ color: c.brand, fontFamily: font.bold, fontSize: 13, paddingVertical: 6, paddingHorizontal: 14, borderRadius: 999, backgroundColor: c.surface3, overflow: 'hidden' }}>
                  Dial
                </Text>
              </View>
            ))}
          </View>
        </Card>

        <Text style={{ fontSize: 12, color: c.ink3, textAlign: 'center', marginTop: 16, paddingHorizontal: 16, fontFamily: font.regular }}>
          Standard carrier USSD charges may apply. Never share your PIN over USSD prompts you didn't start.
        </Text>
      </View>
    </Screen>
  );
};

export default Ussd;
