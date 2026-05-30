import React from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

const Completed = () => {
  const { c } = useTheme();
  return (
    <Screen scroll={false}>
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 12 }}>
        <View style={{ width: 110, height: 110, borderRadius: 55, backgroundColor: 'rgba(0,181,29,.14)', alignItems: 'center', justifyContent: 'center' }}>
          <View style={{ width: 78, height: 78, borderRadius: 39, backgroundColor: c.lime, alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="check" size={40} color="#fff" stroke={3} />
          </View>
        </View>
        <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, marginTop: 24 }}>Setup Complete</Text>
        <Text style={{ fontSize: 14, color: c.ink3, marginTop: 10, textAlign: 'center', maxWidth: 280, fontFamily: font.regular }}>
          Congratulations! Your account setup has been successfully completed.
        </Text>
      </View>
      <View style={{ paddingBottom: 24 }}>
        <Btn label="Proceed Home" onPress={() => router.replace('/home')} />
      </View>
    </Screen>
  );
};

export default Completed;
