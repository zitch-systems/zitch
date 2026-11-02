import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Image, Text, View } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import * as ImagePicker from 'expo-image-picker';
import AuthGuard from '@/components/AuthGuard';
import { Screen, Header, Field, Btn, PillTabs, Card } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { banking, type BankProfile, type BankStatus } from '@/lib/services/banking';
import { kycService } from '@/lib/services/kyc';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { getSessionGeneration } from '@/lib/secureStore';

const emptyProfile: BankProfile = { lastName: '', otherNames: '', phoneNo: '', email: '', dateOfBirth: '', address: '' };
const RESEND_SECONDS = 60;

/** Digits only, shaped to DD/MM/YYYY while typing. */
const maskDob = (value: string) => {
  const d = value.replace(/\D/g, '').slice(0, 8);
  return d.length > 4 ? `${d.slice(0, 2)}/${d.slice(2, 4)}/${d.slice(4)}` : d.length > 2 ? `${d.slice(0, 2)}/${d.slice(2)}` : d;
};
const validDob = (value: string) => {
  const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);
  if (!m) return false;
  const [day, month, year] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const date = new Date(year, month - 1, day);
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day && date < new Date() && year >= 1900;
};
const digits = (value: string, max: number) => value.replace(/\D/g, '').slice(0, max);

function KycScreenContent() {
  const { c } = useTheme();
  const [status, setStatus] = useState<BankStatus | null>(null);
  const [profile, setProfile] = useState<BankProfile>(emptyProfile);
  // BVN is the default ID. Details come first, then one page for the ID and its code.
  const [kind, setKind] = useState<'bvn' | 'nin'>('bvn');
  const [page, setPage] = useState<'details' | 'id'>('details');
  const [step, setStep] = useState<'details' | 'face'>('details');
  const [photo, setPhoto] = useState('');
  const [identifier, setIdentifier] = useState('');
  const [tracking, setTracking] = useState('');
  const [otp, setOtp] = useState('');
  const [needsOtp, setNeedsOtp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [resendAt, setResendAt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [email, setEmail] = useState('');
  const [emailCode, setEmailCode] = useState('');
  const [emailSent, setEmailSent] = useState(false);
  const generation = useRef(getSessionGeneration());
  const active = useRef(true);
  const ownership = useRef(0);
  const readSerial = useRef(0);
  const inFlight = useRef(false);
  const current = (owner = ownership.current) => active.current && owner === ownership.current && generation.current === getSessionGeneration();
  const text = { color: c.ink1, fontFamily: font.regular, lineHeight: 22 };
  const heading = { color: c.ink1, fontFamily: font.bold, fontSize: 17 };
  const waitSeconds = Math.max(0, Math.ceil((resendAt - now) / 1000));
  const counting = waitSeconds > 0;
  useEffect(() => {
    if (!counting) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [counting]);
  const codeSent = () => { setResendAt(Date.now() + RESEND_SECONDS * 1000); setNow(Date.now()); };
  const load = async () => {
    const owner = ownership.current, serial = ++readSerial.current;
    try {
      const result = await banking.status();
      if (!current(owner) || serial !== readSerial.current) return;
      if (result.success) {
        setStatus(result); setMessage(''); setProfile((p) => ({ ...p, ...result.profile }));
        setEmail(result.profile.email || '');
        if (result.email_verified) { setEmailCode(''); setEmailSent(false); }
        const challenge = result.identity_challenge;
        setTracking(challenge?.tracking_id || ''); setNeedsOtp(challenge?.otp_required === true);
        if (challenge) { setKind(challenge.kind); setPage('id'); }
        // An existing account already holds the details: go straight to the ID.
        else if (result.account_number) { setPage('id'); setKind(result.bvn_verified && !result.nin_verified ? 'nin' : 'bvn'); }
      }
      else { setStatus(null); setMessage(result.message || 'Could not load verification status.'); }
    } catch { if (current(owner) && serial === readSerial.current) { setStatus(null); setMessage('Could not load verification status. Please try again.'); } }
  };
  useFocusEffect(useCallback(() => {
    generation.current = getSessionGeneration(); active.current = true; void load();
    return () => {
      active.current = false; ownership.current += 1; inFlight.current = false;
      setBusy(false); setStatus(null); setIdentifier(''); setOtp(''); setTracking(''); setNeedsOtp(false);
      setProfile(emptyProfile); setEmail(''); setEmailCode(''); setEmailSent(false); setMessage('');
      setStep('details'); setPage('details'); setPhoto(''); setResendAt(0);
    };
  }, []));
  const verifyEmail = async (confirm: boolean) => {
    if (!current() || inFlight.current) return;
    if (confirm ? !/^[0-9]{6}$/.test(emailCode) : !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) return;
    const owner = ownership.current;
    inFlight.current = true; setBusy(true);
    try {
      const result = confirm ? await kycService.confirmEmail(emailCode) : await kycService.startEmail(email.trim());
      if (!current(owner)) return;
      setMessage(result.message || (result.success ? 'Check your email for a verification code.' : 'We could not verify your email. Please try again.'));
      if (result.success && confirm) { setEmailCode(''); setEmailSent(false); await load(); }
      else if (result.success) { setEmailSent(true); setEmailCode(''); }
    } catch {
      if (current(owner)) setMessage('Could not reach the verification service. Please try again.');
    } finally {
      if (current(owner)) { inFlight.current = false; setBusy(false); }
    }
  };
  const run = async (action: () => Promise<any>) => {
    if (!current() || inFlight.current) return;
    const owner = ownership.current;
    inFlight.current = true; setBusy(true);
    try {
      const result = await action();
      if (!current(owner) || !result) return;
      setMessage(result.success ? '' : result.message || (result.pending ? '' : 'We could not complete this request.'));
      if (result.tracking_id) {
        setTracking(result.tracking_id); setNeedsOtp(result.otp_required === true);
        if (result.otp_required === true) setOtp('');
      }
      if (['expired', 'failed'].includes(result.account_setup_state)) { setTracking(''); setNeedsOtp(false); setOtp(''); }
      if (result.success) { setOtp(''); setIdentifier(''); setTracking(''); setNeedsOtp(false); await load(); }
      return result;
    } catch { if (current(owner)) setMessage('Connection interrupted. Check the status before trying again.'); }
    finally { if (current(owner)) { inFlight.current = false; setBusy(false); } }
  };
  const sendCode = async () => {
    const result = await run(() => banking.identity(kind, identifier, profile));
    if (result?.otp_required) codeSent();
  };
  const resend = async () => {
    const result = await run(() => (identifier.length === 11 ? banking.identity(kind, identifier, profile) : banking.resend(tracking)));
    if (result?.otp_required) codeSent();
  };
  // The photo is taken on the face verification screen and only sent when the customer confirms it.
  const takePhoto = async () => {
    if (!identifier || !current() || inFlight.current) return;
    const owner = ownership.current;
    inFlight.current = true; setBusy(true);
    try {
      const permission = await ImagePicker.requestCameraPermissionsAsync();
      if (!current(owner)) return;
      if (!permission.granted) { notify('Camera access needed', 'Allow the camera to verify your face.'); return; }
      beginExternalActivity();
      let shot: ImagePicker.ImagePickerResult;
      try { shot = await ImagePicker.launchCameraAsync({ cameraType: ImagePicker.CameraType.front, base64: true, quality: 0.5 }); } finally { endExternalActivity(); }
      const data = !shot.canceled ? shot.assets[0]?.base64 : null;
      if (data && current(owner)) setPhoto(data);
    } catch { if (current(owner)) setMessage('Could not open the camera. Please try again.'); }
    finally { if (current(owner)) { inFlight.current = false; setBusy(false); } }
  };
  const submitFace = async () => {
    if (!photo || !identifier) return;
    const owner = ownership.current;
    const result = await run(() => banking.identity(kind, identifier, profile, photo));
    if (result && current(owner)) { setPhoto(''); setStep('details'); }
  };
  const notice = !!message && <Text accessibilityLiveRegion="polite" style={{ ...text, color: c.brand }}>{message}</Text>;

  if (status?.email_verified === false) return <Screen header={<Header title="Verify your email" />}>
    <View style={{ gap: 16 }}>
      {notice}
      <Field label="Email address" value={email} maxLength={254} keyboardType="email-address" autoCapitalize="none" autoCorrect={false}
        editable={!busy && !emailSent} onChangeText={setEmail} />
      {emailSent ? <>
        <Field label="Six-digit email code" value={emailCode} maxLength={6} keyboardType="number-pad" secureTextEntry
          editable={!busy} onChangeText={setEmailCode} />
        <Btn label="Confirm email code" disabled={busy || !/^[0-9]{6}$/.test(emailCode)} onPress={() => void verifyEmail(true)} />
        <Btn label="Resend email code" variant="outline" disabled={busy} onPress={() => void verifyEmail(false)} />
        <Btn label="Change email address" variant="ghost" disabled={busy} onPress={() => { setEmailSent(false); setEmailCode(''); setMessage(''); }} />
      </> : <Btn label="Send email code" disabled={busy || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())} onPress={() => void verifyEmail(false)} />}
    </View>
  </Screen>;
  if (!status || !status.identity_available) return <Screen header={<Header title="Verify your account" />}>
    <View style={{ gap: 16 }}><Text style={text}>{message || (!status ? 'Loading…' : 'Verification is unavailable for this account. Please contact support.')}</Text>
      <Btn label="Refresh verification status" variant="outline" onPress={() => void load()} /></View>
  </Screen>;
  if (step === 'face') return <Screen header={<Header title="Verify your face" onBack={busy ? undefined : () => { setPhoto(''); setStep('details'); }} />}>
    <View style={{ gap: 16, paddingBottom: 32 }}>
      {!!photo && <Image accessibilityLabel="Your photo" source={{ uri: `data:image/jpeg;base64,${photo}` }}
        style={{ width: 180, height: 180, borderRadius: 90, alignSelf: 'center', borderWidth: 3, borderColor: c.brand }} />}
      {notice}
      <Btn label={photo ? 'Retake photo' : 'Open camera'} variant={photo ? 'outline' : 'primary'} disabled={busy} onPress={() => void takePhoto()} />
      {!!photo && <Btn label={busy ? 'Please wait…' : 'Verify my face'} disabled={busy} onPress={() => void submitFace()} />}
      <Btn label="Back" variant="ghost" disabled={busy} onPress={() => { setPhoto(''); setStep('details'); }} />
    </View>
  </Screen>;

  const hasAccount = !!status.account_number;
  const dobError = profile.dateOfBirth.length === 10 && !validDob(profile.dateOfBirth) ? 'Enter a real date of birth' : undefined;
  const detailsReady = !!profile.otherNames.trim() && !!profile.lastName.trim() && !!profile.phoneNo.trim()
    && validDob(profile.dateOfBirth) && (profile.gender === 0 || profile.gender === 1) && !!profile.address.trim();
  const idReady = identifier.length === 11;
  const verified = kind === 'bvn' ? status.bvn_verified : status.nin_verified;

  if (page === 'details' && !hasAccount) return <Screen header={<Header title="Your details" />}>
    <View style={{ gap: 16, paddingBottom: 32 }}>
      {notice}
      <Field editable={!busy} label="First and middle names" value={profile.otherNames} onChangeText={(otherNames) => setProfile({ ...profile, otherNames })} />
      <Field editable={!busy} label="Surname" value={profile.lastName} onChangeText={(lastName) => setProfile({ ...profile, lastName })} />
      <Field editable={!busy && !status.profile?.phoneNo} label="Phone number" value={profile.phoneNo} keyboardType="phone-pad" onChangeText={(phoneNo) => setProfile({ ...profile, phoneNo })} />
      <Field editable={!busy} label="Date of birth" placeholder="DD/MM/YYYY" value={profile.dateOfBirth} keyboardType="number-pad" maxLength={10}
        error={dobError} onChangeText={(value) => setProfile({ ...profile, dateOfBirth: maskDob(value) })} />
      <View style={{ gap: 8 }}><Text style={heading}>Gender</Text>
        <PillTabs scroll={false} options={[{ v: '0', label: 'Male' }, { v: '1', label: 'Female' }]}
          value={profile.gender === 0 || profile.gender === 1 ? String(profile.gender) : ''}
          onChange={(value) => { if (!busy) setProfile({ ...profile, gender: Number(value) }); }} /></View>
      <Field editable={!busy} label="Home address" value={profile.address} maxLength={100} onChangeText={(address) => setProfile({ ...profile, address })} />
      <Btn label="Next" disabled={busy || !detailsReady} onPress={() => { setMessage(''); setPage('id'); }} />
    </View>
  </Screen>;

  return <Screen header={<Header title="Verify your identity" onBack={busy || hasAccount ? undefined : () => setPage('details')} />}>
    <View style={{ gap: 16, paddingBottom: 32 }}>
      {hasAccount && <Card>
        <Text style={heading}>{status.account_name}</Text>
        <Text style={{ ...text, marginTop: 4 }}>{`${status.account_number} · Tier ${status.bank_tier || 1}`}</Text>
      </Card>}
      <PillTabs scroll={false} options={[{ v: 'bvn', label: 'BVN' }, { v: 'nin', label: 'NIN' }]} value={kind}
        onChange={(value) => { if (busy || tracking) return; setKind(value as 'bvn' | 'nin'); setIdentifier(''); setMessage(''); }} />
      {verified && !tracking ? <Text style={{ ...text, fontFamily: font.bold }}>{`${kind.toUpperCase()} verified`}</Text> : <>
        <Field editable={!busy && !tracking} label={kind.toUpperCase()} placeholder="11 digits" value={identifier} maxLength={11} keyboardType="number-pad"
          secureTextEntry onChangeText={(value) => setIdentifier(digits(value, 11))} />
        {!tracking && <>
          <Btn label={busy ? 'Please wait…' : 'Send code'} disabled={busy || !idReady || (!hasAccount && !detailsReady)} onPress={() => void sendCode()} />
          <Btn label="Verify with my face instead" variant="ghost" disabled={busy || !idReady || (!hasAccount && !detailsReady)}
            onPress={() => { setMessage(''); setPhoto(''); setStep('face'); }} />
        </>}
        {needsOtp && <>
          <Field editable={!busy} label="Six-digit code" value={otp} maxLength={6} keyboardType="number-pad" secureTextEntry onChangeText={(value) => setOtp(digits(value, 6))} />
          <Btn label={busy ? 'Please wait…' : 'Verify'} disabled={busy || otp.length !== 6} onPress={() => void run(() => banking.confirm(tracking, otp))} />
          <Btn label={waitSeconds ? `Resend code in ${waitSeconds}s` : 'Resend code'} variant="ghost" disabled={busy || waitSeconds > 0} onPress={() => void resend()} />
        </>}
        {!!tracking && !needsOtp && <Btn label="Check status" variant="outline" disabled={busy} onPress={() => void run(() => banking.progress(tracking))} />}
      </>}
      {notice}
      {hasAccount && <Btn label="Upgrade account" variant="outline" disabled={busy} onPress={() => router.push('/upgrade')} />}
    </View>
  </Screen>;
}

export default function KycScreen() {
  return <AuthGuard><KycScreenContent /></AuthGuard>;
}
