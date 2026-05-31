import React, { useCallback, useState } from 'react';
import { View, Text, Alert } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import { isBiometricAvailable, authenticate } from '@/lib/biometrics';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, money } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

type Status = {
  tier: number; transaction_limit: string;
  bvn_verified: boolean; nin_verified: boolean; face_verified: boolean;
};

const KycRow = ({ icon, title, sub, done, children }: { icon: string; title: string; sub: string; done: boolean; children?: React.ReactNode }) => {
  const { c } = useTheme();
  return (
    <View style={{ backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, borderRadius: 18, padding: 16, marginTop: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: done ? 'rgba(0,181,29,.14)' : 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name={done ? 'check' : icon} size={20} color={done ? c.lime : c.brand} stroke={done ? 2.6 : 1.9} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={{ fontFamily: font.bold, color: c.ink1, fontSize: 15 }}>{title}</Text>
          <Text style={{ fontSize: 12.5, color: done ? c.lime : c.ink3, marginTop: 2, fontFamily: font.regular }}>{done ? 'Verified' : sub}</Text>
        </View>
      </View>
      {!done && children ? <View style={{ marginTop: 12 }}>{children}</View> : null}
    </View>
  );
};

const Kyc = () => {
  const { c } = useTheme();
  const [token, setToken] = useState('');
  const [status, setStatus] = useState<Status | null>(null);
  const [bvn, setBvn] = useState('');
  const [nin, setNin] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const t = await getToken();
    if (!t) return;
    setToken(t);
    try {
      const res = await fetch(`${baseUrl}/api/kyc/status/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: t }),
      }).then((r) => r.json());
      if (res.success) setStatus(res);
    } catch { /* keep */ }
  }, []);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const submit = async (path: string, body: object, label: string) => {
    setBusy(true);
    try {
      const res = await fetch(`${baseUrl}${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token, ...body }),
      }).then((r) => r.json());
      if (res.success) { setStatus(res); Alert.alert('Success', `${label} verified`); }
      else Alert.alert('Error', res.message || `${label} verification failed`);
    } catch { Alert.alert('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

  const verifyFace = async () => {
    const available = await isBiometricAvailable();
    if (!available) { Alert.alert('Unavailable', 'Set up Face ID or a fingerprint on this device first.'); return; }
    const ok = await authenticate('Verify your identity');
    if (ok) submit('/api/kyc/face/', {}, 'Face');
  };

  return (
    <Screen>
      <Header title="Account Limits & KYC" sub="Verify your identity to raise limits" onBack={() => router.back()} />

      {status && (
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: c.surface3, borderRadius: 16, padding: 16, marginBottom: 4 }}>
          <View>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>Current tier</Text>
            <Text style={{ fontSize: 20, fontFamily: font.extrabold, color: c.ink1 }}>Tier {status.tier}</Text>
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>Per-transaction limit</Text>
            <Text style={{ fontSize: 16, fontFamily: font.bold, color: c.brand, fontVariant: ['tabular-nums'] }}>{money(Number(status.transaction_limit))}</Text>
          </View>
        </View>
      )}

      <KycRow icon="bank" title="BVN" sub="Bank Verification Number" done={!!status?.bvn_verified}>
        <Field value={bvn} onChangeText={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))} keyboardType="number-pad" placeholder="Enter 11-digit BVN" />
        <View style={{ height: 10 }} />
        <Btn label="Verify BVN" size="md" disabled={busy || bvn.length !== 11} onPress={() => submit('/api/kyc/bvn/', { bvn }, 'BVN')} />
      </KycRow>

      <KycRow icon="user" title="NIN" sub="National Identification Number" done={!!status?.nin_verified}>
        <Field value={nin} onChangeText={(v) => setNin(v.replace(/\D/g, '').slice(0, 11))} keyboardType="number-pad" placeholder="Enter 11-digit NIN" />
        <View style={{ height: 10 }} />
        <Btn label="Verify NIN" size="md" disabled={busy || nin.length !== 11} onPress={() => submit('/api/kyc/nin/', { nin }, 'NIN')} />
      </KycRow>

      <KycRow icon="faceid" title="Face verification" sub="Required for large transfers" done={!!status?.face_verified}>
        <Btn label="Verify with Face ID" icon="faceid" size="md" variant="outline" disabled={busy} onPress={verifyFace} />
      </KycRow>

      <Text style={{ fontSize: 12, color: c.ink3, marginTop: 16, lineHeight: 18, fontFamily: font.regular }}>
        Tier 1: ₦50,000 · Tier 2 (BVN or NIN): ₦200,000 · Tier 3 (BVN + NIN): ₦5,000,000 per transaction.
      </Text>
    </Screen>
  );
};

export default Kyc;
