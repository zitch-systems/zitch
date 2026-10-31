import React, { useCallback, useRef, useState } from 'react';
import { Image, Text, View } from 'react-native';
import { useFocusEffect } from 'expo-router';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system';
import AuthGuard from '@/components/AuthGuard';
import { Screen, Header, Field, Btn, PillTabs } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { banking, type BankProfile, type BankStatus } from '@/lib/services/banking';
import { kycService } from '@/lib/services/kyc';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { getSessionGeneration } from '@/lib/secureStore';

const emptyProfile: BankProfile = { lastName: '', otherNames: '', phoneNo: '', email: '', dateOfBirth: '', address: '' };
const upgradeFields = [
  ['bvn', 'BVN'], ['nin', 'NIN'],
  ['idNumber', 'ID number'], ['idIssueDate', 'ID issue date (YYYY-MM-DD)'], ['idExpiryDate', 'ID expiry date (optional for NIN)'],
  ['houseNumber', 'House number'], ['streetName', 'Street'], ['city', 'City'], ['state', 'State'],
  ['localGovernment', 'Local government area'], ['nearestLandmark', 'Nearest landmark'],
] as const;
const idTypes = [{ v: '1', label: 'National ID (NIN)' }, { v: '2', label: 'Driver’s licence' },
  { v: '3', label: 'Voter’s card' }, { v: '4', label: 'Passport' }];
const files = [['userPhoto', 'Passport photo', 100000], ['idCardFront', 'ID front', 100000],
  ['idCardBack', 'ID back', 100000], ['utilityBill', 'Utility bill', 50000], ['customerSignature', 'Signature', 680000]] as const;
const numericFields = new Set(['bvn', 'nin', 'idIssueDate', 'idExpiryDate']);

/** Digits only, shaped while typing: DD/MM/YYYY for a birth date, YYYY-MM-DD for ID dates. */
const maskDob = (value: string) => {
  const d = value.replace(/\D/g, '').slice(0, 8);
  return d.length > 4 ? `${d.slice(0, 2)}/${d.slice(2, 4)}/${d.slice(4)}` : d.length > 2 ? `${d.slice(0, 2)}/${d.slice(2)}` : d;
};
const maskIsoDate = (value: string) => {
  const d = value.replace(/\D/g, '').slice(0, 8);
  return d.length > 6 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d.length > 4 ? `${d.slice(0, 4)}-${d.slice(4)}` : d;
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
  const [kind, setKind] = useState<'bvn' | 'nin'>('nin');
  const [method, setMethod] = useState<'OTP' | 'FACIAL'>('OTP');
  const [step, setStep] = useState<'details' | 'face'>('details');
  const [photo, setPhoto] = useState('');
  const [identifier, setIdentifier] = useState('');
  const [tracking, setTracking] = useState('');
  const [otp, setOtp] = useState('');
  const [needsOtp, setNeedsOtp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [tab, setTab] = useState<'identity' | 'upgrade' | 'address'>('identity');
  const [tier, setTier] = useState(2);
  const [upgrade, setUpgrade] = useState<Record<string, string>>({ pep: 'NO', idType: '1' });
  const [addressRef, setAddressRef] = useState('');
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
  const heading = { color: c.ink1, fontFamily: font.bold, fontSize: 17, marginTop: 8 };
  const hint = { color: c.ink3 ?? c.ink1, fontFamily: font.regular, fontSize: 13.5, lineHeight: 19 };
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
        if (challenge) setKind(challenge.kind);
      }
      else { setStatus(null); setMessage(result.message || 'Could not load verification status.'); }
    } catch { if (current(owner) && serial === readSerial.current) { setStatus(null); setMessage('Could not load verification status. Please try again.'); } }
  };
  useFocusEffect(useCallback(() => {
    generation.current = getSessionGeneration(); active.current = true; void load();
    return () => {
      active.current = false; ownership.current += 1; inFlight.current = false;
      setBusy(false); setStatus(null); setIdentifier(''); setOtp(''); setTracking(''); setNeedsOtp(false);
      setProfile(emptyProfile); setUpgrade({ pep: 'NO', idType: '1' }); setAddressRef('');
      setEmail(''); setEmailCode(''); setEmailSent(false); setMessage('');
      setStep('details'); setPhoto(''); setMethod('OTP');
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
      setMessage(result.message || (result.success ? 'Verification completed.' : result.pending ? 'Your request is being processed.' : 'We could not complete this request.'));
      if (result.tracking_id) { setTracking(result.tracking_id); setNeedsOtp(result.otp_required === true); }
      if (['expired', 'failed'].includes(result.account_setup_state)) { setTracking(''); setNeedsOtp(false); setOtp(''); }
      if ((result.success || result.pending) && tab === 'upgrade') setUpgrade({ pep: 'NO', idType: '1' });
      if (result.success) { setOtp(''); setIdentifier(''); setTracking(''); setNeedsOtp(false); await load(); }
      return result;
    } catch { if (current(owner)) setMessage('Connection interrupted. Check the request status before submitting again.'); }
    finally { if (current(owner)) { inFlight.current = false; setBusy(false); } }
  };
  // The selfie is captured on its own screen and only sent when the customer confirms it.
  const takeSelfie = async () => {
    if (!identifier || !current() || inFlight.current) return;
    const owner = ownership.current;
    inFlight.current = true; setBusy(true);
    try {
      const permission = await ImagePicker.requestCameraPermissionsAsync();
      if (!current(owner)) return;
      if (!permission.granted) { notify('Camera access needed', 'Allow the camera to take your selfie.'); return; }
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
  const upload = async (key: string, label: string, max: number) => {
    if (!current() || inFlight.current) return;
    const owner = ownership.current;
    inFlight.current = true; setBusy(true);
    try {
    beginExternalActivity();
    let result: DocumentPicker.DocumentPickerResult;
    try { result = await DocumentPicker.getDocumentAsync({ type: key === 'utilityBill' ? ['application/pdf', 'image/jpeg', 'image/png'] : ['image/jpeg', 'image/png'], copyToCacheDirectory: true }); } finally { endExternalActivity(); }
    if (result.canceled || !current(owner)) return;
    const file = result.assets[0];
    if (file.size && file.size > Math.floor(max * 3 / 4)) { notify('File too large', `${label} must be smaller than ${Math.floor(max * 3 / 4096)} KB.`); return; }
    const encoded = await FileSystem.readAsStringAsync(file.uri, { encoding: FileSystem.EncodingType.Base64 });
    if (encoded.length > max) { notify('File too large', 'Compress this document and try again.'); return; }
    if (current(owner)) setUpgrade((p) => ({ ...p, [key]: encoded }));
    } catch { if (current(owner)) setMessage('Could not read this document. Please try again.'); }
    finally { if (current(owner)) { inFlight.current = false; setBusy(false); } }
  };
  if (status?.email_verified === false) return <Screen header={<Header title="Verify your email" />}>
    <View style={{ gap: 16 }}>
      <Text style={text}>Confirm your email address before setting up your Zitch account.</Text>
      {!!message && <Text accessibilityLiveRegion="polite" style={text}>{message}</Text>}
      <Field label="Email address" value={email} maxLength={254} keyboardType="email-address" autoCapitalize="none" autoCorrect={false}
        editable={!busy && !emailSent} onChangeText={setEmail} />
      {emailSent ? <>
        <Field label="Six-digit email code" value={emailCode} maxLength={6} keyboardType="number-pad" secureTextEntry
          editable={!busy} onChangeText={setEmailCode} />
        <Btn label="Confirm email code" disabled={busy || !/^[0-9]{6}$/.test(emailCode)} onPress={() => void verifyEmail(true)} />
        <Btn label="Resend email code" variant="outline" disabled={busy} onPress={() => void verifyEmail(false)} />
        <Btn label="Change email address" variant="outline" disabled={busy} onPress={() => { setEmailSent(false); setEmailCode(''); setMessage(''); }} />
      </> : <Btn label="Send email code" disabled={busy || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())} onPress={() => void verifyEmail(false)} />}
    </View>
  </Screen>;
  if (!status || !status.identity_available) return <Screen header={<Header title="Verify your account" />}>
    <View style={{ gap: 16 }}><Text style={text}>{message || (!status ? 'Loading verification status…' : 'Verification is currently unavailable for this account. Please verify your email and phone number, or contact support.')}</Text>
      <Btn label="Refresh verification status" variant="outline" onPress={() => void load()} /></View>
  </Screen>;
  if (step === 'face') return <Screen header={<Header title="Face check" onBack={busy ? undefined : () => { setPhoto(''); setStep('details'); }} />}>
    <View style={{ gap: 16, paddingBottom: 32 }}>
      <Text style={text}>Take a clear selfie so we can match it to your ID.</Text>
      <Text style={text}>{'•  Face the camera in good light\n•  Remove glasses, hats and face coverings\n•  Keep your whole face inside the frame'}</Text>
      {!!photo && <Image accessibilityLabel="Your selfie" source={{ uri: `data:image/jpeg;base64,${photo}` }}
        style={{ width: 180, height: 180, borderRadius: 90, alignSelf: 'center', borderWidth: 3, borderColor: c.brand }} />}
      {!!message && <Text accessibilityLiveRegion="polite" style={text}>{message}</Text>}
      <Btn label={photo ? 'Retake selfie' : 'Take a selfie'} variant={photo ? 'outline' : 'primary'} disabled={busy} onPress={() => void takeSelfie()} />
      {!!photo && <Btn label={busy ? 'Please wait…' : 'Verify my face'} disabled={busy} onPress={() => void submitFace()} />}
      <Btn label="Back to your details" variant="outline" disabled={busy} onPress={() => { setPhoto(''); setStep('details'); }} />
    </View>
  </Screen>;
  const needsNimcId = kind === 'nin' && !status?.account_number;
  const dobError = profile.dateOfBirth.length === 10 && !validDob(profile.dateOfBirth) ? 'Enter a real date of birth' : undefined;
  const detailsReady = identifier.length === 11 && validDob(profile.dateOfBirth) && (profile.gender === 0 || profile.gender === 1)
    && (!needsNimcId || !!profile.ninUserId?.trim());
  return <Screen header={<Header title="Verify your account" />}>
    <View style={{ gap: 16, paddingBottom: 32 }}>
      <Text style={text}>Zitch verifies your identity securely. Your details are only used to open and protect your account.</Text>
      {status?.sandbox && <Text style={{ ...text, color: c.brand }}>Test mode — use test identities only. No real money moves.</Text>}
      <Text style={text}>Account tier: {status?.bank_tier || 'Not set up'} · BVN: {status?.bvn_verified ? 'Verified' : 'Not verified'} · NIN: {status?.nin_verified ? 'Verified' : 'Not verified'}</Text>
      <View style={{ gap: 8 }}>{(['identity', 'upgrade', 'address'] as const).map((value) => <Btn key={value} label={value === 'identity' ? 'Identity & account setup' : value === 'upgrade' ? 'Tier upgrade' : 'Tier 3 address'} variant={tab === value ? 'primary' : 'outline'} onPress={() => setTab(value)} disabled={busy} />)}</View>
      {!!message && <Text accessibilityLiveRegion="polite" style={text}>{message}</Text>}
      {tab === 'identity' && <>
        <Text style={heading}>Your details</Text>
        <Text style={hint}>Use your name exactly as it appears on your NIN or BVN.</Text>
        <Field editable={!busy} label="First and middle names" value={profile.otherNames} onChangeText={(otherNames) => setProfile({ ...profile, otherNames })} />
        <Field editable={!busy} label="Surname" value={profile.lastName} onChangeText={(lastName) => setProfile({ ...profile, lastName })} />
        <Field editable={!busy && !status?.profile?.phoneNo} label="Phone number" value={profile.phoneNo} keyboardType="phone-pad" onChangeText={(phoneNo) => setProfile({ ...profile, phoneNo })} />
        <Field editable={false} label="Verified email" value={profile.email} keyboardType="email-address" />
        <Field editable={!busy} label="Date of birth" placeholder="DD/MM/YYYY" value={profile.dateOfBirth} keyboardType="number-pad" maxLength={10}
          error={dobError} onChangeText={(value) => setProfile({ ...profile, dateOfBirth: maskDob(value) })} />
        <View style={{ gap: 8 }}><Text style={heading}>Gender</Text>
          <PillTabs scroll={false} options={[{ v: '0', label: 'Male' }, { v: '1', label: 'Female' }]}
            value={profile.gender === 0 || profile.gender === 1 ? String(profile.gender) : ''}
            onChange={(value) => { if (!busy) setProfile({ ...profile, gender: Number(value) }); }} /></View>
        <Field editable={!busy} label="Home address" value={profile.address} maxLength={100} onChangeText={(address) => setProfile({ ...profile, address })} />
        <Text style={heading}>Your ID</Text>
        <PillTabs scroll={false} options={[{ v: 'nin', label: 'NIN' }, { v: 'bvn', label: 'BVN' }]} value={kind}
          onChange={(value) => { if (busy || tracking) return; setKind(value as 'bvn' | 'nin'); setIdentifier(''); }} />
        <Field editable={!busy} label={kind.toUpperCase()} placeholder="11 digits" value={identifier} maxLength={11} keyboardType="number-pad" secureTextEntry onChangeText={(value) => setIdentifier(digits(value, 11))} />
        {needsNimcId && <View style={{ gap: 6 }}>
          <Field editable={!busy} label="NIMC User ID" value={profile.ninUserId || ''} autoCapitalize="none" autoCorrect={false} onChangeText={(ninUserId) => setProfile({ ...profile, ninUserId })} />
          <Text style={hint}>A separate ID from NIMC, not your 11-digit NIN. Only needed when you open your account with NIN.</Text>
        </View>}
        {!tracking && <>
          <Text style={heading}>How should we verify you?</Text>
          <PillTabs scroll={false} check options={[{ v: 'OTP', label: 'Text me a code' }, { v: 'FACIAL', label: 'Face check' }]} value={method}
            onChange={(value) => { if (!busy) setMethod(value as 'OTP' | 'FACIAL'); }} />
          <Text style={hint}>{method === 'FACIAL' ? 'Take a quick selfie on the next screen. No code needed.' : 'We send a 6-digit code by SMS.'}</Text>
          <Btn label={busy ? 'Please wait…' : method === 'FACIAL' ? 'Continue to face check' : 'Send my code'} disabled={busy || !detailsReady}
            onPress={() => {
              if (method === 'FACIAL') { setMessage(''); setPhoto(''); setStep('face'); return; }
              void run(() => banking.identity(kind, identifier, profile));
            }} />
        </>}
        {needsOtp && <><Field editable={!busy} label="Six-digit code" value={otp} maxLength={6} keyboardType="number-pad" secureTextEntry onChangeText={(value) => setOtp(digits(value, 6))} /><Btn label="Confirm code" disabled={busy || otp.length !== 6} onPress={() => void run(() => banking.confirm(tracking, otp))} /></>}
        {!!tracking && <Btn label="Check verification status" variant="outline" disabled={busy} onPress={() => void run(() => banking.progress(tracking))} />}
        <Text style={hint}>Codes expire after 10 minutes. If you’ve already started, check the status before starting again.</Text>
      </>}
      {tab === 'upgrade' && <>
        <Text style={text}>Tier 2 needs your verified BVN and NIN, a utility bill and the documents below. Tier 3 also needs a completed address check. Your tier changes once the review is approved.</Text>
        <PillTabs scroll={false} options={[{ v: '2', label: 'Tier 2' }, { v: '3', label: 'Tier 3' }]} value={String(tier)} onChange={(value) => { if (!busy) setTier(Number(value)); }} />
        {upgradeFields.map(([key, label]) => <Field editable={!busy} key={key} label={label} value={upgrade[key] || ''} secureTextEntry={key === 'bvn' || key === 'nin'}
          keyboardType={numericFields.has(key) ? 'number-pad' : undefined} maxLength={key === 'bvn' || key === 'nin' ? 11 : numericFields.has(key) ? 10 : undefined}
          onChangeText={(value) => setUpgrade((p) => ({ ...p, [key]: key === 'bvn' || key === 'nin' ? digits(value, 11) : key === 'idIssueDate' || key === 'idExpiryDate' ? maskIsoDate(value) : value }))} />)}
        <View style={{ gap: 8 }}><Text style={heading}>ID type</Text>
          <PillTabs check options={idTypes} value={upgrade.idType || '1'} onChange={(value) => { if (!busy) setUpgrade((p) => ({ ...p, idType: value })); }} /></View>
        <View style={{ gap: 8 }}><Text style={heading}>Are you a politically exposed person?</Text>
          <Text style={hint}>For example a senior public official, or their close family.</Text>
          <PillTabs scroll={false} options={[{ v: 'NO', label: 'No' }, { v: 'YES', label: 'Yes' }]} value={upgrade.pep || 'NO'} onChange={(value) => { if (!busy) setUpgrade((p) => ({ ...p, pep: value })); }} /></View>
        {files.map(([key, label, max]) => <Btn key={key} variant="outline" label={`${upgrade[key] ? 'Attached: ' : 'Upload '}${label}`} disabled={busy} onPress={() => void upload(key, label, max)} />)}
        {tier === 3 && <Field editable={!busy} label="Address check reference" value={addressRef} onChangeText={setAddressRef} />}
        <Btn label="Submit for bank review" disabled={busy} onPress={() => void run(() => banking.upgrade(tier, { ...upgrade, accountName: status?.account_name || '', phoneNumber: profile.phoneNo, email: profile.email }, addressRef))} />
      </>}
      {tab === 'address' && <>
        <Text style={text}>Zitch checks your home address. Submitting doesn’t unlock Tier 3 straight away.</Text>
        {status.address_verification_available === true && <>
          {upgradeFields.filter(([key]) => ['houseNumber', 'streetName', 'city', 'state', 'localGovernment', 'nearestLandmark'].includes(key)).map(([key, label]) => <Field editable={!busy} key={key} label={label} value={upgrade[key] || ''} onChangeText={(value) => setUpgrade((p) => ({ ...p, [key]: value }))} />)}
          <Field editable={!busy} label="Full home address" value={profile.address} onChangeText={(address) => setProfile({ ...profile, address })} />
        </>}
        {status.address_verification_available !== true && <Text style={text}>Address verification is temporarily unavailable. Please contact support.</Text>}
        <Btn label="Request address verification" disabled={busy || !status.address_verification_available || (status?.bank_tier || 0) < 2} onPress={() => void run(async () => {
          const result = await banking.address({ house_number: upgrade.houseNumber || '', street: upgrade.streetName || '', city: upgrade.city || '', state: upgrade.state || '', lga: upgrade.localGovernment || '', landmark: upgrade.nearestLandmark || '', address: profile.address });
          if (current() && result.reference) setAddressRef(result.reference); return result;
        })} />
        <Field editable={!busy} label="Address request reference" value={addressRef} onChangeText={setAddressRef} />
        <Btn label="Check address status" variant="outline" disabled={busy || !addressRef} onPress={() => void run(() => banking.addressStatus(addressRef))} />
      </>}
    </View>
  </Screen>;
}

export default function KycScreen() {
  return <AuthGuard><KycScreenContent /></AuthGuard>;
}
