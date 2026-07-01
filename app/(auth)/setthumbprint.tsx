import React, { useEffect, useState } from 'react';
import { View, Text, Alert } from 'react-native';
import { router } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { Screen, Header, Btn, PinSheet } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import { isBiometricAvailable, biometricLabel, authenticate, setBiometricEnabled, setBiometricTxnEnabled } from '@/lib/biometrics';
import { saveTransactionPin } from '@/lib/secureStore';

const SetThumbprint = () => {
  const { c } = useTheme();
  const [available, setAvailable] = useState<boolean | null>(null);
  const [kind, setKind] = useState<'face' | 'fingerprint' | 'biometrics'>('biometrics');
  const [pinOpen, setPinOpen] = useState(false);

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
    if (!ok) return;
    // Biometric sign-in is on immediately. Then offer "pay with biometrics",
    // which is the only thing that caches the money PIN — and only if the user
    // confirms it here. Skipping leaves sign-in on with no PIN stored.
    await setBiometricEnabled(true);
    setPinOpen(true);
  };

  const enablePay = async (pin: string) => {
    setPinOpen(false);
    await saveTransactionPin(pin);
    await setBiometricTxnEnabled(true);
    Alert.alert('All set', `${label} sign-in and payments are on.`, [{ text: 'Done', onPress: () => router.back() }]);
  };

  const skipPay = () => {
    setPinOpen(false);
    Alert.alert('Enabled', `${label} sign-in is now on.`, [{ text: 'Done', onPress: () => router.back() }]);
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

      <PinSheet
        open={pinOpen}
        onClose={skipPay}
        onComplete={enablePay}
        title="Pay with biometrics?"
        subtitle={`Enter your 4-digit PIN to approve payments with ${label} too. You can skip and just use it to sign in.`}
      />
    </Screen>
  );
};

export default SetThumbprint;
