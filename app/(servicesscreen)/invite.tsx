import React, { useState } from 'react';
import { View, Text, Pressable, Share, Alert } from 'react-native';
import { router } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { Screen, Header, Card, Btn } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

// A stable, shareable referral code derived from the user's name. (When a
// backend referral endpoint exists, fetch the canonical code instead.)
const codeFor = (name: string) => {
  const base = (name || 'FRIEND').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6) || 'FRIEND';
  return `ZITCH-${base}`;
};

const Invite = () => {
  const { c } = useTheme();
  const { firstName } = useWallet();
  const [copied, setCopied] = useState(false);

  const code = codeFor(firstName);
  const message =
    `Join me on Zitch — buy airtime, data, pay bills and convert airtime to cash. ` +
    `Use my invite code ${code} when you sign up. Download: https://zitch.example/app`;

  const copy = async () => {
    await Clipboard.setStringAsync(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  const share = async () => {
    try {
      await Share.share({ message });
    } catch {
      Alert.alert('Error', 'Could not open the share sheet.');
    }
  };

  const steps = [
    { icon: 'share', title: 'Share your code', sub: 'Send your invite to friends' },
    { icon: 'user', title: 'They sign up', sub: 'Using your code at registration' },
    { icon: 'gift', title: 'You both earn', sub: 'Get rewarded on their first transaction' },
  ];

  return (
    <Screen pad={false}>
      <View style={{ paddingHorizontal: 20 }}>
        <Header title="Invite & Earn" sub="Earn up to ₦5,600" onBack={() => router.back()} />
      </View>

      <View style={{ paddingHorizontal: 16 }}>
        <Card style={{ alignItems: 'center', paddingVertical: 22 }}>
          <View style={{ width: 56, height: 56, borderRadius: 18, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="gift" size={28} color={c.brand} />
          </View>
          <Text style={{ fontSize: 13, color: c.ink3, marginTop: 14, fontFamily: font.regular }}>Your invite code</Text>
          <Text style={{ fontSize: 26, fontFamily: font.extrabold, color: c.ink1, marginTop: 4, letterSpacing: 1 }}>{code}</Text>
          <Pressable onPress={copy} style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 12, paddingVertical: 8, paddingHorizontal: 16, borderRadius: 999, backgroundColor: c.surface3 }}>
            <ZIcon name={copied ? 'check' : 'copy'} size={15} color={c.brand} />
            <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.brand }}>{copied ? 'Copied' : 'Copy code'}</Text>
          </Pressable>
        </Card>

        <View style={{ marginTop: 18 }}>
          {steps.map((s, i) => (
            <View key={s.title} style={{ flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 10 }}>
              <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, alignItems: 'center', justifyContent: 'center' }}>
                <ZIcon name={s.icon} size={19} color={c.brand} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={{ fontFamily: font.semibold, color: c.ink1 }}>{i + 1}. {s.title}</Text>
                <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 1, fontFamily: font.regular }}>{s.sub}</Text>
              </View>
            </View>
          ))}
        </View>

        <View style={{ marginTop: 16 }}>
          <Btn label="Share invite" icon="share" onPress={share} />
        </View>
      </View>
    </Screen>
  );
};

export default Invite;
