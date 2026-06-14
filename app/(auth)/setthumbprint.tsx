import React, { useEffect, useState } from 'react';
import { View, Text, Alert } from 'react-native';
import { router } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { Screen, Header, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import { isBiometricAvailable, biometricLabel, authenticate, setBiometricEnabled } from '@/lib/biometrics';

const SetThumbprint = () => {
  const { c } = useTheme();
  const [available, setAvailable] = useState<boolean | null>(null);
  const [kind, setKind] = useState<'face' | 'fingerprint' | 'biometrics'>('biometrics');

  useEffect(() => {
    isBiometricAvailable().then(setAvailable);
    biometricLabel().then(setKind);
  }, []);

  const label = kind === 'face' ? 'Face ID' : kind === 'fingerprint' ? 'Fingerprint' : 'Biometrics';

  const enable = async () => {
    if (!available) {
      notify('Unavailable', 'Set up Face ID or a fingerprint in your device settings first.');
      return;
    }
    const ok = await authenticate(`Enable ${label}`);
    if (ok) {
      await setBiometricEnabled(true);
      Alert.alert('Enabled', `${label} sign-in is now on.`, [{ text: 'Done', onPress: () => router.back() }]);
    }
  };

  return (
    <Screen scroll={false}>
      <Header onBack={() => router.back()} />
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32 }}>
        <View style={{ width: 110, height: 110, borderRadius: 55, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name={kind === 'face' ? 'faceid' : 'fingerprint'} size={56} color={c.brand} stroke={1.6} />
        </View>
        <Text style={{ fontSize: 23, fontFamily: font.extrabold, color: c.ink1, marginTop: 30, textAlign: 'center' }}>
          Enable {label}
        </Text>
        <Text style={{ fontSize: 14.5, color: c.ink3, marginTop: 10, lineHeight: 22, textAlign: 'center', fontFamily: font.regular }}>
          {available === false
            ? 'No biometrics are set up on this device yet. Add them in your device settings to use this feature.'
            : 'Sign in and approve payments faster and more securely with biometrics.'}
        </Text>
      </View>
      <View style={{ paddingBottom: 24 }}>
        <Btn label={`Enable ${label}`} icon="check" onPress={enable} disabled={available === false} />
        <View style={{ alignItems: 'center', marginTop: 14 }}>
          <Text onPress={() => router.back()} style={{ fontSize: 14, fontFamily: font.semibold, color: c.ink3 }}>Maybe later</Text>
        </View>
      </View>
    </Screen>
  );
};

export default SetThumbprint;
