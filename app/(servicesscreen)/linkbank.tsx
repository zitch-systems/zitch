import React, { useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import { Screen, Header, Card, Btn } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { apiJson } from '@/lib/api';
import { useTheme, font } from '@/lib/theme';

// The three consent assurances shown on the link screen (matches design v2).
const CHECKS: { icon: string; title: string; sub: string }[] = [
  { icon: 'eye', title: 'Read-only by default', sub: 'We see balances & history to help you — never your login.' },
  { icon: 'insurance', title: 'Bank-grade & consented', sub: 'You authorise each connection; secured by Mono.' },
  { icon: 'unlink', title: 'Unlink anytime', sub: 'Remove a connected bank whenever you want.' },
];

const LinkBank = () => {
  const { c } = useTheme();
  const [busy, setBusy] = useState(false);

  // Open the hosted Mono Connect widget, capture the returned auth code, and
  // exchange it server-side. Uses an app deep link as the redirect so the auth
  // session closes back into Zitch.
  const connect = async () => {
    setBusy(true);
    try {
      const redirect = Linking.createURL('linkbank');
      const init = await apiJson<{ success?: boolean; mono_url?: string; message?: string }>(
        '/api/banklink/connect-init/',
        { redirect_url: redirect },
      );
      if (!init.success || !init.mono_url) {
        notify('Error', init.message || "Couldn't start bank linking.");
        return;
      }
      const result = await WebBrowser.openAuthSessionAsync(init.mono_url, redirect);
      if (result.type !== 'success' || !result.url) return; // user dismissed
      const code = Linking.parse(result.url).queryParams?.code;
      if (!code) {
        notify('Error', "Bank linking didn't complete. Please try again.");
        return;
      }
      const res = await apiJson<{ success?: boolean; message?: string }>(
        '/api/banklink/connect/',
        { code: String(code) },
      );
      if (res.success) {
        notify('Bank linked', 'Your account is now connected.');
        router.replace('/wallet');
      } else {
        notify('Error', res.message || "Couldn't link your bank.");
      }
    } catch {
      notify('Error', 'Something went wrong linking your bank.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen>
      <Header title="Link a bank" onBack={() => router.back()} />

      {/* hero illustration */}
      <View style={{ alignItems: 'center', marginTop: 8, marginBottom: 18 }}>
        <View style={{ width: 96, height: 96, borderRadius: 28, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="link" size={42} color={c.brand} />
        </View>
      </View>

      <Text style={{ fontSize: 22, fontFamily: font.extrabold, color: c.ink1, textAlign: 'center', paddingHorizontal: 24 }}>
        Link your bank to Zitch
      </Text>
      <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular, textAlign: 'center', marginTop: 8, paddingHorizontal: 28, lineHeight: 20 }}>
        See your balances and move money in — securely, with your consent. Powered by Mono.
      </Text>

      <Card style={{ marginHorizontal: 16, marginTop: 20, gap: 16 }}>
        {CHECKS.map((k) => (
          <View key={k.title} style={{ flexDirection: 'row', gap: 12, alignItems: 'flex-start' }}>
            <View style={{ width: 34, height: 34, borderRadius: 11, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name={k.icon} size={18} color={c.brand} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>{k.title}</Text>
              <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular, marginTop: 2, lineHeight: 18 }}>{k.sub}</Text>
            </View>
          </View>
        ))}
      </Card>

      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 16, paddingHorizontal: 24 }}>
        <ZIcon name="lock" size={13} color={c.ink3} />
        <Text style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.medium, textAlign: 'center' }}>
          Your bank login never touches Zitch's servers.
        </Text>
      </View>

      <View style={{ paddingHorizontal: 16, marginTop: 22 }}>
        <Btn label={busy ? 'Connecting…' : 'Connect a bank'} icon="bank" disabled={busy} onPress={connect} />
      </View>
    </Screen>
  );
};

export default LinkBank;
