import React, { useEffect, useState } from 'react';
import { router } from 'expo-router';
import { Screen, Header, ZItem, Toggle } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { isBiometricAvailable, isBiometricEnabled, setBiometricEnabled, authenticate } from '@/lib/biometrics';
import AuthGuard from '@/components/AuthGuard';

const SecuritySetup = () => {
  const [bio, setBio] = useState(false);

  useEffect(() => { isBiometricEnabled().then(setBio); }, []);

  // Inline sign-in toggle on the Biometrics row. Tapping the toggle flips it
  // here; tapping the rest of the row opens the full biometrics page (where
  // payment approval also lives). The nested toggle Pressable captures its own
  // taps, so the two don't collide.
  const toggleBio = async (v: boolean) => {
    if (!v) { await setBiometricEnabled(false); setBio(false); return; }
    if (!(await isBiometricAvailable())) {
      notify('Unavailable', 'Set up Face ID or a fingerprint in your device settings first.');
      return;
    }
    if (await authenticate('Enable biometric sign-in')) { await setBiometricEnabled(true); setBio(true); }
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
             right={<Toggle on={bio} onChange={toggleBio} />} />
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
