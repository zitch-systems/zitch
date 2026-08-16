import React, { useEffect, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import { registerForPushNotifications } from '@/lib/notifications';

const Completed = () => {
  const { c } = useTheme();
  const [firstName, setFirstName] = useState('');

  useEffect(() => {
    AsyncStorage.getItem('UserFirstName').then((n) => n && setFirstName(n));
    // Ask only after account creation, when the benefit is concrete and an
    // authenticated token exists to bind this device's push token server-side.
    registerForPushNotifications(true).catch(() => {});
  }, []);

  return (
    <Screen scroll={false}>
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 12 }}>
        <View style={{ width: 110, height: 110, borderRadius: 55, backgroundColor: 'rgba(0,181,29,.14)', alignItems: 'center', justifyContent: 'center' }}>
          <View style={{ width: 78, height: 78, borderRadius: 39, backgroundColor: c.lime, alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="check" size={40} color="#fff" stroke={3} />
          </View>
        </View>
        <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, marginTop: 24, textAlign: 'center' }}>
          {firstName ? `You’re all set, ${firstName}!` : 'You’re all set!'}
        </Text>
        <Text style={{ fontSize: 14, color: c.ink3, marginTop: 10, textAlign: 'center', maxWidth: 300, lineHeight: 21, fontFamily: font.regular }}>
          Your Zitch account is ready. Add money to start paying bills, sending cash and buying airtime, data & more.
        </Text>
      </View>
      <View style={{ paddingBottom: 24, gap: 12 }}>
        <Btn label="Add money" icon="bank" onPress={() => router.replace('/addmoney')} />
        <Btn label="Go to home" variant="outline" onPress={() => router.replace('/home')} />
      </View>
    </Screen>
  );
};

export default Completed;
