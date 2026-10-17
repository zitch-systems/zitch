import React, { useEffect, useRef, useState } from 'react';
import { router } from 'expo-router';
import { Screen, Header, ZItem, Toggle } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { isBiometricAvailable, isBiometricEnabled, setBiometricEnabled, authenticate } from '@/lib/biometrics';
import AuthGuard from '@/components/AuthGuard';
import { getSessionGeneration } from '@/lib/secureStore';

const SecuritySetup = () => {
  const [bio, setBio] = useState(false);
  const [busy, setBusy] = useState(true);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const screenGeneration = useRef(getSessionGeneration()).current;

  useEffect(() => {
    mounted.current = true;
    const generation = screenGeneration;
    isBiometricEnabled().then(value => {
      if (mounted.current && generation === getSessionGeneration()) setBio(value);
    }).catch(() => {}).finally(() => { if (mounted.current) setBusy(false); });
    return () => { mounted.current = false; };
  }, [screenGeneration]);

  // Inline sign-in toggle on the Biometrics row. Tapping the toggle flips it
  // here; tapping the rest of the row opens the full biometrics page (where
  // payment approval also lives). The nested toggle Pressable captures its own
  // taps, so the two don't collide.
  const toggleBio = async (v: boolean) => {
    if (inFlight.current || busy) return;
    inFlight.current = true;
    setBusy(true);
    const generation = screenGeneration;
    const current = () => mounted.current && generation === getSessionGeneration();
    try {
      if (!current()) return;
      if (v) {
        const available = await isBiometricAvailable();
        if (!current()) return;
        if (!available) {
          notify('Unavailable', 'Set up Face ID or a fingerprint in your device settings first.');
          return;
        }
        if (!(await authenticate('Enable biometric sign-in')) || !current()) return;
      }
      await setBiometricEnabled(v);
      if (current()) setBio(v);
    } catch {
      if (current()) notify('Could not update biometrics', 'Please try again.');
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <Screen>
      <Header title="Security" sub="Your account security details" onBack={() => router.back()} />
      {/* Password + PIN route to their CHANGE screens, which require the current
          password / PIN before setting a new one. */}
      <ZItem icon="shield" title="Password" sub="Change your account password"
             onPress={() => router.push('/setpassword?change=1' as any)} />
      <ZItem icon="lock" title="Transaction PIN" sub="Update your payment PIN"
             onPress={() => router.push('/resetpin')} />
      <ZItem icon="fingerprint" title="Biometrics" sub="Face ID / fingerprint sign-in" last
             onPress={() => router.push('/setthumbprint')}
             right={<Toggle accessibilityLabel="Biometric sign-in" on={bio} onChange={toggleBio} disabled={busy} />} />
    </Screen>
  );
};

// Post-login screen living in the unguarded (auth) group: gate it explicitly
// so a deep link can't render it without a valid, unlocked session (the API
// would 401 anyway — this keeps the surface consistent with the other groups).
const GuardedSecuritySetup = () => (
  <AuthGuard>
    <SecuritySetup />
  </AuthGuard>
);

export default GuardedSecuritySetup;
