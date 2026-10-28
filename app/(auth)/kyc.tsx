import React, { useCallback, useRef, useState } from 'react';
import { Text, View } from 'react-native';
import { useFocusEffect } from 'expo-router';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system';
import AuthGuard from '@/components/AuthGuard';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { banking, type BankProfile, type BankStatus } from '@/lib/services/banking';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { getSessionGeneration } from '@/lib/secureStore';

const emptyProfile: BankProfile = { lastName: '', otherNames: '', phoneNo: '', email: '', dateOfBirth: '', address: '' };
const upgradeFields = [
  ['bvn', 'BVN'], ['nin', 'NIN'], ['idType', 'ID type: 1 NIN, 2 driver licence, 3 voter card, 4 passport'],
  ['idNumber', 'ID number'], ['idIssueDate', 'ID issue date (YYYY-MM-DD)'], ['idExpiryDate', 'ID expiry date (if applicable)'],
  ['houseNumber', 'House number'], ['streetName', 'Street'], ['city', 'City'], ['state', 'State'],
  ['localGovernment', 'Local government area'], ['nearestLandmark', 'Nearest landmark'], ['pep', 'Politically exposed person: YES or NO'],
] as const;
const files = [['userPhoto', 'Passport photo', 100000], ['idCardFront', 'ID front', 100000],
  ['idCardBack', 'ID back', 100000], ['utilityBill', 'Utility bill', 50000], ['customerSignature', 'Signature', 680000]] as const;

function KycScreenContent() {
  const { c } = useTheme();
  const [status, setStatus] = useState<BankStatus | null>(null);
  const [profile, setProfile] = useState<BankProfile>(emptyProfile);
  const [kind, setKind] = useState<'bvn' | 'nin'>('bvn');
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
  const generation = useRef(getSessionGeneration());
  const active = useRef(true);
  const ownership = useRef(0);
  const readSerial = useRef(0);
  const inFlight = useRef(false);
  const current = (owner = ownership.current) => active.current && owner === ownership.current && generation.current === getSessionGeneration();
  const text = { color: c.ink1, fontFamily: font.regular, lineHeight: 22 };
  const load = async () => {
    const owner = ownership.current, serial = ++readSerial.current;
    try {
      const result = await banking.status();
      if (!current(owner) || serial !== readSerial.current) return;
      if (result.success) {
        setStatus(result); setMessage(''); setProfile((p) => ({ ...p, ...result.profile }));
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
    };
  }, []));
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
  const face = async () => {
    if (!identifier || busy || !current()) return;
    const owner = ownership.current;
    await run(async () => {
      const permission = await ImagePicker.requestCameraPermissionsAsync();
      if (!current(owner)) return;
      if (!permission.granted) { notify('Camera access needed', 'Allow the camera to take your verification photo.'); return; }
      beginExternalActivity();
      let photo: ImagePicker.ImagePickerResult;
      try { photo = await ImagePicker.launchCameraAsync({ cameraType: ImagePicker.CameraType.front, base64: true, quality: 0.5 }); } finally { endExternalActivity(); }
      const data = !photo.canceled ? photo.assets[0]?.base64 : null;
      if (data && current(owner)) return banking.identity(kind, identifier, profile, data);
    });
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
    if (file.size && file.size > Math.floor(max * 3 / 4)) { notify('File too large', `${label} must be smaller than ${Math.floor(max * 3 / 4096)} KB for the bank.`); return; }
    const encoded = await FileSystem.readAsStringAsync(file.uri, { encoding: FileSystem.EncodingType.Base64 });
    if (encoded.length > max) { notify('File too large', 'Compress this document and try again.'); return; }
    if (current(owner)) setUpgrade((p) => ({ ...p, [key]: encoded }));
    } catch { if (current(owner)) setMessage('Could not read this document. Please try again.'); }
    finally { if (current(owner)) { inFlight.current = false; setBusy(false); } }
  };
  if (!status || !status.identity_available) return <Screen header={<Header title="Verify your account" />}>
    <View style={{ gap: 16 }}><Text style={text}>{message || (!status ? 'Loading verification status…' : 'Bank verification is currently unavailable for this account. Please verify your email and phone number, or contact support.')}</Text>
      <Btn label="Refresh verification status" variant="outline" onPress={() => void load()} /></View>
  </Screen>;
  return <Screen header={<Header title="Verify your account" />}>
    <View style={{ gap: 16, paddingBottom: 32 }}>
      <Text style={text}>9PSB verifies your BVN, NIN and face. Prembly verifies your residential address for Tier 3.</Text>
      {status?.sandbox && <Text style={{ ...text, color: c.brand }}>Test environment — use approved test identities only. No real banking is active.</Text>}
      <Text style={text}>Bank tier: {status?.bank_tier || 'Not confirmed'} · BVN: {status?.bvn_verified ? 'Verified' : 'Required'} · NIN: {status?.nin_verified ? 'Verified' : 'Not verified'}</Text>
      <View style={{ gap: 8 }}>{(['identity', 'upgrade', 'address'] as const).map((value) => <Btn key={value} label={value === 'identity' ? 'Identity & account setup' : value === 'upgrade' ? 'Tier upgrade' : 'Tier 3 address'} variant={tab === value ? 'primary' : 'outline'} onPress={() => setTab(value)} disabled={busy} />)}</View>
      {!!message && <Text accessibilityLiveRegion="polite" style={text}>{message}</Text>}
      {tab === 'identity' && <>
        <Field editable={!busy} label="First and other names" value={profile.otherNames} onChangeText={(otherNames) => setProfile({ ...profile, otherNames })} />
        <Field editable={!busy} label="Last name" value={profile.lastName} onChangeText={(lastName) => setProfile({ ...profile, lastName })} />
        <Field editable={!busy} label="Phone number" value={profile.phoneNo} keyboardType="phone-pad" onChangeText={(phoneNo) => setProfile({ ...profile, phoneNo })} />
        <Field editable={false} label="Verified email" value={profile.email} keyboardType="email-address" />
        <Field editable={!busy} label="Date of birth (DD/MM/YYYY)" value={profile.dateOfBirth} onChangeText={(dateOfBirth) => setProfile({ ...profile, dateOfBirth })} />
        <View style={{ gap: 8 }}><Text style={text}>Gender required by the bank</Text>{[['Male', 0], ['Female', 1]].map(([label, value]) => <Btn key={label} label={String(label)} disabled={busy} variant={profile.gender === value ? 'primary' : 'outline'} onPress={() => setProfile({ ...profile, gender: Number(value) })} />)}</View>
        <Field editable={!busy} label="Residential address" value={profile.address} maxLength={100} onChangeText={(address) => setProfile({ ...profile, address })} />
        <View style={{ gap: 8 }}>{(['bvn', 'nin'] as const).map((value) => <Btn key={value} label={value.toUpperCase()} variant={kind === value ? 'primary' : 'outline'} disabled={busy || !!tracking} onPress={() => { setKind(value); setIdentifier(''); }} />)}</View>
        <Field editable={!busy} label={kind.toUpperCase()} value={identifier} maxLength={11} keyboardType="number-pad" secureTextEntry onChangeText={setIdentifier} />
        {kind === 'nin' && !status?.account_number && <Field editable={!busy} label="NIN user ID (from NIMC)" value={profile.ninUserId || ''} onChangeText={(ninUserId) => setProfile({ ...profile, ninUserId })} />}
        {!tracking && <><Btn label={busy ? 'Please wait…' : 'Verify with bank OTP'} disabled={busy || identifier.length !== 11} onPress={() => void run(() => banking.identity(kind, identifier, profile))} /><Btn label="Verify with a face photo" variant="outline" disabled={busy || identifier.length !== 11} onPress={() => void face()} /></>}
        {needsOtp && <><Field editable={!busy} label="Six-digit bank OTP" value={otp} maxLength={6} keyboardType="number-pad" secureTextEntry onChangeText={setOtp} /><Btn label="Confirm bank OTP" disabled={busy || otp.length !== 6} onPress={() => void run(() => banking.confirm(tracking, otp))} /></>}
        {!!tracking && <Btn label="Check verification status" variant="outline" disabled={busy} onPress={() => void run(() => banking.progress(tracking))} />}
        <Text style={text}>Bank OTPs expire after 10 minutes. An accepted request may still be processing; check its status before starting again.</Text>
      </>}
      {tab === 'upgrade' && <>
        <Text style={text}>Tier 2 requires verified BVN and NIN, a utility bill and the documents below. Tier 3 also requires a completed Prembly address check. Your tier changes after bank approval.</Text>
        {[2, 3].map((value) => <Btn key={value} label={`Tier ${value}`} variant={tier === value ? 'primary' : 'outline'} onPress={() => setTier(value)} disabled={busy} />)}
        {upgradeFields.map(([key, label]) => <Field editable={!busy} key={key} label={label} value={upgrade[key] || ''} secureTextEntry={key === 'bvn' || key === 'nin'} onChangeText={(value) => setUpgrade((p) => ({ ...p, [key]: value }))} />)}
        {files.map(([key, label, max]) => <Btn key={key} variant="outline" label={`${upgrade[key] ? 'Attached: ' : 'Upload '}${label}`} disabled={busy} onPress={() => void upload(key, label, max)} />)}
        {tier === 3 && <Field editable={!busy} label="Prembly address request reference" value={addressRef} onChangeText={setAddressRef} />}
        <Btn label="Submit for bank review" disabled={busy} onPress={() => void run(() => banking.upgrade(tier, { ...upgrade, accountName: status?.account_name || '', phoneNumber: profile.phoneNo, email: profile.email }, addressRef))} />
      </>}
      {tab === 'address' && <>
        <Text style={text}>Physical verification is completed by Prembly. Submission does not immediately activate Tier 3.</Text>
        {status.address_verification_available === true && <>
          {upgradeFields.filter(([key]) => ['houseNumber', 'streetName', 'city', 'state', 'localGovernment', 'nearestLandmark'].includes(key)).map(([key, label]) => <Field editable={!busy} key={key} label={label} value={upgrade[key] || ''} onChangeText={(value) => setUpgrade((p) => ({ ...p, [key]: value }))} />)}
          <Field editable={!busy} label="Full residential address" value={profile.address} onChangeText={(address) => setProfile({ ...profile, address })} />
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
