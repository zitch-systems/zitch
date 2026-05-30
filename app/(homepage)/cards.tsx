import React, { useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Image } from 'react-native';
import { router } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Card } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

const Cards = () => {
  const { c } = useTheme();
  const [freeze, setFreeze] = useState(false);

  return (
    <Screen pad={false} tab>
      <Text style={{ paddingHorizontal: 20, paddingTop: 6, fontSize: 26, fontFamily: font.extrabold, color: c.ink1 }}>Cards</Text>

      {/* card visual */}
      <LinearGradient
        colors={freeze ? ['#1B463C', '#0B2A24'] : ['#0C5249', '#0FA295', '#5CF5EB']}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={{ margin: 16, borderRadius: 22, padding: 20, height: 200, overflow: 'hidden', shadowColor: '#000', shadowOpacity: 0.5, shadowRadius: 24, shadowOffset: { width: 0, height: 18 }, elevation: 8 }}
      >
        <Image source={require('@/assets/images/zitch-mark.png')} style={{ position: 'absolute', right: -20, bottom: -30, width: 160, height: 160, opacity: 0.22 }} resizeMode="contain" />
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <Text style={{ color: 'rgba(255,255,255,.9)', fontSize: 13, fontFamily: font.bold, letterSpacing: 1.3 }}>ZITCH</Text>
          <ZIcon name="wallet" size={22} color="#fff" />
        </View>
        <Text style={{ color: '#fff', fontSize: 21, letterSpacing: 3, marginTop: 46, fontVariant: ['tabular-nums'] }}>5061 •••• •••• 2043</Text>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 16 }}>
          <Text style={{ color: 'rgba(255,255,255,.9)', fontSize: 13, fontFamily: font.semibold }}>WILLIAM A.</Text>
          <Text style={{ color: 'rgba(255,255,255,.9)', fontSize: 13, fontVariant: ['tabular-nums'] }}>08/27</Text>
        </View>
        {freeze && (
          <View style={{ position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(5,32,28,.4)' }}>
            <Text style={{ color: '#fff', fontFamily: font.bold, fontSize: 14, letterSpacing: 1.4 }}>❄ FROZEN</Text>
          </View>
        )}
      </LinearGradient>

      {/* actions */}
      <View style={{ flexDirection: 'row', gap: 10, marginHorizontal: 16 }}>
        {[
          { icon: 'lock', label: freeze ? 'Unfreeze' : 'Freeze', go: () => setFreeze((f) => !f) },
          { icon: 'eye', label: 'Details', go: () => router.push('/comingsoon') },
          { icon: 'settings', label: 'Settings', go: () => router.push('/comingsoon') },
        ].map((a) => (
          <Pressable key={a.label} onPress={a.go} style={{ flex: 1 }}>
            <View style={{ alignItems: 'center', gap: 8, paddingVertical: 14, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line }}>
              <ZIcon name={a.icon} size={20} color={c.brand} />
              <Text style={{ fontSize: 12, fontFamily: font.semibold, color: c.ink2 }}>{a.label}</Text>
            </View>
          </Pressable>
        ))}
      </View>

      <View style={{ marginHorizontal: 16, marginTop: 16 }}>
        <Card onPress={() => router.push('/comingsoon')} style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="plus" size={22} color={c.brand} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ fontFamily: font.bold, color: c.ink1 }}>Create a virtual card</Text>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>For online & USD payments</Text>
          </View>
          <ZIcon name="right" size={20} color={c.ink3} />
        </Card>
      </View>
    </Screen>
  );
};

export default Cards;
