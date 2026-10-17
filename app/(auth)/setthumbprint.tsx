import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Platform } from 'react-native';
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
import { apiPost } from '@/lib/api';
import { getSessionGeneration, saveTransactionPin } from '@/lib/secureStore';
import { TRANSACTION_PIN_LENGTH } from '@/lib/transactionPin';

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
  const [busy, setBusy] = useState(false);
  const [pinError, setPinError] = useState('');
  const enablingPay = useRef(false);
  const toggling = useRef(false);
  const mounted = useRef(true);
  const screenGeneration = useRef(getSessionGeneration()).current;
  const [toggleBusy, setToggleBusy] = useState(false);
  const paymentSupported = Platform.OS !== 'web';

  useEffect(() => {
    mounted.current = true;
    const generation = screenGeneration;
    const current = () => mounted.current && generation === getSessionGeneration();
    isBiometricAvailable().then(v => { if (current()) setAvailable(v); }).catch(() => { if (current()) setAvailable(false); });
    biometricLabel().then(v => { if (current()) setKind(v); }).catch(() => {});
    isBiometricEnabled().then(v => { if (current()) setSignIn(v); }).catch(() => {});
    if (paymentSupported) isBiometricTxnEnabled().then(v => { if (current()) setPay(v); }).catch(() => {});
    return () => { mounted.current = false; };
  }, [paymentSupported, screenGeneration]);

  const label = kind === 'face' ? 'Face ID' : kind === 'fingerprint' ? 'Fingerprint' : 'Biometrics';

  const guardAvailable = async (): Promise<boolean> => {
    if (await isBiometricAvailable()) return true;
    notify('Unavailable', `Set up ${label} in your device settings first.`);
    return false;
  };

  // Sign-in toggle — a scan turns it on; off just clears the opt-in.
  const toggleSignIn = async (v: boolean) => {
    if (toggling.current || enablingPay.current) return;
    toggling.current = true;
    setToggleBusy(true);
    const generation = screenGeneration;
    const current = () => mounted.current && generation === getSessionGeneration();
    try {
      if (!current()) return;
      if (v && (!(await guardAvailable()) || !current())) return;
      if (v && (!(await authenticate(`Enable ${label} sign-in`)) || !current())) return;
      if (!current()) return;
      await setBiometricEnabled(v);
      if (!current()) return;
      setSignIn(v);
      if (v) notify('All set', `${label} sign-in is on.`);
    } catch {
      if (current()) notify('Could not update biometrics', 'Please try again.');
    } finally {
      toggling.current = false;
      if (mounted.current) setToggleBusy(false);
    }
  };

  // Payment-approval toggle — capture the PIN, then SecureStore opens the one OS
  // authentication required to place it behind the keychain biometric ACL.
  const togglePay = async (v: boolean) => {
    if (!paymentSupported || toggling.current || enablingPay.current) return;
    toggling.current = true;
    setToggleBusy(true);
    const generation = screenGeneration;
    const current = () => mounted.current && generation === getSessionGeneration();
    try {
      if (!current()) return;
      if (!v) {
        await setBiometricTxnEnabled(false);
        if (current()) setPay(false);
        return;
      }
      if (!(await guardAvailable()) || !current()) return;
      setPinError('');
      setPinOpen(true);
    } catch {
      if (current()) notify('Could not update biometrics', 'Please try again.');
    } finally {
      toggling.current = false;
      if (mounted.current) setToggleBusy(false);
    }
  };

  const enablePay = async (pin: string) => {
    if (!paymentSupported || enablingPay.current || toggling.current) return;
    enablingPay.current = true;
    setBusy(true);
    setPinError('');
    const generation = screenGeneration;
    const current = () => mounted.current && generation === getSessionGeneration();
    try {
      if (!current()) return;
      // Never cache a typo: replaying a wrong PIN at each future payment can lock
      // the account across both the mobile app and WhatsApp.
      const response = await apiPost('/api/verify-transaction-pin/', { pin });
      const result = await response.json().catch(() => ({}));
      if (!current()) return;
      if (!response.ok || !result.success) {
        setPinError(result.message || 'Could not verify your transaction PIN.');
        return;
      }
      await saveTransactionPin(pin);
      if (!current()) return;
      await setBiometricTxnEnabled(true);
      if (!current()) return;
      setPinOpen(false);
      setPay(true);
      notify('All set', `${label} payments are on.`);
    } catch {
      if (current()) setPinError('Biometric payments were not enabled. Please try again when you are ready.');
    } finally {
      enablingPay.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <Screen>
      <Header onBack={() => router.back()} />

      <View style={{ alignItems: 'center', paddingHorizontal: 24, marginBottom: 26 }}>
        <View style={{ width: 104, height: 104, borderRadius: 52, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name={kind === 'face' ? 'faceid' : 'fingerprint'} size={52} color={c.brand} stroke={1.6} />
        </View>
        <Text style={{ fontSize: 23, fontFamily: font.extrabold, color: c.ink1, marginTop: 24, textAlign: 'center' }}>
          {label} settings
        </Text>
        <Text style={{ fontSize: 14.5, color: c.ink3, marginTop: 10, lineHeight: 22, textAlign: 'center', fontFamily: font.regular }}>
          {available === false
            ? `No ${label} is set up on this device yet. Add it in your device settings to use this feature.`
            : paymentSupported ? `Use ${label} to unlock the app or approve payments on this device.` : `Use ${label} to unlock the app on this device. Enter your transaction PIN to approve payments in a browser.`}
        </Text>
      </View>

      <Card pad={0} style={{ paddingHorizontal: 16, opacity: available === false ? 0.5 : 1 }}>
        <ZItem
          icon="fingerprint"
          title={`${label} sign-in`}
          sub={`Unlock the app with ${label}`}
          right={<Toggle accessibilityLabel={`${label} sign-in`} on={signIn} onChange={toggleSignIn} disabled={available !== true || toggleBusy || busy} />}
        />
        <ZItem
          icon="faceid"
          title="Approve payments"
          sub={paymentSupported ? `Confirm transfers & bills with ${label} instead of your PIN` : 'Available in the mobile app. Use your PIN in this browser.'}
          last
          right={<Toggle accessibilityLabel="Approve payments with biometrics" on={pay} onChange={togglePay} disabled={!paymentSupported || available !== true || toggleBusy || busy} />}
        />
      </Card>

      <View style={{ height: 20 }} />
      <Btn label="Done" variant="outline" onPress={() => router.back()} />

      <PinSheet
        open={pinOpen}
        onClose={() => { if (!enablingPay.current) setPinOpen(false); }}
        busy={busy}
        error={pinError}
        onComplete={enablePay}
        title={`Approve payments with ${label}?`}
        subtitle={`Enter your ${TRANSACTION_PIN_LENGTH}-digit PIN to approve payments with ${label} too. You can skip and just use it to sign in.`}
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
  <AuthGuard fresh>
    <SetThumbprint />
  </AuthGuard>
);

export default GuardedSetThumbprint;
