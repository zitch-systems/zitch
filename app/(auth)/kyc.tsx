import React, { useCallback, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import * as ImagePicker from 'expo-image-picker';
import { notify } from '@/components/design/Notify';
import { getToken } from '@/lib/secureStore';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
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
  const [, setToken] = useState('');
  const [status, setStatus] = useState<Status | null>(null);
  const [bvn, setBvn] = useState('');
  const [bvnOtp, setBvnOtp] = useState('');
  const [bvnSent, setBvnSent] = useState(false);
  const [nin, setNin] = useState('');
  const [ninImage, setNinImage] = useState(''); // base64 of the NIN slip
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

  // --- BVN: enter number -> we send a one-time code -> confirm it ---
  const startBvn = async () => {
    setBusy(true);
    try {
      const res = await apiJson('/api/kyc/bvn/start/', { bvn });
      if (res.success) { setBvnSent(true); notify('Code sent', 'Enter the code we sent to your phone and email.'); }
      else notify('Error', res.message || 'Could not start BVN verification');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };
  const confirmBvn = async () => {
    setBusy(true);
    try {
      const res = await apiJson('/api/kyc/bvn/confirm/', { otp: bvnOtp });
      if (res.success) { setStatus(res); setBvnSent(false); setBvn(''); setBvnOtp(''); notify('Success', 'BVN verified'); }
      else notify('Error', res.message || 'Incorrect code');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

  // --- NIN: number + a photo of the NIN slip ---
  const pickNinSlip = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) { notify('Photos needed', 'Allow photo access to upload your NIN slip.'); return; }
    beginExternalActivity(); // don't let the app-lock fire while the picker is up
    try {
      const res = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images, base64: true, quality: 0.4, allowsEditing: true,
      });
      if (res.canceled || !res.assets?.[0]?.base64) return;
      setNinImage(res.assets[0].base64);
    } finally { endExternalActivity(); }
  };

  // --- Selfie: a real captured image for server-side liveness (NOT device
  // Face ID — KYC must match a face, which the device unlock can't prove). ---
  const verifySelfie = async () => {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) { notify('Camera needed', 'Allow camera access so we can verify your identity.'); return; }
    beginExternalActivity(); // keep the app-lock from firing while the camera is up
    let shot;
    try {
      shot = await ImagePicker.launchCameraAsync({
        cameraType: ImagePicker.CameraType.front, base64: true, quality: 0.4, allowsEditing: false,
      });
    } finally { endExternalActivity(); }
    if (shot.canceled || !shot.assets?.[0]?.base64) return;
    submit('/api/kyc/face/', { selfie: shot.assets[0].base64 }, 'Selfie');
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

      <KycRow icon="bank" title="BVN" sub="We'll send a code to verify it's yours" done={!!status?.bvn_verified}>
        {!bvnSent ? (
          <>
            <Field value={bvn} onChangeText={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))} keyboardType="number-pad" placeholder="Enter 11-digit BVN" />
            <View style={{ height: 10 }} />
            <Btn label="Send verification code" size="md" disabled={busy || bvn.length !== 11} onPress={startBvn} />
          </>
        ) : (
          <>
            <Text style={{ fontSize: 12.5, color: c.ink3, marginBottom: 8, fontFamily: font.regular }}>Enter the code sent to your phone & email.</Text>
            <Field value={bvnOtp} onChangeText={(v) => setBvnOtp(v.replace(/\D/g, '').slice(0, 6))} keyboardType="number-pad" placeholder="6-digit code" />
            <View style={{ height: 10 }} />
            <Btn label="Confirm BVN" size="md" disabled={busy || bvnOtp.length < 4} onPress={confirmBvn} />
            <Text onPress={() => { setBvnSent(false); setBvnOtp(''); }} style={{ textAlign: 'center', marginTop: 10, fontSize: 13, color: c.brand, fontFamily: font.semibold }}>Change BVN</Text>
          </>
        )}
      </KycRow>

      <KycRow icon="user" title="NIN" sub="Number + a photo of your NIN slip" done={!!status?.nin_verified}>
        <Field value={nin} onChangeText={(v) => setNin(v.replace(/\D/g, '').slice(0, 11))} keyboardType="number-pad" placeholder="Enter 11-digit NIN" />
        <View style={{ height: 10 }} />
        <Btn label={ninImage ? 'NIN slip added ✓' : 'Upload your NIN slip'} icon="copy" size="md" variant="outline" disabled={busy} onPress={pickNinSlip} />
        <View style={{ height: 10 }} />
        <Btn label="Verify NIN" size="md" disabled={busy || nin.length !== 11 || !ninImage} onPress={() => submit('/api/kyc/nin/', { nin, nin_image: ninImage }, 'NIN')} />
      </KycRow>

      <KycRow icon="faceid" title="Selfie verification" sub="A quick selfie — required for large transfers" done={!!status?.face_verified}>
        <Btn label="Take a selfie" icon="faceid" size="md" variant="outline" disabled={busy} onPress={verifySelfie} />
      </KycRow>

      <NText style={{ fontSize: 12, color: c.ink3, marginTop: 16, lineHeight: 18, fontFamily: font.regular }}>
        Tier 1: ₦50,000 · Tier 2 (BVN or NIN): ₦200,000 · Tier 3 (BVN + NIN): ₦5,000,000 per transaction. Your BVN/NIN are never stored in full.
      </NText>
    </Screen>
  );
};

export default Kyc;
