import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import * as Device from 'expo-device';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Btn } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { publicPost } from '@/lib/api';
import { storeSession, rememberIdentifier, getSessionGeneration } from '@/lib/secureStore';
import { unlockSession, beginExternalActivity, endExternalActivity } from '@/lib/session';
import { registerForPushNotifications } from '@/lib/notifications';
import { useTheme, font } from '@/lib/theme';

/**
 * Signing in on a new device: the password was right, but this account has never
 * been used on this phone, so the server holds the session until a Prembly face
 * check matches the customer to their BVN photo (accounts.views.signin_face_*).
 * Nothing on the account changes; the check only opens this sign-in.
 */
const PREMBLY_ORIGIN = 'https://sdk-live.prembly.com';

const NewDevice = () => {
  const { c } = useTheme();
  const params = useLocalSearchParams<{ challenge?: string; id?: string }>();
  const challenge = String(params.challenge || '');
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState('');
  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  useEffect(() => {
    if (!challenge) router.replace('/signin');
  }, [challenge]);

  const expired = (message?: string) => {
    notify('Sign in again', message || 'This sign-in has expired. Enter your password to start again.');
    router.replace('/signin');
  };

  // Poll a few times: the result reaches our server from Prembly, not the browser.
  const finish = async (state: string, generation: number) => {
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const response = await publicPost('/api/signin/face/status/', { challenge, session: state });
      const result = await response.json().catch(() => ({}));
      if (!mounted.current || generation !== getSessionGeneration()) return;
      if (response.ok && result.access_token) {
        await storeSession(result, generation);
        const authenticated = getSessionGeneration();
        if (params.id) await rememberIdentifier(String(params.id), authenticated);
        await unlockSession();
        registerForPushNotifications(false).catch(() => {});
        router.replace('/home');
        return;
      }
      if (response.status === 401) { expired(result.message); return; }
      if (result.status === 'failed' || response.status === 409) {
        setSession('');
        notify('Face check not confirmed', result.message || 'We could not confirm it was you. Try the face check again.');
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 2500));
    }
    notify('Still checking', 'Prembly has not confirmed your face check yet. Tap Check again in a moment.', 'info');
  };

  const start = async () => {
    if (busy || !challenge) return;
    setBusy(true);
    const generation = getSessionGeneration();
    try {
      let state = session;
      if (!state) {
        const response = await publicPost('/api/signin/face/start/', { challenge });
        const started = await response.json().catch(() => ({}));
        if (!mounted.current) return;
        if (response.status === 401) { expired(started.message); return; }
        if (!response.ok || !started.session || !started.url) {
          notify('Face check', started.message || 'Could not start the face check. Please try again.', 'info');
          return;
        }
        const target = new URL(String(started.url));
        if (target.origin !== PREMBLY_ORIGIN || target.username || target.password) {
          notify('Face check unavailable', 'The face check link could not be confirmed.');
          return;
        }
        state = String(started.session);
        setSession(state);
        beginExternalActivity();
        try { await WebBrowser.openBrowserAsync(target.toString()); }
        finally { endExternalActivity(); }
      }
      if (!mounted.current) return;
      await finish(state, generation);
    } catch {
      if (mounted.current) notify('Face check', 'Something went wrong. Check your connection and try again.');
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const tips: [string, string][] = [
    ['eye', 'Find good light and keep your face uncovered'],
    ['faceid', 'Follow the prompts: look ahead, blink, turn'],
    ['shield', 'We match your face to your BVN photo'],
  ];

  return (
    <Screen>
      <Header onBack={() => router.replace('/signin')} />
      <View style={{ alignItems: 'center', marginTop: 8 }}>
        <View style={{ width: 92, height: 92, borderRadius: 46, backgroundColor: 'rgba(6,110,102,0.10)', alignItems: 'center', justifyContent: 'center' }}>
          <View style={{ width: 64, height: 64, borderRadius: 32, backgroundColor: c.surface, alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="scan" size={30} color={c.brand} />
          </View>
          <View style={{ position: 'absolute', right: 8, bottom: 8, width: 24, height: 24, borderRadius: 12, backgroundColor: c.brand, alignItems: 'center', justifyContent: 'center', borderWidth: 2, borderColor: c.surface }}>
            <ZIcon name="lock" size={12} color="#fff" stroke={2.6} />
          </View>
        </View>
        <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, marginTop: 18, textAlign: 'center' }}>Confirm it&apos;s you</Text>
        <Text style={{ fontSize: 14, fontFamily: font.regular, color: c.ink3, marginTop: 8, lineHeight: 21, textAlign: 'center', paddingHorizontal: 8 }}>
          You&apos;re signing in on a new device. A quick face check matches you to your BVN photo.
        </Text>
      </View>

      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 22, padding: 14, borderRadius: 18, backgroundColor: c.surface }}>
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: c.surface2, alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="phone" size={20} color={c.ink2} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 11.5, fontFamily: font.semibold, color: c.ink3 }}>New device</Text>
          <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1, marginTop: 2 }}>{Device.modelName || 'This phone'}</Text>
          <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, marginTop: 1 }}>Just now</Text>
        </View>
      </View>

      <View style={{ gap: 12, marginTop: 18, marginBottom: 24 }}>
        {tips.map(([icon, text]) => (
          <View key={text} style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
            <View style={{ width: 26, height: 26, borderRadius: 13, backgroundColor: 'rgba(6,110,102,0.10)', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name={icon} size={14} color={c.brand} />
            </View>
            <Text style={{ flex: 1, fontSize: 13.5, fontFamily: font.regular, color: c.ink2 }}>{text}</Text>
          </View>
        ))}
      </View>

      <Btn label={busy ? 'Checking…' : session ? 'Check again' : 'Start face check'} icon="scan" onPress={() => void start()} disabled={busy} />
      <Pressable onPress={() => router.replace('/signin')} accessibilityRole="button" accessibilityLabel="Cancel sign-in" style={{ alignItems: 'center', paddingVertical: 14 }}>
        <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.brand }}>Cancel sign-in</Text>
      </Pressable>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 4 }}>
        <ZIcon name="lock" size={13} color={c.ink3} />
        <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3 }}>Face check by Prembly, matched to your BVN</Text>
      </View>
    </Screen>
  );
};

export default NewDevice;
