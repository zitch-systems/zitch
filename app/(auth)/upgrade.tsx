import React, { useCallback, useRef, useState } from 'react';
import { Image, Text, View } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system';
import AuthGuard from '@/components/AuthGuard';
import { Screen, Header, Field, Btn, PillTabs, Card } from '@/components/design/ui';
import { ComboField } from '@/components/design/ComboField';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { banking, type BankStatus } from '@/lib/services/banking';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { getSessionGeneration } from '@/lib/secureStore';
import { NIGERIA_LGAS, NIGERIA_STATES } from '@/lib/data/nigeria';

const idTypes = [{ v: '1', label: 'NIN' }, { v: '2', label: 'Driver’s licence' }, { v: '3', label: 'Voter’s card' }, { v: '4', label: 'Passport' }];
// A NIN slip and a passport data page have no back side.
const noBack = new Set(['1', '4']);
const docs = { idCardFront: ['ID front', 100000], idCardBack: ['ID back', 100000], utilityBill: ['Utility bill', 50000], customerSignature: ['Signature', 680000] } as const;
type Doc = keyof typeof docs;
const fresh = () => ({ pep: 'NO', idType: '1' } as Record<string, string>);
const digits = (value: string, max: number) => value.replace(/\D/g, '').slice(0, max);
const isoDate = (value: string) => {
  const d = value.replace(/\D/g, '').slice(0, 8);
  return d.length > 6 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d.length > 4 ? `${d.slice(0, 4)}-${d.slice(4)}` : d;
};
const lgasFor = (state: string) => NIGERIA_LGAS[NIGERIA_STATES.find((s) => s.toLowerCase() === state.trim().toLowerCase()) || ''] || [];

function UpgradeContent() {
  const { c } = useTheme();
  const [status, setStatus] = useState<BankStatus | null>(null);
  const [form, setForm] = useState<Record<string, string>>(fresh);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const generation = useRef(getSessionGeneration());
  const active = useRef(true);
  const ownership = useRef(0);
  const inFlight = useRef(false);
  const current = (owner = ownership.current) => active.current && owner === ownership.current && generation.current === getSessionGeneration();
  const text = { color: c.ink1, fontFamily: font.regular, lineHeight: 22 };
  const heading = { color: c.ink1, fontFamily: font.bold, fontSize: 16 };
  const set = (key: string, value: string) => setForm((p) => ({ ...p, [key]: value }));

  const load = async () => {
    const owner = ownership.current;
    try {
      const result = await banking.status();
      if (!current(owner)) return;
      if (result.success) setStatus(result); else setMessage(result.message || 'Could not load your account.');
    } catch { if (current(owner)) setMessage('Could not load your account. Please try again.'); }
  };
  useFocusEffect(useCallback(() => {
    generation.current = getSessionGeneration(); active.current = true; void load();
    return () => { active.current = false; ownership.current += 1; inFlight.current = false; setBusy(false); setForm(fresh()); setMessage(''); setSubmitted(false); };
  }, []));
  const guarded = async (work: (owner: number) => Promise<void>) => {
    if (!current() || inFlight.current) return;
    const owner = ownership.current;
    inFlight.current = true; setBusy(true);
    try { await work(owner); }
    catch { if (current(owner)) setMessage('Connection interrupted. Check the status before trying again.'); }
    finally { if (current(owner)) { inFlight.current = false; setBusy(false); } }
  };
  const upload = (key: Doc) => guarded(async (owner) => {
    const [label, max] = docs[key];
    beginExternalActivity();
    let result: DocumentPicker.DocumentPickerResult;
    try { result = await DocumentPicker.getDocumentAsync({ type: key === 'utilityBill' ? ['application/pdf', 'image/jpeg', 'image/png'] : ['image/jpeg', 'image/png'], copyToCacheDirectory: true }); }
    finally { endExternalActivity(); }
    if (result.canceled || !current(owner)) return;
    const file = result.assets[0];
    if (file.size && file.size > Math.floor(max * 3 / 4)) { notify('File too large', `${label} must be smaller than ${Math.floor(max * 3 / 4096)} KB.`); return; }
    const encoded = await FileSystem.readAsStringAsync(file.uri, { encoding: FileSystem.EncodingType.Base64 });
    if (encoded.length > max) { notify('File too large', 'Compress this document and try again.'); return; }
    if (current(owner)) set(key, encoded);
  });
  // In-app face capture with the front camera; the server sizes it for the bank.
  const captureFace = () => guarded(async (owner) => {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!current(owner)) return;
    if (!permission.granted) { notify('Camera access needed', 'Allow the camera to verify your face.'); return; }
    beginExternalActivity();
    let shot: ImagePicker.ImagePickerResult;
    try { shot = await ImagePicker.launchCameraAsync({ cameraType: ImagePicker.CameraType.front, base64: true, quality: 0.4, allowsEditing: true, aspect: [1, 1] }); }
    finally { endExternalActivity(); }
    const data = !shot.canceled ? shot.assets[0]?.base64 : null;
    if (data && current(owner)) set('userPhoto', data);
  });

  if (!status) return <Screen header={<Header title="Upgrade account" />}>
    <View style={{ gap: 16 }}><Text style={text}>{message || 'Loading…'}</Text>
      {!!message && <Btn label="Try again" variant="outline" onPress={() => { setMessage(''); void load(); }} />}</View>
  </Screen>;
  const tier = status.bank_tier || 0;
  if (!status.account_number || tier < 1) return <Screen header={<Header title="Upgrade account" />}>
    <View style={{ gap: 16 }}><Text style={text}>Open your Zitch account first.</Text>
      <Btn label="Open my account" onPress={() => router.replace('/kyc')} /></View>
  </Screen>;
  if (tier >= 3) return <Screen header={<Header title="Upgrade account" />}>
    <Card><Text style={heading}>You’re on Tier 3</Text><Text style={{ ...text, marginTop: 4 }}>Your account has the highest limits.</Text></Card>
  </Screen>;
  const target = tier + 1;
  if (!status.bvn_verified || !status.nin_verified) return <Screen header={<Header title={`Upgrade to Tier ${target}`} />}>
    <View style={{ gap: 16 }}><Text style={text}>{`Verify your ${status.bvn_verified ? 'NIN' : 'BVN'} first.`}</Text>
      <Btn label={`Verify my ${status.bvn_verified ? 'NIN' : 'BVN'}`} onPress={() => router.push('/kyc')} /></View>
  </Screen>;

  const notice = !!message && <Text accessibilityLiveRegion="polite" style={{ ...text, color: c.brand }}>{message}</Text>;
  const addressFields = (prefix: '' | 'addr_') => <>
    <Field editable={!busy} label="House number" value={form[prefix + 'houseNumber'] || ''} keyboardType="number-pad" maxLength={20}
      onChangeText={(v) => set(prefix + 'houseNumber', v)} />
    <Field editable={!busy} label="Street" value={form[prefix + 'streetName'] || ''} onChangeText={(v) => set(prefix + 'streetName', v)} />
    <Field editable={!busy} label="City or town" value={form[prefix + 'city'] || ''} onChangeText={(v) => set(prefix + 'city', v)} />
    <ComboField editable={!busy} label="State" value={form[prefix + 'state'] || ''} options={NIGERIA_STATES}
      onChange={(v) => setForm((p) => ({ ...p, [prefix + 'state']: v, ...(lgasFor(v).includes(p[prefix + 'localGovernment']) ? {} : { [prefix + 'localGovernment']: '' }) }))} />
    <ComboField editable={!busy} label="Local government area" value={form[prefix + 'localGovernment'] || ''}
      options={lgasFor(form[prefix + 'state'] || '')} onChange={(v) => set(prefix + 'localGovernment', v)} />
    <Field editable={!busy} label="Nearest landmark" value={form[prefix + 'nearestLandmark'] || ''} onChangeText={(v) => set(prefix + 'nearestLandmark', v)} />
  </>;

  // Tier 3: the home address is verified first; the upgrade form follows once it is.
  if (target === 3 && !status.address_verified) {
    const pending = status.address_verification_pending && !!status.address_reference;
    const addressReady = ['houseNumber', 'streetName', 'city', 'state', 'localGovernment', 'nearestLandmark'].every((k) => !!form['addr_' + k]?.trim());
    return <Screen header={<Header title="Upgrade to Tier 3" />}>
      <View style={{ gap: 16, paddingBottom: 32 }}>
        <Text style={heading}>Verify your home address</Text>
        {pending ? <>
          <Card><Text style={text}>Your address is being verified.</Text></Card>
          <Btn label="Check status" variant="outline" disabled={busy} onPress={() => void guarded(async (owner) => {
            const result = await banking.addressStatus(status.address_reference || '');
            if (!current(owner)) return;
            setMessage(result.success ? '' : result.message || '');
            await load();
          })} />
        </> : status.address_verification_available === true ? <>
          {addressFields('addr_')}
          <Btn label="Verify my address" disabled={busy || !addressReady} onPress={() => void guarded(async (owner) => {
            const a = (k: string) => (form['addr_' + k] || '').trim();
            const result = await banking.address({ house_number: a('houseNumber'), street: a('streetName'), city: a('city'), state: a('state'),
              lga: a('localGovernment'), landmark: a('nearestLandmark'),
              address: `${a('houseNumber')} ${a('streetName')}, ${a('city')}, ${a('state')}`.slice(0, 255) });
            if (!current(owner)) return;
            setMessage(result.message || '');
            await load();
          })} />
        </> : <Card><Text style={text}>Address verification is not available yet.</Text></Card>}
        {notice}
      </View>
    </Screen>;
  }

  const nin = form.idType === '1';
  const needsBack = !noBack.has(form.idType);
  const required = ['bvn', 'nin', 'idCardFront', 'utilityBill', 'customerSignature', 'userPhoto',
    ...(nin ? [] : ['idIssueDate', 'idExpiryDate']), ...(needsBack ? ['idCardBack'] : []),
    ...(target === 2 ? ['houseNumber', 'streetName', 'city', 'state', 'localGovernment', 'nearestLandmark'] : [])];
  const ready = required.every((k) => !!form[k]?.trim()) && form.bvn?.length === 11 && form.nin?.length === 11;
  const docButton = (key: Doc) => <Btn key={key} variant="outline" label={`${form[key] ? '✓ ' : 'Upload '}${docs[key][0]}`} disabled={busy} onPress={() => void upload(key)} />;
  const submit = () => guarded(async (owner) => {
    const body: Record<string, string> = { ...form, accountName: status.account_name || '', phoneNumber: status.profile?.phoneNo || '', email: status.profile?.email || '' };
    if (nin) { delete body.idIssueDate; delete body.idExpiryDate; }
    if (!needsBack) delete body.idCardBack;
    Object.keys(body).filter((k) => k.startsWith('addr_')).forEach((k) => delete body[k]);
    const result = await banking.upgrade(target, body);
    if (!current(owner)) return;
    setMessage(result.message || (result.success ? 'Upgrade approved.' : 'We could not submit your upgrade.'));
    // Raw documents are dropped once the bank has the request.
    if (result.success || result.pending) { setForm(fresh()); setSubmitted(true); await load(); }
  });

  if (submitted) return <Screen header={<Header title={`Upgrade to Tier ${target}`} />}>
    <View style={{ gap: 16 }}><Card><Text style={text}>{message || 'Your upgrade is being reviewed.'}</Text></Card>
      <Btn label="Done" onPress={() => router.back()} /></View>
  </Screen>;

  return <Screen header={<Header title={`Upgrade to Tier ${target}`} />}>
    <View style={{ gap: 16, paddingBottom: 32 }}>
      <Text style={heading}>BVN and NIN</Text>
      <Field editable={!busy} label="BVN" value={form.bvn || ''} maxLength={11} keyboardType="number-pad" secureTextEntry onChangeText={(v) => set('bvn', digits(v, 11))} />
      <Field editable={!busy} label="NIN" value={form.nin || ''} maxLength={11} keyboardType="number-pad" secureTextEntry onChangeText={(v) => set('nin', digits(v, 11))} />

      <Text style={heading}>ID document</Text>
      <PillTabs check options={idTypes} value={form.idType} onChange={(v) => { if (!busy) set('idType', v); }} />
      {!nin && <>
        <Field editable={!busy} label="Issue date" placeholder="YYYY-MM-DD" value={form.idIssueDate || ''} keyboardType="number-pad" maxLength={10} onChangeText={(v) => set('idIssueDate', isoDate(v))} />
        <Field editable={!busy} label="Expiry date" placeholder="YYYY-MM-DD" value={form.idExpiryDate || ''} keyboardType="number-pad" maxLength={10} onChangeText={(v) => set('idExpiryDate', isoDate(v))} />
      </>}
      {docButton('idCardFront')}
      {needsBack && docButton('idCardBack')}

      {target === 2 && <><Text style={heading}>Home address</Text>{addressFields('')}</>}

      <Text style={heading}>Face and documents</Text>
      {!!form.userPhoto && <Image accessibilityLabel="Your photo" source={{ uri: `data:image/jpeg;base64,${form.userPhoto}` }}
        style={{ width: 120, height: 120, borderRadius: 60, alignSelf: 'center', borderWidth: 3, borderColor: c.brand }} />}
      <Btn label={form.userPhoto ? 'Retake face photo' : 'Verify your face'} variant={form.userPhoto ? 'outline' : 'primary'} disabled={busy} onPress={() => void captureFace()} />
      {docButton('utilityBill')}
      {docButton('customerSignature')}

      <View style={{ gap: 8 }}><Text style={heading}>Are you a politically exposed person?</Text>
        <PillTabs scroll={false} options={[{ v: 'NO', label: 'No' }, { v: 'YES', label: 'Yes' }]} value={form.pep} onChange={(v) => { if (!busy) set('pep', v); }} /></View>
      {notice}
      <Btn label={busy ? 'Please wait…' : `Upgrade to Tier ${target}`} disabled={busy || !ready} onPress={() => void submit()} />
    </View>
  </Screen>;
}

export default function UpgradeScreen() {
  return <AuthGuard><UpgradeContent /></AuthGuard>;
}
