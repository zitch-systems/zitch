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
import { TERMS_URL, PRIVACY_URL } from '@/components/configFiles/links';
import { getPushAvailability, registerForPushNotifications } from '@/lib/notifications';

const Toggle = ({ label, ...props }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) => (
  <SharedToggle {...props} accessibilityLabel={label} />
);

const SECURITY_CENTER_SUB = 'Password, transaction PIN and biometrics';

const Settings = () => {
  const { c, theme, setTheme } = useTheme();
  const [loggingOut, setLoggingOut] = useState(false);
  const logoutLock = useRef(false);
  const mounted = useRef(true);
  const notificationLock = useRef(false);
  const [notificationBusy, setNotificationBusy] = useState(false);
  const chev = <ZIcon name="right" size={18} color={c.ink3} />;
  const pushAvailability = getPushAvailability();

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

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

  // Each setting lives in one place (17 Nov 2026). Password, transaction PIN and
  // biometric sign-in are all in Security Center, so Settings links to it once
  // instead of also listing Change Transaction PIN and a biometrics toggle.
  const security = [
    { icon: 'insurance', title: 'Security Center', sub: SECURITY_CENTER_SUB, go: () => router.push('/securitysetup') },
    // Verify me (17 Nov 2026): the email from sign-up, then BVN, NIN and the face check.
    { icon: 'user', title: 'Identity verification', sub: 'Your email, BVN, NIN and face check', go: () => router.push('/kyc') },
    { icon: 'chart', title: 'Account Limits', sub: 'Your tier and transaction limits', go: () => router.push('/accountlimits') },
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

      {/* One 20px column for the header, the cards and the section labels
          (17 Nov 2026); the labels line up with the row icons inside the cards. */}
      <View style={{ paddingHorizontal: 20 }}>
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
        <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.ink3, marginLeft: 16, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Preferences</Text>
        <Card pad={0} style={{ paddingHorizontal: 16 }}>
          <ZItem
            icon="spark" title="Dark mode" sub="Easier on the eyes at night"
            right={<Toggle label="Dark mode" on={theme === 'dark'} onChange={(v) => setTheme(v ? 'dark' : 'light')} />}
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
        <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.ink3, marginLeft: 16, marginTop: 18, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>Security</Text>
        <Card pad={0} style={{ paddingHorizontal: 16 }}>
          {security.map((r, i) => (
            <ZItem key={r.title} icon={r.icon} title={r.title} sub={r.sub} onPress={r.go} last={i === security.length - 1} right={chev} />
          ))}
        </Card>

        {/* About */}
        <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.ink3, marginLeft: 16, marginTop: 18, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>About</Text>
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
