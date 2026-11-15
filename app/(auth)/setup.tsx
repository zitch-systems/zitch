import React from 'react';
import { View, Text, Pressable } from 'react-native';
import { router } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { ZMark } from '@/components/design/Brand';
import { Screen, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import AuthGuard from '@/components/AuthGuard';

const Tag = ({ text, strong }: { text: string; strong?: boolean }) => {
  const { c } = useTheme();
  return (
    <View style={{ paddingHorizontal: 9, paddingVertical: 4, borderRadius: 999, backgroundColor: strong ? 'rgba(6,110,102,0.12)' : c.surface2 }}>
      <Text style={{ fontSize: 11.5, fontFamily: font.bold, color: strong ? c.brand : c.ink3 }}>{text}</Text>
    </View>
  );
};

const SetupRow = ({ icon, title, sub, to, tag }: { icon: string; title: string; sub: string; to: string; tag?: React.ReactNode }) => {
  const { c } = useTheme();
  return (
    <Pressable
      onPress={() => router.push(to as any)}
      accessibilityRole="button"
      accessibilityLabel={`${title}, ${sub}`}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, borderRadius: 18, padding: 16, marginTop: 12 }}
    >
      <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name={icon} size={22} color={c.brand} />
      </View>
      <View style={{ flex: 1 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ fontFamily: font.bold, color: c.ink1, fontSize: 15 }}>{title}</Text>
          {tag}
        </View>
        <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{sub}</Text>
      </View>
      <ZIcon name="right" size={20} color={c.ink3} />
    </Pressable>
  );
};

const Setup = () => {
  const { c } = useTheme();

  // The password is set with the phone number and the BVN comes next, so the
  // only step left here is the PIN that approves payments.
  return (
    <Screen>
      <View style={{ flex: 1, alignItems: 'stretch' }}>
        <View style={{ alignItems: 'center', marginTop: 30 }}>
          <ZMark size={48} />
        </View>
        <View style={{ width: 76, height: 76, borderRadius: 38, backgroundColor: 'rgba(0,160,26,0.12)', alignItems: 'center', justifyContent: 'center', alignSelf: 'center', marginTop: 26 }}>
          <View style={{ width: 48, height: 48, borderRadius: 24, backgroundColor: '#00A01A', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="check" size={24} color="#fff" stroke={3} />
          </View>
        </View>
        <Text style={{ fontSize: 22, fontFamily: font.extrabold, color: c.ink1, textAlign: 'center', marginTop: 18 }}>Account created 🎉</Text>
        <Text style={{ fontSize: 14, color: c.ink3, textAlign: 'center', marginTop: 8, marginBottom: 22, fontFamily: font.regular }}>
          One last step: create the PIN you&apos;ll use to approve payments.
        </Text>

        <SetupRow icon="lock" title="Transaction PIN" sub="Authorize payments securely" to="/setpin" tag={<Tag text="Next" strong />} />
        <SetupRow icon="fingerprint" title="Biometrics" sub="Enable Face ID or fingerprint sign-in" to="/setthumbprint" tag={<Tag text="Optional" />} />
        <View style={{ flex: 1, minHeight: 24 }} />
        <View style={{ marginBottom: 12 }}>
          <Btn label="Create my PIN" onPress={() => router.push('/setpin')} />
        </View>
      </View>
    </Screen>
  );
};

export default function GuardedSetup() { return <AuthGuard><Setup /></AuthGuard>; }
