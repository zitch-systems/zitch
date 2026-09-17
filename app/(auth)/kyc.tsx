import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Animated, Easing, Pressable } from 'react-native';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import * as ImagePicker from 'expo-image-picker';
import * as WebBrowser from 'expo-web-browser';
import Svg, { Circle } from 'react-native-svg';
import { notify } from '@/components/design/Notify';
import { getToken } from '@/lib/secureStore';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { classifyKycResponse, isAccountOtpPending, kycService, resolveIdentityOtpRoute, type KycStatus, type KycVerificationFlag, type ResidentialAddress } from '@/lib/services/kyc';
import type { VirtualAccount } from '@/lib/services/wallet';
import FaceLivenessModal from '@/components/design/FaceLivenessModal';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Tap, money } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

type Status = {
  tier: number; transaction_limit: string;
  bvn_verified: boolean; nin_verified: boolean; face_verified: boolean;
  message?: string;
  address_verified?: boolean;
  pending?: boolean;
  identity_review_required?: boolean;
  id_document_verified?: boolean;
  identity_face_available?: boolean;
  identity_verification_methods?: string[];
  face_rail?: 'document' | 'wema';
  tier2_face_rail?: 'prembly' | 'wema';
  address_rail?: 'document' | 'wema';
  // Set once the bank has opened the account number: from then on it will not
  // accept a lone BVN or NIN, only all of it at once. Read here so the menu can
  // send the customer straight to the step that works.
  identity_upgrade_required?: boolean;
};

type Method = 'menu' | 'bvn' | 'nin' | 'selfie' | 'upgrade' | 'address';

// Method accent colours — EXACT per the design handoff.
const C_BVN = '#0FA295';
const C_NIN = '#2D7FF9';
const C_SELFIE = '#7A5CFF';

const EMPTY_ADDRESS: ResidentialAddress = {
  buildingNumber: '', apartment: '', street: '', city: '', town: '', state: '',
  lga: '', lcda: '', landmark: '', additionalInformation: '', country: 'Nigeria',
  fullAddress: '', postalCode: '',
};

const cardShadow = {
  shadowColor: '#063731',
  shadowOpacity: 0.12,
  shadowRadius: 16,
  shadowOffset: { width: 0, height: 8 },
  elevation: 3,
};

const Kyc = () => {
  const { c } = useTheme();
  const params = useLocalSearchParams<{
    pending_identity?: string;
    pending_tracking_id?: string;
    pending_otp_destination?: string;
  }>();
  const [, setToken] = useState('');
  const [status, setStatus] = useState<Status | null>(null);
  const [method, setMethod] = useState<Method>('menu');
  const [bvn, setBvn] = useState('');
  const [bvnOtp, setBvnOtp] = useState('');
  const [bvnSent, setBvnSent] = useState(false);
  const [bvnTrackingId, setBvnTrackingId] = useState('');
  const [bvnOtpDestination, setBvnOtpDestination] = useState('');
  const [nin, setNin] = useState('');
  const [ninOtp, setNinOtp] = useState('');
  const [ninTrackingId, setNinTrackingId] = useState('');
  const [ninOtpDestination, setNinOtpDestination] = useState('');
  const [ninSent, setNinSent] = useState(false);
  const [ninImage, setNinImage] = useState(''); // base64 of the NIN slip
  // Combined existing-account upgrade. The bank scores all three together, so
  // they are collected before anything is sent — a partial submission is just a
  // refusal with the customer's identity already handed over.
  const [upBvn, setUpBvn] = useState('');
  const [upNin, setUpNin] = useState('');
  const [upSelfie, setUpSelfie] = useState('');
  const [busy, setBusy] = useState(false);
  const [address, setAddress] = useState<ResidentialAddress>(EMPTY_ADDRESS);
  const [addressDocument, setAddressDocument] = useState('');
  const [upgradeCameraOpen, setUpgradeCameraOpen] = useState(false);
  const [scanning, setScanning] = useState(false); // camera guide animation running
  const spin = useRef(new Animated.Value(0)).current;

  // Add-money can discover that the bank's pending attempt belongs to NIN
  // rather than the BVN form that started face verification. Resume that exact
  // server-owned attempt here so the confirmation and resend actions use the
  // matching identity route.
  useEffect(() => {
    const identity = params.pending_identity === 'nin' || params.pending_identity === 'bvn'
      ? params.pending_identity
      : '';
    const trackingId = params.pending_tracking_id || '';
    if (!identity || !trackingId) return;
    const destination = params.pending_otp_destination || '';
    if (identity === 'bvn') {
      setBvnTrackingId(trackingId);
      setBvnOtpDestination(destination);
      setBvnOtp('');
      setBvnSent(true);
      setMethod('bvn');
    } else {
      setNinTrackingId(trackingId);
      setNinOtpDestination(destination);
      setNinOtp('');
      setNinSent(true);
      setMethod('nin');
    }
  }, [params.pending_identity, params.pending_otp_destination, params.pending_tracking_id]);

  const load = useCallback(async (): Promise<Status | null> => {
    const t = await getToken();
    if (!t) return null;
    setToken(t);
    try {
      const res = await kycService.getStatus();
      if (res.success) {
        setStatus(res);
        return res;
      }
    } catch { /* keep */ }
    return null;
  }, []);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  // Return to the method menu and clear any in-flight sub-flow state.
  const goMenu = () => {
    setMethod('menu');
    setBvnSent(false);
    setBvnTrackingId(''); setBvnOtpDestination('');
    setNinSent(false);
    setNinTrackingId(''); setNinOtpDestination('');
    setUpBvn(''); setUpNin(''); setUpSelfie('');
    setAddress(EMPTY_ADDRESS);
    setAddressDocument('');
    setUpgradeCameraOpen(false);
    setScanning(false);
    setBusy(false);
    spin.stopAnimation();
  };
  const onBack = method === 'menu' ? () => router.back() : goMenu;
  // Both identities are still outstanding to the BANK in this state even when
  // one is verified with us, because the upgrade request carries both.
  const needsUpgrade = !!status?.identity_upgrade_required
    && !(status?.bvn_verified && status?.nin_verified);
  const setAddressField = (field: keyof ResidentialAddress, value: string) =>
    setAddress((current) => ({ ...current, [field]: value }));

  // Shared submit: on success update tier status, toast the design copy, reset
  // the sub-flow fields and bounce back to the menu.
  const submit = async (call: () => Promise<KycStatus | VirtualAccount>, successTitle: string, requiredFlags: KycVerificationFlag[] = []) => {
    setBusy(true);
    try {
      const res = await call();
      const outcome = classifyKycResponse(res, requiredFlags);
      if (outcome === 'pending') {
        notify('Verification processing', res.message || 'The verification service is still processing this submission. Check your status again shortly.', 'info');
      } else if (outcome === 'review') {
        notify('Verification needs review', res.message || 'Your account opened, but the verification service needs to review the identity details.', 'info');
        setBvn(''); setBvnOtp(''); setBvnSent(false); setBvnTrackingId(''); setBvnOtpDestination('');
        setNin(''); setNinOtp(''); setNinTrackingId(''); setNinOtpDestination(''); setNinSent(false); setNinImage('');
        setUpBvn(''); setUpNin(''); setUpSelfie('');
        setAddressDocument('');
        setAddress(EMPTY_ADDRESS);
        setMethod('menu');
        await load();
      } else if (outcome === 'success' || outcome === 'unverified') {
        const authoritative = requiredFlags.length ? await load() : null;
        const verified = !requiredFlags.length || requiredFlags.every((flag) => authoritative?.[flag] === true);
        if (!verified) {
          notify(
            authoritative?.identity_review_required ? 'Verification needs review' : 'Verification submitted',
            authoritative?.message || (authoritative?.identity_review_required
              ? 'The verification service needs to review the identity details.'
              : authoritative?.pending
                ? 'The verification service is still processing this submission. Check your status again shortly.'
                : 'Your submission was received. Check your identity status for the result.'),
            'info',
          );
          setMethod('menu');
          return;
        }
        notify(successTitle, undefined, 'success');
        setBvn(''); setBvnOtp(''); setBvnSent(false); setBvnTrackingId(''); setBvnOtpDestination('');
        setNin(''); setNinOtp(''); setNinTrackingId(''); setNinOtpDestination(''); setNinSent(false); setNinImage('');
        setUpBvn(''); setUpNin(''); setUpSelfie('');
        setAddressDocument('');
        setAddress(EMPTY_ADDRESS);
        setMethod('menu');
        await load();
      } else notify('Error', res.message || 'Verification failed');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

  // --- BVN: enter number -> we send a one-time code -> confirm it ---
  const startBvn = async () => {
    setBusy(true);
    try {
      const res = await kycService.startBvn(bvn);
      if (res.pending) {
        notify('Verification processing', res.message || 'Your BVN verification is still processing. Check your status again shortly.', 'info');
      } else if (res.upgrade_required) {
        setUpBvn(bvn);
        setMethod('upgrade');
      } else if (res.otp_required) {
        if (!res.tracking_id) {
          notify('Error', 'The verification request did not return a tracking reference. Please try again.');
        } else {
          setBvnTrackingId(String(res.tracking_id));
          setBvnOtpDestination(res.delivery || res.otp_destination || '');
          setBvnSent(true);
          notify('BVN code requested', res.message || 'Enter the verification code to finish.', 'success');
        }
      } else if (res.identity_review_required) {
        notify('Verification needs review', res.message || 'Your account opened, but your BVN needs review.', 'info');
        await load();
        setBvn(''); setBvnOtp(''); setBvnTrackingId(''); setBvnOtpDestination(''); setBvnSent(false);
        setMethod('menu');
      } else if (res.success) {
        const authoritative = await load();
        notify(
          authoritative?.bvn_verified ? 'BVN already verified' : 'Verification submitted',
          authoritative?.bvn_verified
            ? (res.message || 'Your BVN is already verified.')
            : (authoritative?.message || 'Your BVN submission was received. Check your identity status for the result.'),
          authoritative?.bvn_verified ? 'success' : 'info',
        );
        setBvn(''); setBvnOtp(''); setBvnTrackingId(''); setBvnOtpDestination(''); setBvnSent(false);
        setMethod('menu');
      }
      else notify('Error', res.message || 'Could not start BVN verification');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };
  const resendBvn = async () => {
    if (!bvnTrackingId) return;
    setBusy(true);
    try {
      const res = await kycService.resendBvn(bvnTrackingId);
      if (res.success) {
        setBvnOtpDestination(res.otp_destination || '');
        notify('BVN code resent', res.message || 'Enter the latest verification code.', 'success');
      } else if (res.pending) {
        notify('Verification processing', res.message || 'Your BVN verification is still processing. Check your status again shortly.', 'info');
      } else notify('Error', res.message || 'Could not resend the BVN code');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };
  const confirmBvn = () => submit(() => kycService.confirmBvn(bvnTrackingId, bvnOtp), 'BVN verified — tier upgraded', ['bvn_verified']);

  const startIdentityFaceVerification = async (identity: { bvn?: string; nin?: string }) => {
    setBusy(true);
    try {
      const started = await kycService.startIdentityFace(identity);
      const otpRoute = resolveIdentityOtpRoute(started, identity.bvn ? 'bvn' : 'nin');
      if (otpRoute) {
        // Face-start can return the existing bank OTP attempt instead of a URL.
        // Keep the server's KYC flags and tracking reference, then continue in
        // the matching confirmation screen so the bank owns the identity state.
        setStatus((current) => current ? { ...current, ...started } : started);
        if (otpRoute.kind === 'bvn') {
          setBvnTrackingId(otpRoute.trackingId);
          setBvnOtpDestination(started.delivery || started.otp_destination || '');
          setBvnOtp('');
          setBvnSent(true);
          setMethod('bvn');
        } else {
          setNinTrackingId(otpRoute.trackingId);
          setNinOtpDestination(started.delivery || started.otp_destination || '');
          setNinOtp('');
          setNinSent(true);
          setMethod('nin');
        }
        notify('SMS verification required', started.message || 'Enter the bank code sent to the phone registered on your identity.', 'info');
        return;
      }
      if (isAccountOtpPending(started)) {
        // Do not label this as a face outage when the server says the account
        // is waiting on SMS but did not provide a usable tracking reference.
        setStatus((current) => current ? { ...current, ...started } : started);
        notify('SMS verification pending', started.message || 'Your bank verification is waiting for an SMS code. Please start the verification again.', 'info');
        return;
      }
      if (started.pending) {
        notify('Verification processing', started.message || 'The verification service is still processing this request. Check your status again shortly.', 'info');
        return;
      }
      if (!started.success || !started.url || !started.session) {
        notify('Face verification unavailable', started.message || 'Please use the SMS code for now.');
        return;
      }
      beginExternalActivity();
      try { await WebBrowser.openBrowserAsync(started.url); }
      finally { endExternalActivity(); }
      for (let attempt = 0; attempt < 15; attempt += 1) {
        const result = await kycService.getIdentityFaceStatus(started.session);
        if (result.status === 'verified') {
          setStatus(result);
          setBvn(''); setBvnOtp(''); setBvnSent(false); setBvnTrackingId(''); setBvnOtpDestination('');
          setNin(''); setNinOtp(''); setNinTrackingId(''); setNinOtpDestination(''); setNinSent(false);
          setMethod('menu');
          notify('Identity verified', result.message || 'Your bank confirmed the face check.', 'success');
          return;
        }
        if (result.status === 'failed' || result.status === 'expired') {
          notify('Face verification incomplete', 'The bank did not confirm the check. You can retry or use SMS.');
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
      notify('Still processing', 'Your bank is still confirming the check. This page will refresh when you return.');
      await load();
    } catch { notify('Error', 'Could not complete face verification. Please use the SMS code or try again.'); }
    finally { setBusy(false); }
  };

  // --- NIN: enter number -> Wema sends a one-time code -> confirm it ---
  const startNin = async () => {
    setBusy(true);
    try {
      const res = await kycService.startNin(nin);
      if (res.pending) {
        notify('Verification processing', res.message || 'Your NIN verification is still processing. Check your status again shortly.', 'info');
      } else if (res.upgrade_required) {
        // Not an error the customer can retry out of: this account can only be
        // finished by the combined upgrade. Carry the NIN they already typed
        // across so they are not asked for it twice.
        setUpNin(nin);
        setMethod('upgrade');
      } else if (res.otp_required) {
        if (!res.tracking_id) {
          notify('Error', 'The verification request did not return a tracking reference. Please try again.');
        } else {
          setNinTrackingId(String(res.tracking_id));
          setNinOtpDestination(res.otp_destination || '');
          setNinSent(true);
          notify('NIN code requested', res.message || 'Enter the verification code to finish.', 'success');
        }
      } else if (res.identity_review_required) {
        notify('Verification needs review', res.message || 'Your account opened, but your NIN needs review.', 'info');
        await load();
        setNin(''); setNinOtp(''); setNinTrackingId(''); setNinOtpDestination(''); setNinSent(false);
        setMethod('menu');
      } else if (res.success) {
        const authoritative = await load();
        notify(
          authoritative?.nin_verified ? 'NIN verified' : 'Verification submitted',
          authoritative?.nin_verified
            ? (res.message || 'NIN verified successfully')
            : (authoritative?.message || 'Your NIN submission was received. Check your identity status for the result.'),
          authoritative?.nin_verified ? 'success' : 'info',
        );
        setNin(''); setNinOtp(''); setNinTrackingId(''); setNinOtpDestination(''); setNinSent(false);
        setMethod('menu');
      } else notify('Error', res.message || 'Could not start NIN verification');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };
  const confirmNin = () => submit(
    () => kycService.confirmNin(ninTrackingId, ninOtp, nin),
    'NIN verified — tier upgraded',
    ['nin_verified'],
  );
  const resendNin = async () => {
    if (!ninTrackingId) return;
    setBusy(true);
    try {
      const res = await kycService.resendNin(ninTrackingId);
      if (res.success) {
        setNinOtpDestination(res.otp_destination || '');
        notify('NIN code resent', res.message || 'Enter the latest verification code.', 'success');
      } else if (res.pending) {
        notify('Verification processing', res.message || 'Your NIN verification is still processing. Check your status again shortly.', 'info');
      } else notify('Error', res.message || 'Could not resend the NIN code');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

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

  // --- Selfie: a real captured image for server-side identity verification (NOT device
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
    submit(() => kycService.verifyFace(selfie), 'Selfie verification completed', ['face_verified']);
  };
  // Show a visible camera guide animation (~2.3s spin) THEN open the front camera.
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
  // --- Combined upgrade: BVN + NIN + a front-camera selfie, submitted together ---
  // The selfie is captured and HELD rather than posted, because the bank scores
  // it against the two numbers in one request. Posting it alone would spend the
  // verification attempt on a call that cannot complete the upgrade.
  const captureUpgradeSelfie = () => setUpgradeCameraOpen(true);
  const submitUpgrade = () => submit(
    () => kycService.upgradeTier2(upBvn, upNin, upSelfie),
    'Identity verified — tier upgraded',
    ['bvn_verified', 'nin_verified', 'face_verified'],
  );

  const pickAddressProof = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) { notify('Photos needed', 'Allow photo access to upload your proof of address.'); return; }
    beginExternalActivity();
    try {
      const res = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images, base64: true, quality: 0.4, allowsEditing: true,
      });
      if (!res.canceled && res.assets?.[0]?.base64) setAddressDocument(res.assets[0].base64);
    } finally { endExternalActivity(); }
  };

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
    <Tap onPress={() => { if (!done) setMethod(id); }} style={{ marginTop: 12 }}>
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
    <>
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
              <MethodCard id="nin" icon="card" color={C_NIN} title="NIN verification" sub="Tier 1: National Identification Number" done={!!status?.nin_verified} />
            </>
          )}
          {status?.bvn_verified && status?.nin_verified && (
              <MethodCard id="selfie" icon="user" color={C_SELFIE} title="Selfie verification" sub="Tier 2: verification service review" done={!!status?.face_verified} />
          )}
          {status?.tier !== undefined && status.tier >= 2 && (
            <MethodCard id="address" icon="home" color={C_BVN} title="Address verification" sub="Tier 3" done={!!status?.address_verified} />
          )}
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
          <Hero icon="insurance" color={C_BVN} title="Confirm your BVN" sub={`Enter the 6-digit code${bvnOtpDestination ? ` sent to ${bvnOtpDestination}` : ' sent for your BVN'}.`} />
          <View style={{ marginTop: 22 }}>
            <Field label="Verification code" placeholder="6-digit code" keyboardType="number-pad" value={bvnOtp} onChangeText={(v) => setBvnOtp(v.replace(/\D/g, '').slice(0, 6))} prefix={<ZIcon name="lock" size={18} color={c.ink3} />} />
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 10 }}>
              <Text onPress={() => { setBvnSent(false); setBvnOtp(''); setBvnTrackingId(''); setBvnOtpDestination(''); }} style={{ fontSize: 12.5, color: c.brand, fontFamily: font.semibold }}>Change BVN</Text>
              <Text onPress={() => void resendBvn()} style={{ fontSize: 12.5, color: busy ? c.ink3 : c.brand, fontFamily: font.semibold }}>Resend code</Text>
            </View>
            <View style={{ height: 22 }} />
            <Btn label={busy ? 'Confirming…' : 'Confirm BVN'} disabled={busy || bvnOtp.length !== 6 || !bvnTrackingId} onPress={confirmBvn} />
            {status?.identity_face_available && (
              <View style={{ marginTop: 12 }}>
                <Btn label={busy ? 'Opening face verification…' : 'Use face verification instead'} variant="ghost" disabled={busy || bvn.length !== 11} onPress={() => startIdentityFaceVerification({ bvn })} />
              </View>
            )}
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
          <Hero icon="card" color={C_NIN} title="Confirm your NIN" sub={`Enter the 6-digit code${ninOtpDestination ? ` sent to ${ninOtpDestination}` : ' sent for your NIN'}.`} />
          <View style={{ marginTop: 22 }}>
            <Field label="Verification code" placeholder="6-digit code" keyboardType="number-pad" value={ninOtp} onChangeText={(v) => setNinOtp(v.replace(/\D/g, '').slice(0, 6))} prefix={<ZIcon name="lock" size={18} color={c.ink3} />} />
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 10 }}>
              <Text onPress={() => { setNinSent(false); setNinOtp(''); setNinTrackingId(''); setNinOtpDestination(''); }} style={{ fontSize: 12.5, color: c.brand, fontFamily: font.semibold }}>Change NIN</Text>
              <Text onPress={() => void resendNin()} style={{ fontSize: 12.5, color: busy ? c.ink3 : c.brand, fontFamily: font.semibold }}>Resend code</Text>
            </View>
            <View style={{ height: 22 }} />
            <Btn label={busy ? 'Confirming…' : 'Confirm NIN'} disabled={busy || ninOtp.length !== 6 || !ninTrackingId} onPress={confirmNin} />
            {status?.identity_face_available && (
              <View style={{ marginTop: 12 }}>
                <Btn label={busy ? 'Opening face verification…' : 'Use face verification instead'} variant="ghost" disabled={busy || nin.length !== 11} onPress={() => startIdentityFaceVerification({ nin })} />
              </View>
            )}
          </View>
          <Footer />
        </View>
      )}

      {method === 'upgrade' && (
        <View>
          <Hero icon="insurance" color={C_BVN} title="Verify identity"
                sub="Your verification service needs your BVN, NIN and a front-camera selfie together. Nothing is sent until all three are here." />
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
                  <Text style={{ fontSize: 14, fontFamily: font.semibold, color: c.ink1 }}>{upSelfie ? 'Selfie captured' : 'Take a selfie'}</Text>
                  <Text style={{ fontSize: 12, color: c.ink3, marginTop: 1, fontFamily: font.regular }}>{upSelfie ? 'Tap to retake' : 'Front camera · required for verification'}</Text>
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

      {method === 'address' && (
        <View>
          <Hero icon="home" color={C_BVN} title="Address verification" sub="Tier 3" />
          <Field label="Building number" value={address.buildingNumber} onChangeText={(value) => setAddressField('buildingNumber', value)} />
          <Field label="Apartment / flat (optional)" value={address.apartment} onChangeText={(value) => setAddressField('apartment', value)} />
          <Field label="Street" value={address.street} onChangeText={(value) => setAddressField('street', value)} />
          <Field label="City" value={address.city} onChangeText={(value) => setAddressField('city', value)} />
          <Field label="Town (optional)" value={address.town} onChangeText={(value) => setAddressField('town', value)} />
          <Field label="State" value={address.state} onChangeText={(value) => setAddressField('state', value)} />
          <Field label="LGA" value={address.lga} onChangeText={(value) => setAddressField('lga', value)} />
          <Field label="LCDA (optional)" value={address.lcda} onChangeText={(value) => setAddressField('lcda', value)} />
          <Field label="Landmark (optional)" value={address.landmark} onChangeText={(value) => setAddressField('landmark', value)} />
          <Field label="Additional information (optional)" value={address.additionalInformation} onChangeText={(value) => setAddressField('additionalInformation', value)} />
          <Field label="Postal code (optional)" keyboardType="number-pad" value={address.postalCode} onChangeText={(value) => setAddressField('postalCode', value)} />
          <Pressable onPress={() => void pickAddressProof()} disabled={busy} style={{ flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 14, borderWidth: 1.5, borderStyle: 'dashed', borderColor: addressDocument ? C_BVN : c.line, backgroundColor: addressDocument ? 'rgba(15,162,149,.08)' : c.surface2, marginBottom: 12 }}>
            <ZIcon name={addressDocument ? 'check' : 'file'} size={20} color={addressDocument ? C_BVN : c.ink3} />
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 14, fontFamily: font.semibold, color: c.ink1 }}>{addressDocument ? 'Proof of address selected' : 'Add proof of address'}</Text>
              <Text style={{ fontSize: 12, color: c.ink3, marginTop: 1, fontFamily: font.regular }}>{status?.address_rail === 'wema' ? 'Optional for bank verification' : 'Utility bill or bank statement'}</Text>
            </View>
            <ZIcon name="right" size={17} color={c.ink3} />
          </Pressable>
          <Btn label={busy ? 'Verifying...' : 'Verify address'} disabled={busy || !address.buildingNumber.trim() || !address.street.trim() || !address.city.trim() || !address.state.trim() || !address.lga.trim()}
            onPress={() => submit(() => kycService.verifyAddress(address, addressDocument), 'Address verified', ['address_verified'])} />
        </View>
      )}

      {method === 'selfie' && (
        <View>
          <Hero icon="user" color={C_SELFIE} title="Selfie verification" sub="Hold your phone at eye level and keep your face inside the circle. The verification service reviews the capture after submission." />
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
            {scanning ? 'Preparing camera…' : 'Front camera · no Face ID needed'}
          </Text>
          <Btn label={scanning ? 'Verifying…' : 'Start camera'} disabled={scanning || busy} onPress={runSelfie} />
          <Footer />
        </View>
      )}
    </Screen>
    <FaceLivenessModal
      visible={upgradeCameraOpen}
      onClose={() => setUpgradeCameraOpen(false)}
      onCapture={(image) => { setUpgradeCameraOpen(false); setUpSelfie(image); }}
    />
    </>
  );
};

export default Kyc;
