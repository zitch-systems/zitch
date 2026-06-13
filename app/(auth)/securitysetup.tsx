import React from 'react';
import { router } from 'expo-router';
import { Screen, Header, ZItem } from '@/components/design/ui';

const SecuritySetup = () => {
  const items = [
    { icon: 'lock', title: 'Password', sub: 'Change your account password', to: '/setpassword' },
    { icon: 'qr', title: 'Transaction PIN', sub: 'Update your payment PIN', to: '/setpin' },
    { icon: 'fingerprint', title: 'Biometrics', sub: 'Face ID / fingerprint sign-in', to: '/setthumbprint' },
  ];
  return (
    <Screen>
      <Header title="Security" sub="Your account security details" onBack={() => router.back()} />
      {items.map((it, i) => (
        <ZItem key={it.title} icon={it.icon} title={it.title} sub={it.sub} onPress={() => router.push(it.to as any)} last={i === items.length - 1} />
      ))}
    </Screen>
  );
};

export default SecuritySetup;
