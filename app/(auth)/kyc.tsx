import React, { useCallback, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import * as ImagePicker from 'expo-image-picker';
import { notify } from '@/components/design/Notify';
import { getToken } from '@/lib/secureStore';
import { apiJson } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, money, NText } from '@/components/design/ui';
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
      const res = await apiJson('/api/kyc/status/');
      if (res.success) setStatus(res);
    } catch { /* keep */ }
  }, []);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const submit = async (path: string, body: object, label: string) => {
    setBusy(true);
    try {
      const res = await apiJson(path, body);
      if (res.success) { setStatus(res); notify('Success', `${label} verified`); }
      else notify('Error', res.message || `${label} verification failed`);
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

  // Capture a live selfie and send it for server-side liveness verification.
  // The backend gates large transfers on the result, so a real image (not a
  // device-unlock claim) is what clears it.
  const verifyFace = async () => {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) {
      notify('Camera needed', 'Allow camera access so we can verify your identity.');
      return;
    }
    const shot = await ImagePicker.launchCameraAsync({
      cameraType: ImagePicker.CameraType.front,
      base64: true,
      quality: 0.4,
      allowsEditing: false,
    });
    if (shot.canceled || !shot.assets?.[0]?.base64) return;
    submit('/api/kyc/face/', { selfie: shot.assets[0].base64 }, 'Face');
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

      <NText style={{ fontSize: 12, color: c.ink3, marginTop: 16, lineHeight: 18, fontFamily: font.regular }}>
        Tier 1: ₦50,000 · Tier 2 (BVN or NIN): ₦200,000 · Tier 3 (BVN + NIN): ₦5,000,000 per transaction.
      </NText>
    </Screen>
  );
};

export default Kyc;
