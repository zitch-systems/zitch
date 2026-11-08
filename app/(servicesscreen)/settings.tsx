import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, Linking, Alert } from 'react-native';
import { router } from 'expo-router';
import Constants from 'expo-constants';
import { Screen, Header, Card, ZItem, Toggle as SharedToggle } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { useTheme, font } from '@/lib/theme';
import { clearSession, getSessionGeneration } from '@/lib/secureStore';
import { apiPost } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import { isBiometricAvailable, isBiometricEnabled, setBiometricEnabled, authenticate } from '@/lib/biometrics';
import { TERMS_URL, PRIVACY_URL } from '@/components/configFiles/links';
import { getPushAvailability, registerForPushNotifications } from '@/lib/notifications';

const Toggle = ({ label, ...props }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) => (
  <SharedToggle {...props} accessibilityLabel={label} />
);

const Settings = () => {
  const { c, theme, setTheme } = useTheme();
  const [biometrics, setBiometrics] = useState(false);
  const [bioBusy, setBioBusy] = useState(true);
  const [loggingOut, setLoggingOut] = useState(false);
  const bioLock = useRef(false);
  const logoutLock = useRef(false);
  const mounted = useRef(true);
  const notificationLock = useRef(false);
  const [notificationBusy, setNotificationBusy] = useState(false);
  const chev = <ZIcon name="right" size={18} color={c.ink3} />;
  const pushAvailability = getPushAvailability();

  useEffect(() => {
    let active = true;
    mounted.current = true;
    const generation = getSessionGeneration();
    const current = () => active && generation === getSessionGeneration();
    isBiometricEnabled().then((enabled) => { if (current()) setBiometrics(enabled); })
      .catch(() => { if (current()) notify('Could not load preferences', 'Please reopen Settings to try again.'); })
      .finally(() => { if (current()) setBioBusy(false); });
    return () => { active = false; mounted.current = false; };
  }, []);

  const toggleBio = async (v: boolean) => {
    if (bioLock.current || bioBusy || !mounted.current) return;
    const generation = getSessionGeneration();
    const current = () => mounted.current && generation === getSessionGeneration();
    bioLock.current = true;
    setBioBusy(true);
    try {
      if (v) {
        const available = await isBiometricAvailable();
        if (!current()) return;
        if (!available) {
          notify('Biometrics unavailable', 'Set up Face ID or a fingerprint in your device settings first.');
          return;
        }
        const authenticated = await authenticate('Enable biometric sign-in');
        if (!current() || !authenticated) return;
      }
      if (!current()) return;
      await setBiometricEnabled(v);
      if (current()) setBiometrics(v);
    } catch {
      if (current()) notify('Could not save preference', 'Please try again.');
    } finally {
      bioLock.current = false;
      if (current()) setBioBusy(false);
    }
  };

  const openUrl = (url: string) => Linking.openURL(url).catch(() => notify('Error', 'Could not open this link.'));

  const enableNotifications = async () => {
    if (notificationLock.current || !mounted.current) return;
    const generation = getSessionGeneration();
    const current = () => mounted.current && generation === getSessionGeneration();
    if (pushAvailability !== 'available') {
      notify('Alerts unavailable', pushAvailability === 'not-configured'
        ? 'Push alerts are not available in this build. Your activity is still available in Notifications.'
        : 'Use the installed Zitch app on your phone to register for transaction alerts.');
      return;
    }
    notificationLock.current = true;
    setNotificationBusy(true);
    try {
      const result = await registerForPushNotifications(true);
      if (!current()) return;
      if (result === 'registered') {
        notify('Device registered', 'This device is registered for transaction alerts. You can also check your activity in Notifications.');
      } else if (result === 'denied') {
        Alert.alert('Notifications are off', 'Enable Zitch notifications in your phone settings, then return here and tap Transaction alerts again.', [
          { text: 'Not now', style: 'cancel' },
          { text: 'Open settings', onPress: () => {
            if (!current()) return;
            void Linking.openSettings().catch(() => {
              if (current()) notify('Could not open settings', 'Open your phone settings to enable Zitch notifications.');
            });
          } },
        ]);
      } else {
        notify('Alerts unavailable', result === 'not-configured'
          ? 'Push alerts are not available in this build. Your activity is still available in Notifications.'
          : result === 'unavailable'
          ? 'Use the installed Zitch app on your phone to enable transaction alerts.'
          : 'We could not enable alerts on this device. Please try again later. Your activity is available in Notifications.');
      }
    } catch {
      if (current()) notify('Alerts unavailable', 'We could not enable alerts on this device. Please try again later.');
    } finally {
      notificationLock.current = false;
      if (current()) setNotificationBusy(false);
    }
  };

  const handleLogout = async () => {
    if (logoutLock.current || !mounted.current) return;
    let generation = getSessionGeneration();
    const current = () => mounted.current && generation === getSessionGeneration();
    logoutLock.current = true;
    setLoggingOut(true);
    try {
      try { await apiPost(EP.auth.logout, {}, 5000); } catch { /* clear only the session that requested logout */ }
      if (!current()) return;
      const clearing = clearSession();
      // clearSession advances the generation synchronously. A later login
      // during secure-store cleanup must not be redirected out by this logout.
      generation = getSessionGeneration();
      await clearing;
      if (current()) router.replace('/signin');
    } catch {
      if (current()) notify('Could not finish signing out', 'Please try again to clear this device session.');
    } finally {
      logoutLock.current = false;
      if (current()) setLoggingOut(false);
    }
  };

  const version = Constants.expoConfig?.version ?? '1.0.0';

  const security = [
    { icon: 'lock', title: 'Change Transaction PIN', sub: 'Update your 6-digit PIN', go: () => router.push('/resetpin') },
    { icon: 'insurance', title: 'Security Center', sub: 'Protect your funds', go: () => router.push('/securitysetup') },
    { icon: 'chart', title: 'Account Limits', sub: 'Account details & available services', go: () => router.push('/accountlimits') },
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
        {/* Bank on WhatsApp — the channel's hero entry */}
        <Card pad={0} style={{ paddingHorizontal: 16, marginBottom: 18 }}>
          <Pressable accessibilityRole="button" accessibilityLabel="Link WhatsApp" onPress={() => router.push('/linkwhatsapp')} style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 15, gap: 14 }}>
            <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: '#25D366', alignItems: 'center', justifyContent: 'center' }}>
              <WhatsAppGlyph size={22} color="#fff" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ fontFamily: font.semibold, color: c.ink1, fontSize: 15 }}>Link WhatsApp</Text>
              <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 12.5, marginTop: 2 }}>Bank from your WhatsApp chats</Text>
            </View>
            {chev}
          </Pressable>
        </Card>

        {/* Preferences */}
        <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.ink3, marginLeft: 6, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Preferences</Text>
        <Card pad={0} style={{ paddingHorizontal: 16 }}>
          <ZItem
            icon="spark" title="Dark mode" sub="Easier on the eyes at night"
            right={<Toggle label="Dark mode" on={theme === 'dark'} onChange={(v) => setTheme(v ? 'dark' : 'light')} />}
          />
          <ZItem
            icon="faceid" title="Biometric login" sub="Unlock Zitch with your face or fingerprint"
            right={<Toggle label="Biometric login" disabled={bioBusy} on={biometrics} onChange={toggleBio} />}
          />
          <ZItem
            icon="bell" title="Transaction alerts" sub={notificationBusy ? 'Registering this device…'
              : pushAvailability === 'available' ? 'Register this device for notifications'
              : pushAvailability === 'not-configured' ? 'Push alerts unavailable in this build'
              : 'Push alerts unavailable on this device'} last
            onPress={() => { void enableNotifications(); }} right={chev}
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

        <Pressable accessibilityRole="button" disabled={loggingOut} onPress={handleLogout} style={{ marginTop: 18, paddingVertical: 14, borderRadius: 16, backgroundColor: 'rgba(255,59,59,.1)', alignItems: 'center' }}>
          <Text style={{ color: c.red, fontFamily: font.bold }}>{loggingOut ? 'Logging out…' : 'Log out'}</Text>
        </Pressable>

        <Text style={{ textAlign: 'center', color: c.ink3, fontSize: 12, marginTop: 16, fontFamily: font.regular }}>Zitch v{version}</Text>
      </View>
    </Screen>
  );
};

export default Settings;
