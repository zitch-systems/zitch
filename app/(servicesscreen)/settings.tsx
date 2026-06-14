import React, { useEffect, useState } from 'react';
import { View, Text, Pressable, Linking } from 'react-native';
import { router } from 'expo-router';
import Constants from 'expo-constants';
import { Screen, Header, Card, ZItem } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { clearSession } from '@/lib/secureStore';
import { apiPost } from '@/lib/api';
import { isBiometricAvailable, isBiometricEnabled, setBiometricEnabled, authenticate } from '@/lib/biometrics';
import { TERMS_URL, PRIVACY_URL } from '@/components/configFiles/links';

const Toggle = ({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) => {
  const { c } = useTheme();
  return (
    <Pressable onPress={() => onChange(!on)} style={{ width: 46, height: 28, borderRadius: 999, padding: 3, backgroundColor: on ? c.brand : c.surface3, justifyContent: 'center' }}>
      <View style={{ width: 22, height: 22, borderRadius: 11, backgroundColor: '#fff', transform: [{ translateX: on ? 18 : 0 }] }} />
    </Pressable>
  );
};

const Settings = () => {
  const { c, theme, setTheme } = useTheme();
  const [biometrics, setBiometrics] = useState(false);
  const chev = <ZIcon name="right" size={18} color={c.ink3} />;

  useEffect(() => {
    isBiometricEnabled().then(setBiometrics);
  }, []);

  const toggleBio = async (v: boolean) => {
    if (!v) {
      await setBiometricEnabled(false);
      setBiometrics(false);
      return;
    }
    if (!(await isBiometricAvailable())) {
      notify('Biometrics unavailable', 'Set up Face ID or a fingerprint in your device settings first.');
      return;
    }
    if (await authenticate('Enable biometric sign-in')) {
      await setBiometricEnabled(true);
      setBiometrics(true);
    }
  };

  const openUrl = (url: string) => Linking.openURL(url).catch(() => notify('Error', 'Could not open this link.'));

  const handleLogout = async () => {
    try { await apiPost('/api/logout/'); } catch { /* fall through */ }
    await clearSession();
    router.replace('/signin');
  };

  const version = Constants.expoConfig?.version ?? '1.0.0';

  const security = [
    { icon: 'lock', title: 'Change Transaction PIN', sub: 'Update your 4-digit PIN', go: () => router.push('/resetpin') },
    { icon: 'insurance', title: 'Security Center', sub: 'Protect your funds', go: () => router.push('/securitysetup') },
    { icon: 'chart', title: 'Account Limits', sub: 'KYC tiers & limits', go: () => router.push('/kyc') },
  ];
  const about = [
    { icon: 'ticket', title: 'Terms of Service', go: () => openUrl(TERMS_URL) },
    { icon: 'insurance', title: 'Privacy Policy', go: () => openUrl(PRIVACY_URL) },
    { icon: 'help', title: 'Help & Support', go: () => router.push('/support') },
  ];

  return (
    <Screen pad={false}>
      <View style={{ paddingHorizontal: 20 }}>
        <Header title="Settings" onBack={() => router.back()} />
      </View>

      <View style={{ paddingHorizontal: 16 }}>
        {/* Preferences */}
        <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.ink3, marginLeft: 6, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Preferences</Text>
        <Card pad={0} style={{ paddingHorizontal: 16 }}>
          <ZItem
            icon="spark" title="Dark mode" sub="Easier on the eyes at night"
            right={<Toggle on={theme === 'dark'} onChange={(v) => setTheme(v ? 'dark' : 'light')} />}
          />
          <ZItem
            icon="faceid" title="Biometric login" sub="Sign in & approve payments" last
            right={<Toggle on={biometrics} onChange={toggleBio} />}
          />
        </Card>

        {/* Security */}
        <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.ink3, marginLeft: 6, marginTop: 18, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Security</Text>
        <Card pad={0} style={{ paddingHorizontal: 16 }}>
          {security.map((r, i) => (
            <ZItem key={r.title} icon={r.icon} title={r.title} sub={r.sub} onPress={r.go} last={i === security.length - 1} right={chev} />
          ))}
        </Card>

        {/* About */}
        <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.ink3, marginLeft: 6, marginTop: 18, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>About</Text>
        <Card pad={0} style={{ paddingHorizontal: 16 }}>
          {about.map((r, i) => (
            <ZItem key={r.title} icon={r.icon} title={r.title} onPress={r.go} last={i === about.length - 1} right={chev} />
          ))}
        </Card>

        <Pressable onPress={handleLogout} style={{ marginTop: 18, paddingVertical: 14, borderRadius: 16, backgroundColor: 'rgba(255,59,59,.1)', alignItems: 'center' }}>
          <Text style={{ color: c.red, fontFamily: font.bold }}>Log out</Text>
        </Pressable>

        <Text style={{ textAlign: 'center', color: c.ink3, fontSize: 12, marginTop: 16, fontFamily: font.regular }}>Zitch v{version}</Text>
      </View>
    </Screen>
  );
};

export default Settings;
