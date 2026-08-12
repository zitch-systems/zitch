import React, { useEffect, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { Screen, Header, Btn, Card, ZItem, PinSheet, Toggle } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import AuthGuard from '@/components/AuthGuard';
import {
  isBiometricAvailable, biometricLabel, authenticate,
  isBiometricEnabled, setBiometricEnabled,
  isBiometricTxnEnabled, setBiometricTxnEnabled,
} from '@/lib/biometrics';
import { saveTransactionPin } from '@/lib/secureStore';

// Biometrics settings: turn Face ID / fingerprint SIGN-IN and PAYMENT approval on
// or off with live toggles. The label and icon follow what the device actually
// supports (Face ID vs fingerprint). Reached from onboarding and from
// Security → Biometrics.
const SetThumbprint = () => {
  const { c } = useTheme();
  const [available, setAvailable] = useState<boolean | null>(null);
  const [kind, setKind] = useState<'face' | 'fingerprint' | 'biometrics'>('biometrics');
  const [signIn, setSignIn] = useState(false);       // biometric sign-in on/off
  const [pay, setPay] = useState(false);             // approve payments with biometrics on/off
  const [pinOpen, setPinOpen] = useState(false);

  useEffect(() => {
    isBiometricAvailable().then(setAvailable);
    biometricLabel().then(setKind);
    isBiometricEnabled().then(setSignIn);
    isBiometricTxnEnabled().then(setPay);
  }, []);

  const label = kind === 'face' ? 'Face ID' : kind === 'fingerprint' ? 'Fingerprint' : 'Biometrics';

  const guardAvailable = async (): Promise<boolean> => {
    if (await isBiometricAvailable()) return true;
    notify('Unavailable', `Set up ${label} in your device settings first.`);
    return false;
  };

  // Sign-in toggle — a scan turns it on; off just clears the opt-in.
  const toggleSignIn = async (v: boolean) => {
    if (!v) { await setBiometricEnabled(false); setSignIn(false); return; }
    if (!(await guardAvailable())) return;
    if (await authenticate(`Enable ${label} sign-in`)) {
      await setBiometricEnabled(true);
      setSignIn(true);
      notify('All set', `${label} sign-in is on.`);
    }
  };

  // Payment-approval toggle — a scan then the PIN (captured in the sheet) caches
  // the money PIN; off drops it so payments fall back to the keypad.
  const togglePay = async (v: boolean) => {
    if (!v) { await setBiometricTxnEnabled(false); setPay(false); return; }
    if (!(await guardAvailable())) return;
    // biometricOnly: the device passcode must not stand in for the owner's scan
    // when arming payment approval.
    if (await authenticate(`Approve payments with ${label}`, true)) setPinOpen(true);
  };

  const enablePay = async (pin: string) => {
    setPinOpen(false);
    await saveTransactionPin(pin);
    await setBiometricTxnEnabled(true);
    setPay(true);
    notify('All set', `${label} payments are on.`);
  };

  return (
    <Screen>
      <Header onBack={() => router.back()} />

      <View style={{ alignItems: 'center', paddingHorizontal: 24, marginBottom: 26 }}>
        <View style={{ width: 104, height: 104, borderRadius: 52, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name={kind === 'face' ? 'faceid' : 'fingerprint'} size={52} color={c.brand} stroke={1.6} />
        </View>
        <Text style={{ fontSize: 23, fontFamily: font.extrabold, color: c.ink1, marginTop: 24, textAlign: 'center' }}>
          Enable {label}
        </Text>
        <Text style={{ fontSize: 14.5, color: c.ink3, marginTop: 10, lineHeight: 22, textAlign: 'center', fontFamily: font.regular }}>
          {available === false
            ? `No ${label} is set up on this device yet. Add it in your device settings to use this feature.`
            : `Sign in and approve payments faster and more securely with ${label}.`}
        </Text>
      </View>

      <Card pad={0} style={{ paddingHorizontal: 16, opacity: available === false ? 0.5 : 1 }}>
        <ZItem
          icon="fingerprint"
          title={`${label} sign-in`}
          sub={`Unlock the app with ${label}`}
          right={<Toggle on={signIn} onChange={toggleSignIn} disabled={available === false} />}
        />
        <ZItem
          icon="faceid"
          title="Approve payments"
          sub={`Confirm transfers & bills with ${label} instead of your PIN`}
          last
          right={<Toggle on={pay} onChange={togglePay} disabled={available === false} />}
        />
      </Card>

      <View style={{ height: 20 }} />
      <Btn label="Done" variant="outline" onPress={() => router.back()} />

      <PinSheet
        open={pinOpen}
        onClose={() => setPinOpen(false)}
        onComplete={enablePay}
        title={`Approve payments with ${label}?`}
        subtitle={`Enter your 6-digit PIN to approve payments with ${label} too. You can skip and just use it to sign in.`}
      />
    </Screen>
  );
};

// Enabling biometric sign-in is a security setting, and this screen is reachable
// by deep link (`zitch://setthumbprint`) because the (auth) group is unguarded.
// Without this, anyone holding an OS-unlocked device with a locked-but-live Zitch
// session could turn biometric sign-in on with one local scan and thereafter
// enter the account without a password. Same wrapper the other post-login
// screens in this group already carry (resetpin, securitysetup, kyc,
// accountdetails).
const GuardedSetThumbprint = () => (
  <AuthGuard>
    <SetThumbprint />
  </AuthGuard>
);

export default GuardedSetThumbprint;
