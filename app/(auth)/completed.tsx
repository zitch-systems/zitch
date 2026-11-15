import React, { useEffect, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import { registerForPushNotifications } from '@/lib/notifications';
import AuthGuard from '@/components/AuthGuard';
import { getSessionGeneration } from '@/lib/secureStore';

const Completed = () => {
  const { c } = useTheme();
  const [firstName, setFirstName] = useState('');

  useEffect(() => {
    let active = true;
    const generation = getSessionGeneration();
    AsyncStorage.getItem('UserFirstName').then((n) => {
      if (active && generation === getSessionGeneration() && n) setFirstName(n);
    }).catch(() => {});
    // Ask only after account creation, when the benefit is concrete and an
    // authenticated token exists to bind this device's push token server-side.
    registerForPushNotifications(true).catch(() => {});
    return () => { active = false; };
  }, []);

  return (
    <Screen>
      <View style={{ alignItems: 'center', paddingHorizontal: 12, paddingVertical: 40 }}>
        <View style={{ width: 110, height: 110, borderRadius: 55, backgroundColor: 'rgba(0,181,29,.14)', alignItems: 'center', justifyContent: 'center' }}>
          <View style={{ width: 78, height: 78, borderRadius: 39, backgroundColor: c.lime, alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="check" size={40} color="#fff" stroke={3} />
          </View>
        </View>
        <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, marginTop: 24, textAlign: 'center' }}>
          {firstName ? `Welcome to Zitch, ${firstName}!` : 'Welcome to Zitch!'}
        </Text>
        <Text style={{ fontSize: 14, color: c.ink3, marginTop: 10, textAlign: 'center', maxWidth: 300, lineHeight: 21, fontFamily: font.regular }}>
          {/* The same words whether or not the BVN step was finished: no status
              call, so nothing changes under the customer as it loads. Account
              limits says what to verify next. */}
          Your sign-up is complete. Your account is ready to use, and you can raise your limits at any time.
        </Text>
      </View>
      <View style={{ paddingBottom: 24, gap: 12 }}>
        <Btn label="Go to home" onPress={() => router.replace('/home')} />
        <Btn label="Raise my limits" variant="outline" onPress={() => router.push('/accountlimits')} />
      </View>
    </Screen>
  );
};

export default function GuardedCompleted() { return <AuthGuard><Completed /></AuthGuard>; }
