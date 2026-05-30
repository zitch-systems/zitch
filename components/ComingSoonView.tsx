import React from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

/**
 * Shared placeholder for features that are not built yet. Themed to match the
 * revamp so the app never drops the user onto a blank/raw screen.
 */
const ComingSoonView = ({ title = 'Coming Soon', icon = 'spark', note = "We're working hard to bring you something amazing. Stay tuned!" }: { title?: string; icon?: string; note?: string }) => {
  const { c } = useTheme();
  return (
    <Screen scroll={false}>
      <Header title={title} onBack={() => router.back()} />
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 24, paddingBottom: 80 }}>
        <View style={{ width: 88, height: 88, borderRadius: 28, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name={icon} size={40} color={c.brand} />
        </View>
        <Text style={{ fontSize: 20, fontFamily: font.extrabold, color: c.ink1, marginTop: 22 }}>{title}</Text>
        <Text style={{ fontSize: 14, color: c.ink3, marginTop: 8, textAlign: 'center', maxWidth: 280, fontFamily: font.regular }}>{note}</Text>
        <View style={{ marginTop: 16, paddingVertical: 7, paddingHorizontal: 16, borderRadius: 999, backgroundColor: c.surface3 }}>
          <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.ink2 }}>Fully designed in handoff →</Text>
        </View>
      </View>
    </Screen>
  );
};

export default ComingSoonView;
