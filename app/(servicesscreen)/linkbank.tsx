import React from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { Screen, Header, Btn } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { useMono } from '@/lib/mono';
import { apiJson } from '@/lib/api';

// Connect a bank to Zitch via Mono. The SDK returns an auth code on success; we
// POST it to /api/banklink/connect/ which exchanges it server-side and links the
// account. See lib/mono.tsx for the SDK wiring + Expo Go fallback.
const LinkBank = () => {
  const { c } = useTheme();
  const { reloadLinked } = useWallet();
  const { launch, native } = useMono();

  const connect = () =>
    launch({
      reference: 'zitch-link-' + Date.now(),
      onSuccess: async (code) => {
        const r = await apiJson<{ success?: boolean; account?: { bank_name?: string }; message?: string }>('/api/banklink/connect/', { code });
        if (r?.success) {
          notify('Bank connected', `${r.account?.bank_name || 'Your bank'} is now linked to your wallet.`);
          reloadLinked();
          router.back();
        } else {
          notify('Could not connect', r?.message || 'We could not link that account. Please try again.');
        }
      },
      onClose: () => {},
    });

  const checks: [string, string, string][] = [
    ['eye', 'Read-only by default', 'We only see what you allow — never move money without you.'],
    ['shield', 'Bank-grade, you consent each time', '256-bit encryption. You approve every connection and debit.'],
    ['unlink', 'Unlink anytime', 'Remove a connected bank in one tap, from your wallet.'],
  ];

  return (
    <Screen>
      <Header title="Add a bank" onBack={() => router.back()} />

      {/* hero illustration: mint panel + teal rounded square + cyan circle + link */}
      <View style={{ borderRadius: 24, backgroundColor: c.surface3, paddingVertical: 34, alignItems: 'center', marginBottom: 22 }}>
        <View style={{ width: 110, height: 96 }}>
          <View style={{ position: 'absolute', right: 4, top: -2, width: 34, height: 34, borderRadius: 17, backgroundColor: c.cyan }} />
          <View style={{ position: 'absolute', left: 0, bottom: -2, width: 22, height: 22, borderRadius: 11, backgroundColor: 'rgba(15,162,149,.3)' }} />
          <View style={{ width: 82, height: 82, borderRadius: 26, backgroundColor: c.brand, alignItems: 'center', justifyContent: 'center', alignSelf: 'center', marginTop: 8 }}>
            <ZIcon name="link" size={38} color="#fff" stroke={2} />
          </View>
        </View>
      </View>

      <Text style={{ fontSize: 24, fontFamily: font.extrabold, color: c.ink1, textAlign: 'center', letterSpacing: -0.2 }}>Link your bank to Zitch</Text>
      <Text style={{ fontSize: 14, fontFamily: font.regular, color: c.ink3, textAlign: 'center', marginTop: 8, lineHeight: 21, paddingHorizontal: 8 }}>
        See your balances and move money in — securely, with your consent. Powered by Mono.
      </Text>

      <View style={{ marginTop: 24, gap: 14 }}>
        {checks.map(([icon, title, sub]) => (
          <View key={title} style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 13 }}>
            <View style={{ width: 38, height: 38, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name={icon} size={19} color={c.brand} />
            </View>
            <View style={{ flex: 1, paddingTop: 1 }}>
              <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>{title}</Text>
              <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: c.ink3, marginTop: 2, lineHeight: 18 }}>{sub}</Text>
            </View>
          </View>
        ))}
      </View>

      <Text style={{ marginTop: 22, textAlign: 'center', fontSize: 12, color: c.ink3, fontFamily: font.regular }}>
        Your bank login never touches Zitch's servers.
      </Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 8, marginBottom: 8 }}>
        <ZIcon name="lock" size={13} color={c.brand} />
        <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.brandDeep }}>Secured by Mono</Text>
      </View>

      <View style={{ height: 18 }} />
      <Btn label="Connect a bank" icon="link" onPress={connect} />
      {!native && (
        <Text style={{ textAlign: 'center', fontSize: 11, color: c.ink3, marginTop: 10, fontFamily: font.regular }}>
          Dev preview — install the Mono SDK build for live bank linking.
        </Text>
      )}
    </Screen>
  );
};

export default LinkBank;
