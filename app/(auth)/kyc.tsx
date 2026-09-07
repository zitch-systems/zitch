import React, { useCallback, useRef, useState } from 'react';
import { View, Text, Animated, Easing } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import * as ImagePicker from 'expo-image-picker';
import Svg, { Circle } from 'react-native-svg';
import { notify } from '@/components/design/Notify';
import { getToken } from '@/lib/secureStore';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { kycService, type KycStatus } from '@/lib/services/kyc';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Tap, money } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

type Status = {
  tier: number; transaction_limit: string;
  bvn_verified: boolean; nin_verified: boolean; face_verified: boolean;
  // Set once the bank has opened the account number: from then on it will not
  // accept a lone BVN or NIN, only all of it at once. Read here so the menu can
  // send the customer straight to the step that works.
  identity_upgrade_required?: boolean;
};

type Method = 'menu' | 'bvn' | 'nin' | 'selfie' | 'upgrade';

// Method accent colours — EXACT per the design handoff.
const C_BVN = '#0FA295';
const C_NIN = '#2D7FF9';
const C_SELFIE = '#7A5CFF';

const cardShadow = {
  shadowColor: '#063731',
  shadowOpacity: 0.12,
  shadowRadius: 16,
  shadowOffset: { width: 0, height: 8 },
  elevation: 3,
};

const Kyc = () => {
  const { c } = useTheme();
  const [, setToken] = useState('');
  const [status, setStatus] = useState<Status | null>(null);
  const [method, setMethod] = useState<Method>('menu');
  const [bvn, setBvn] = useState('');
  const [bvnOtp, setBvnOtp] = useState('');
  const [bvnSent, setBvnSent] = useState(false);
  const [nin, setNin] = useState('');
  const [ninOtp, setNinOtp] = useState('');
  const [ninTrackingId, setNinTrackingId] = useState('');
  const [ninSent, setNinSent] = useState(false);
  const [ninImage, setNinImage] = useState(''); // base64 of the NIN slip
  // Combined existing-account upgrade. The bank scores all three together, so
  // they are collected before anything is sent — a partial submission is just a
  // refusal with the customer's identity already handed over.
  const [upBvn, setUpBvn] = useState('');
  const [upNin, setUpNin] = useState('');
  const [upSelfie, setUpSelfie] = useState('');
  const [busy, setBusy] = useState(false);
  const [scanning, setScanning] = useState(false); // selfie liveness ring running
  const spin = useRef(new Animated.Value(0)).current;

  const load = useCallback(async () => {
    const t = await getToken();
    if (!t) return;
    setToken(t);
    try {
      const res = await kycService.getStatus();
      if (res.success) setStatus(res);
    } catch { /* keep */ }
  }, []);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  // Return to the method menu and clear any in-flight sub-flow state.
  const goMenu = () => {
    setMethod('menu');
    setBvnSent(false);
    setNinSent(false);
    setUpBvn(''); setUpNin(''); setUpSelfie('');
    setScanning(false);
    setBusy(false);
    spin.stopAnimation();
  };
  const onBack = method === 'menu' ? () => router.back() : goMenu;
  // Both identities are still outstanding to the BANK in this state even when
  // one is verified with us, because the upgrade request carries both.
  const needsUpgrade = !!status?.identity_upgrade_required
    && !(status?.bvn_verified && status?.nin_verified);

  // Shared submit: on success update tier status, toast the design copy, reset
  // the sub-flow fields and bounce back to the menu.
  const submit = async (call: () => Promise<KycStatus>, successTitle: string) => {
    setBusy(true);
    try {
      const res = await call();
      if (res.success) {
        setStatus(res);
        notify(successTitle, undefined, 'success');
        setBvn(''); setBvnOtp(''); setBvnSent(false);
        setNin(''); setNinOtp(''); setNinTrackingId(''); setNinSent(false); setNinImage('');
        setUpBvn(''); setUpNin(''); setUpSelfie('');
        setMethod('menu');
      } else notify('Error', res.message || 'Verification failed');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

  // --- BVN: enter number -> we send a one-time code -> confirm it ---
  const startBvn = async () => {
    setBusy(true);
    try {
      const res = await kycService.startBvn(bvn);
      if (res.success) { setBvnSent(true); notify('Code sent to your BVN phone', undefined, 'success'); }
      else notify('Error', res.message || 'Could not start BVN verification');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };
  const confirmBvn = () => submit(() => kycService.confirmBvn(bvnOtp), 'BVN verified — tier upgraded');

  // --- NIN: enter number -> Wema sends a one-time code -> confirm it ---
  const startNin = async () => {
    setBusy(true);
    try {
      const res = await kycService.startNin(nin);
      if (res.success && res.otp_required && res.tracking_id) {
        setNinTrackingId(String(res.tracking_id));
        setNinSent(true);
        notify('NIN code requested', res.message || 'Enter the Wema SMS code to finish.', 'success');
      } else if (res.success) {
        setStatus(res);
        notify('NIN verified', res.message || 'NIN verified successfully', 'success');
        setNin(''); setNinOtp(''); setNinTrackingId(''); setNinSent(false);
        setMethod('menu');
      } else if (res.upgrade_required) {
        // Not an error the customer can retry out of: this account can only be
        // finished by the combined upgrade. Carry the NIN they already typed
        // across so they are not asked for it twice.
        setUpNin(nin);
        setMethod('upgrade');
      } else notify('Error', res.message || 'Could not start NIN verification');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };
  const confirmNin = () => submit(
    () => kycService.confirmNin(ninTrackingId, ninOtp, nin),
    'NIN verified — tier upgraded',
  );

  // Optional document upload remains available for providers that require a slip.
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
  const verifyNin = startNin;

  // --- Selfie: a real captured image for server-side liveness (NOT device
  // Face ID — KYC must match a face, which the device unlock can't prove). ---
  const captureSelfie = async () => {
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
    const selfie = shot.assets[0].base64;
    submit(() => kycService.verifyFace(selfie), 'Selfie verified — liveness passed');
  };
  // Show a visible liveness ring (~2.3s spin) THEN open the front camera.
  const runSelfie = () => {
    if (scanning || busy) return;
    setScanning(true);
    spin.setValue(0);
    Animated.loop(Animated.timing(spin, { toValue: 1, duration: 1000, easing: Easing.linear, useNativeDriver: true })).start();
    setTimeout(() => {
      spin.stopAnimation();
      setScanning(false);
      captureSelfie();
    }, 2300);
  };
  // --- Combined upgrade: BVN + NIN + a live selfie, submitted together ---
  // The selfie is captured and HELD rather than posted, because the bank scores
  // it against the two numbers in one request. Posting it alone would spend the
  // liveness check on a call that cannot complete the upgrade.
  const captureUpgradeSelfie = async () => {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) { notify('Camera needed', 'Allow camera access so we can verify your identity.'); return; }
    beginExternalActivity();
    let shot;
    try {
      shot = await ImagePicker.launchCameraAsync({
        cameraType: ImagePicker.CameraType.front, base64: true, quality: 0.4, allowsEditing: false,
      });
    } finally { endExternalActivity(); }
    if (shot.canceled || !shot.assets?.[0]?.base64) return;
    setUpSelfie(shot.assets[0].base64);
  };
  const submitUpgrade = () => submit(
    () => kycService.upgradeTier2(upBvn, upNin, upSelfie),
    'Identity verified — tier upgraded',
  );

  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });

  // ---- pieces shared by every sub-flow ----
  const Hero = ({ icon, color, title, sub }: { icon: string; color: string; title: string; sub: string }) => (
    <View style={{ alignItems: 'center', paddingHorizontal: 8, paddingTop: 4 }}>
      <View style={{ width: 80, height: 80, borderRadius: 24, backgroundColor: color + '22', alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name={icon} size={38} color={color} stroke={1.9} />
      </View>
      <Text style={{ fontSize: 20, fontFamily: font.extrabold, color: c.ink1, marginTop: 14 }}>{title}</Text>
      <Text style={{ fontSize: 13.5, color: c.ink3, marginTop: 6, lineHeight: 20, textAlign: 'center', maxWidth: 300, fontFamily: font.regular }}>{sub}</Text>
    </View>
  );

  const Footer = () => (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7, marginTop: 18 }}>
      <ZIcon name="lock" size={13} color={c.ink3} />
      <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.regular }}>BVN/NIN are never stored in full.</Text>
    </View>
  );

  const MethodCard = ({ id, icon, color, title, sub, badge, done }: { id: Method; icon: string; color: string; title: string; sub: string; badge?: string; done?: boolean }) => (
    <Tap onPress={() => setMethod(id)} style={{ marginTop: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 13, padding: 15, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, ...cardShadow }}>
        <View style={{ width: 46, height: 46, borderRadius: 13, backgroundColor: color + '22', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name={icon} size={22} color={color} stroke={1.9} />
        </View>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>{title}</Text>
          <Text style={{ fontSize: 12.5, color: done ? c.lime : c.ink3, marginTop: 1, fontFamily: font.regular }}>{done ? 'Verified' : sub}</Text>
        </View>
        {done ? (
          <ZIcon name="check" size={20} color={c.lime} stroke={2.6} />
        ) : badge ? (
          <View style={{ paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, backgroundColor: 'rgba(15,162,149,.14)' }}>
            <Text style={{ fontSize: 10, fontFamily: font.bold, color: C_BVN }}>{badge}</Text>
          </View>
        ) : (
          <ZIcon name="right" size={18} color={c.ink3} />
        )}
      </View>
    </Tap>
  );

  return (
    <Screen>
      <Header title="Identity verification" onBack={onBack} />

      {method === 'menu' && (
        <View>
          <Text style={{ fontSize: 13.5, color: c.ink3, lineHeight: 20, marginBottom: 6, fontFamily: font.regular }}>
            Verify your identity to raise your limits and unlock every Zitch feature.
          </Text>

          {status && (
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: c.surface3, borderRadius: 16, padding: 16, marginTop: 12 }}>
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

          {needsUpgrade ? (
            <>
              <View style={{ flexDirection: 'row', gap: 10, padding: 14, borderRadius: 14, backgroundColor: 'rgba(45,127,249,.10)', marginTop: 14 }}>
                <ZIcon name="help" size={16} color={C_NIN} />
                <Text style={{ flex: 1, fontSize: 12.5, color: c.ink2, lineHeight: 19, fontFamily: font.regular }}>
                  Your Zitch account number is already open, so your bank needs your
                  BVN, NIN and a selfie together in one step. It can&apos;t take them
                  one at a time any more.
                </Text>
              </View>
              <MethodCard id="upgrade" icon="insurance" color={C_BVN} title="Verify identity" sub="BVN, NIN and a selfie · about a minute" badge="Finish" />
            </>
          ) : (
            <>
              <MethodCard id="bvn" icon="insurance" color={C_BVN} title="BVN verification" sub="Fastest · Bank Verification Number" badge="Recommended" done={!!status?.bvn_verified} />
              <MethodCard id="nin" icon="card" color={C_NIN} title="NIN verification" sub="National ID number + photo of your slip" done={!!status?.nin_verified} />
            </>
          )}
          <MethodCard id="selfie" icon="user" color={C_SELFIE} title="Selfie verification" sub="Quick liveness check with your camera" done={!!status?.face_verified} />
          <Footer />
        </View>
      )}

      {method === 'bvn' && !bvnSent && (
        <View>
          <Hero icon="insurance" color={C_BVN} title="BVN verification" sub="Enter your 11-digit BVN. We'll send a code to the phone number linked to it." />
          <View style={{ marginTop: 22 }}>
            <Field label="Bank Verification Number (BVN)" placeholder="Enter your 11-digit BVN" keyboardType="number-pad" value={bvn} onChangeText={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))} prefix={<ZIcon name="insurance" size={18} color={c.ink3} />} />
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }}>
              <ZIcon name="help" size={14} color={c.amber} />
              <Text style={{ flex: 1, color: c.ink3, fontSize: 12.5, fontFamily: font.regular }}>Dial *565*0# on your registered line to get your BVN.</Text>
            </View>
            <View style={{ height: 22 }} />
            <Btn label={busy ? 'Sending code…' : 'Send verification code'} disabled={busy || bvn.length !== 11} onPress={startBvn} />
          </View>
          <Footer />
        </View>
      )}

      {method === 'bvn' && bvnSent && (
        <View>
          <Hero icon="insurance" color={C_BVN} title="Confirm your BVN" sub="Enter the 6-digit code we sent to the phone linked to your BVN." />
          <View style={{ marginTop: 22 }}>
            <Field label="Verification code" placeholder="6-digit code" keyboardType="number-pad" value={bvnOtp} onChangeText={(v) => setBvnOtp(v.replace(/\D/g, '').slice(0, 6))} prefix={<ZIcon name="lock" size={18} color={c.ink3} />} />
            <Text onPress={() => { setBvnSent(false); setBvnOtp(''); }} style={{ fontSize: 12.5, color: c.brand, marginTop: 10, fontFamily: font.semibold }}>Change BVN</Text>
            <View style={{ height: 22 }} />
            <Btn label={busy ? 'Confirming…' : 'Confirm BVN'} disabled={busy || bvnOtp.length !== 6} onPress={confirmBvn} />
          </View>
          <Footer />
        </View>
      )}

      {method === 'nin' && !ninSent && (
        <View>
          <Hero icon="card" color={C_NIN} title="NIN verification" sub="Enter your 11-digit NIN. Wema will send a code to the phone number registered on your NIN." />
          <View style={{ marginTop: 22 }}>
            <Field label="National Identification Number (NIN)" placeholder="Enter your 11-digit NIN" keyboardType="number-pad" value={nin} onChangeText={(v) => setNin(v.replace(/\D/g, '').slice(0, 11))} prefix={<ZIcon name="card" size={18} color={c.ink3} />} />
            <View style={{ height: 12 }} />
            <Tap onPress={pickNinSlip}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 14, borderWidth: 1.5, borderStyle: 'dashed', borderColor: ninImage ? C_BVN : c.line, backgroundColor: ninImage ? 'rgba(15,162,149,.08)' : c.surface2 }}>
                <View style={{ width: 40, height: 40, borderRadius: 11, backgroundColor: ninImage ? 'rgba(15,162,149,.16)' : c.surface3, alignItems: 'center', justifyContent: 'center' }}>
                  <ZIcon name={ninImage ? 'check' : 'plus'} size={20} color={ninImage ? C_BVN : c.ink3} stroke={2.4} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontSize: 14, fontFamily: font.semibold, color: c.ink1 }}>{ninImage ? 'NIN_slip.jpg' : 'Upload NIN slip if available'}</Text>
                  <Text style={{ fontSize: 12, color: c.ink3, marginTop: 1, fontFamily: font.regular }}>{ninImage ? 'Tap to replace' : 'Optional support document'}</Text>
                </View>
              </View>
            </Tap>
            <View style={{ height: 18 }} />
            <Btn label={busy ? 'Requesting code…' : 'Send NIN verification code'} disabled={busy || nin.length !== 11} onPress={verifyNin} />
          </View>
          <Footer />
        </View>
      )}

      {method === 'nin' && ninSent && (
        <View>
          <Hero icon="card" color={C_NIN} title="Confirm your NIN" sub="Enter the 6-digit code Wema sent to the phone number registered on your NIN." />
          <View style={{ marginTop: 22 }}>
            <Field label="Verification code" placeholder="6-digit code" keyboardType="number-pad" value={ninOtp} onChangeText={(v) => setNinOtp(v.replace(/\D/g, '').slice(0, 6))} prefix={<ZIcon name="lock" size={18} color={c.ink3} />} />
            <Text onPress={() => { setNinSent(false); setNinOtp(''); setNinTrackingId(''); }} style={{ fontSize: 12.5, color: c.brand, marginTop: 10, fontFamily: font.semibold }}>Change NIN</Text>
            <View style={{ height: 22 }} />
            <Btn label={busy ? 'Confirming…' : 'Confirm NIN'} disabled={busy || ninOtp.length !== 6 || !ninTrackingId} onPress={confirmNin} />
          </View>
          <Footer />
        </View>
      )}

      {method === 'upgrade' && (
        <View>
          <Hero icon="insurance" color={C_BVN} title="Verify identity"
                sub="Your bank needs your BVN, NIN and a live selfie together. Nothing is sent until all three are here." />
          <View style={{ marginTop: 22 }}>
            <Field label="Bank Verification Number (BVN)" placeholder="Enter your 11-digit BVN" keyboardType="number-pad"
                   value={upBvn} onChangeText={(v) => setUpBvn(v.replace(/\D/g, '').slice(0, 11))}
                   prefix={<ZIcon name="insurance" size={18} color={c.ink3} />} />
            {status?.bvn_verified && (
              <Text style={{ fontSize: 12, color: c.ink3, marginTop: 6, lineHeight: 18, fontFamily: font.regular }}>
                Already verified with us — your bank still needs it inside this one
                request, so enter it once more. We don&apos;t re-store it.
              </Text>
            )}
            <View style={{ height: 14 }} />
            <Field label="National Identification Number (NIN)" placeholder="Enter your 11-digit NIN" keyboardType="number-pad"
                   value={upNin} onChangeText={(v) => setUpNin(v.replace(/\D/g, '').slice(0, 11))}
                   prefix={<ZIcon name="card" size={18} color={c.ink3} />} />
            <View style={{ height: 14 }} />
            <Tap onPress={captureUpgradeSelfie}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 14, borderWidth: 1.5, borderStyle: 'dashed', borderColor: upSelfie ? C_BVN : c.line, backgroundColor: upSelfie ? 'rgba(15,162,149,.08)' : c.surface2 }}>
                <View style={{ width: 40, height: 40, borderRadius: 11, backgroundColor: upSelfie ? 'rgba(15,162,149,.16)' : c.surface3, alignItems: 'center', justifyContent: 'center' }}>
                  <ZIcon name={upSelfie ? 'check' : 'user'} size={20} color={upSelfie ? C_BVN : c.ink3} stroke={2.4} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontSize: 14, fontFamily: font.semibold, color: c.ink1 }}>{upSelfie ? 'Selfie captured' : 'Take a live selfie'}</Text>
                  <Text style={{ fontSize: 12, color: c.ink3, marginTop: 1, fontFamily: font.regular }}>{upSelfie ? 'Tap to retake' : 'Front camera · required by your bank'}</Text>
                </View>
              </View>
            </Tap>
            <View style={{ height: 22 }} />
            <Btn label={busy ? 'Verifying…' : 'Finish verification'}
                 disabled={busy || upBvn.length !== 11 || upNin.length !== 11 || !upSelfie}
                 onPress={submitUpgrade} />
          </View>
          <Footer />
        </View>
      )}

      {method === 'selfie' && (
        <View>
          <Hero icon="user" color={C_SELFIE} title="Selfie verification" sub="Hold your phone at eye level and keep your face inside the circle." />
          <View style={{ alignItems: 'center', marginVertical: 22 }}>
            <View style={{ width: 180, height: 180, borderRadius: 90, backgroundColor: c.surface2, borderWidth: 2, borderStyle: 'dashed', borderColor: '#8FDDD4', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
              <ZIcon name="user" size={92} color={c.ink3} stroke={1.5} />
              {scanning && (
                <Animated.View style={{ position: 'absolute', width: 180, height: 180, transform: [{ rotate }] }}>
                  <Svg width={180} height={180} viewBox="0 0 180 180">
                    <Circle cx={90} cy={90} r={84} fill="none" stroke="rgba(15,162,149,.20)" strokeWidth={4} />
                    <Circle cx={90} cy={90} r={84} fill="none" stroke={C_BVN} strokeWidth={4} strokeLinecap="round" strokeDasharray="132 528" />
                  </Svg>
                </Animated.View>
              )}
            </View>
          </View>
          <Text style={{ textAlign: 'center', fontSize: 13, color: scanning ? c.brand : c.ink3, fontFamily: scanning ? font.bold : font.regular, marginBottom: 14 }}>
            {scanning ? 'Checking liveness…' : 'Front camera · no Face ID needed'}
          </Text>
          <Btn label={scanning ? 'Verifying…' : 'Start camera'} disabled={scanning || busy} onPress={runSelfie} />
          <Footer />
        </View>
      )}
    </Screen>
  );
};

export default Kyc;
