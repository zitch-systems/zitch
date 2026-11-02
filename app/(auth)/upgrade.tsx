import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Text, View } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
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

// The NIN is the ID: its number is the ID number and its slip is the ID image.
// Files go to Zitch one at a time (up to 5 MB, a photo or PDF); Zitch converts
// them to the sizes the bank accepts and holds them until the upgrade is sent.
const docs = { idCardFront: 'NIN slip', utilityBill: 'Utility bill', customerSignature: 'Signature' } as const;
type Doc = keyof typeof docs;
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
const REQUIRED = 'This field is required';
const fresh = () => ({ pep: 'NO' } as Record<string, string>);
const digits = (value: string, max: number) => value.replace(/\D/g, '').slice(0, max);
const lgasFor = (state: string) => NIGERIA_LGAS[NIGERIA_STATES.find((s) => s.toLowerCase() === state.trim().toLowerCase()) || ''] || [];

function UpgradeContent() {
  const { c } = useTheme();
  const [status, setStatus] = useState<BankStatus | null>(null);
  const [form, setForm] = useState<Record<string, string>>(fresh);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [ready, setReady] = useState<Set<string>>(new Set());
  const [tried, setTried] = useState(false);
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
  const loadDocuments = async () => {
    const owner = ownership.current;
    try {
      const result = await banking.documents();
      if (current(owner) && result.success) setReady(new Set(result.documents || []));
    } catch { /* The list refreshes on the next visit. */ }
  };
  useFocusEffect(useCallback(() => {
    generation.current = getSessionGeneration(); active.current = true; void load(); void loadDocuments();
    return () => { active.current = false; ownership.current += 1; inFlight.current = false; setBusy(false); setForm(fresh()); setMessage(''); setSubmitted(false); setTried(false); };
  }, []));
  // Returning from the face-check page refreshes what is ready.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => { if (state === 'active') void loadDocuments(); });
    return () => sub.remove();
  }, []);
  const guarded = async (work: (owner: number) => Promise<void>) => {
    if (!current() || inFlight.current) return;
    const owner = ownership.current;
    inFlight.current = true; setBusy(true);
    try { await work(owner); }
    catch { if (current(owner)) setMessage('Connection interrupted. Check the status before trying again.'); }
    finally { if (current(owner)) { inFlight.current = false; setBusy(false); } }
  };
  const upload = (key: Doc) => guarded(async (owner) => {
    beginExternalActivity();
    let result: DocumentPicker.DocumentPickerResult;
    try { result = await DocumentPicker.getDocumentAsync({ type: ['image/*', 'application/pdf'], copyToCacheDirectory: true }); }
    finally { endExternalActivity(); }
    if (result.canceled || !current(owner)) return;
    const file = result.assets[0];
    if (file.size && file.size > MAX_UPLOAD_BYTES) { notify('File too large', `${docs[key]} must be 5 MB or smaller.`); return; }
    const encoded = await FileSystem.readAsStringAsync(file.uri, { encoding: FileSystem.EncodingType.Base64 });
    if (!current(owner)) return;
    if (encoded.length > Math.ceil(MAX_UPLOAD_BYTES / 3) * 4) { notify('File too large', `${docs[key]} must be 5 MB or smaller.`); return; }
    const sent = await banking.uploadDocument(key, encoded);
    if (!current(owner)) return;
    if (sent.success) setReady((p) => new Set(p).add(key));
    else notify('Could not upload', sent.message || 'Please try another file.');
  });
  // The face check is Zitch's own page, opened in the browser: it uses the live
  // camera and asks the customer to turn their head, so a saved photo can't pass.
  const verifyFace = () => guarded(async (owner) => {
    const link = await banking.livenessStart();
    if (!current(owner)) return;
    if (!link.success || !link.url) { setMessage(link.message || 'Face check is unavailable. Please try again.'); return; }
    beginExternalActivity();
    try { await WebBrowser.openBrowserAsync(link.url); } finally { endExternalActivity(); }
    if (current(owner)) await loadDocuments();
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

  const notice = !!message && <Text accessibilityLiveRegion="polite" style={{ ...text, color: c.brand }}>{message}</Text>;
  const missing = (key: string) => (tried && !form[key]?.trim() ? REQUIRED : undefined);
  const addressFields = (prefix: '' | 'addr_') => <>
    <Field editable={!busy} label="House number" value={form[prefix + 'houseNumber'] || ''} keyboardType="number-pad" maxLength={20}
      error={missing(prefix + 'houseNumber')} onChangeText={(v) => set(prefix + 'houseNumber', v)} />
    <Field editable={!busy} label="Street" value={form[prefix + 'streetName'] || ''} error={missing(prefix + 'streetName')}
      onChangeText={(v) => set(prefix + 'streetName', v)} />
    <Field editable={!busy} label="City or town" value={form[prefix + 'city'] || ''} error={missing(prefix + 'city')}
      onChangeText={(v) => set(prefix + 'city', v)} />
    <ComboField editable={!busy} label="State" value={form[prefix + 'state'] || ''} options={NIGERIA_STATES} error={missing(prefix + 'state')}
      onChange={(v) => setForm((p) => ({ ...p, [prefix + 'state']: v, ...(lgasFor(v).includes(p[prefix + 'localGovernment']) ? {} : { [prefix + 'localGovernment']: '' }) }))} />
    <ComboField editable={!busy} label="Local government area" value={form[prefix + 'localGovernment'] || ''} error={missing(prefix + 'localGovernment')}
      options={lgasFor(form[prefix + 'state'] || '')} onChange={(v) => set(prefix + 'localGovernment', v)} />
    <Field editable={!busy} label="Nearest landmark" value={form[prefix + 'nearestLandmark'] || ''} error={missing(prefix + 'nearestLandmark')}
      onChangeText={(v) => set(prefix + 'nearestLandmark', v)} />
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
          <Btn label="Verify my address" disabled={busy} onPress={() => { setTried(true); if (addressReady) void guarded(async (owner) => {
            const a = (k: string) => (form['addr_' + k] || '').trim();
            const result = await banking.address({ house_number: a('houseNumber'), street: a('streetName'), city: a('city'), state: a('state'),
              lga: a('localGovernment'), landmark: a('nearestLandmark'),
              address: `${a('houseNumber')} ${a('streetName')}, ${a('city')}, ${a('state')}`.slice(0, 255) });
            if (!current(owner)) return;
            setMessage(result.message || '');
            await load();
          }); }} />
        </> : <Card><Text style={text}>Address verification is not available yet.</Text></Card>}
        {notice}
      </View>
    </Screen>;
  }

  const askBvn = !status.bvn_on_file;
  const fields = ['nin', ...(askBvn ? ['bvn'] : []),
    ...(target === 2 ? ['houseNumber', 'streetName', 'city', 'state', 'localGovernment', 'nearestLandmark'] : [])];
  const files = ['idCardFront', 'userPhoto', 'utilityBill', 'customerSignature'];
  const complete = fields.every((k) => !!form[k]?.trim()) && files.every((k) => ready.has(k))
    && form.nin?.length === 11 && (!askBvn || form.bvn?.length === 11);
  const elevenDigits = (key: string, label: string) => (tried && (form[key] || '').length !== 11
    ? (form[key] ? `Enter your 11-digit ${label}` : REQUIRED) : undefined);
  // A file row: outlined in red with a note when it's still missing after "Upgrade".
  const fileRow = (key: string, label: string, onPress: () => void) => {
    const done = ready.has(key);
    return <View key={key} style={{ gap: 6 }}>
      <Btn variant="outline" label={`${done ? '✓ ' : ''}${label}`} disabled={busy} onPress={onPress}
        style={tried && !done ? { borderColor: c.red, borderWidth: 1.5 } : undefined} />
      {tried && !done && <Text accessibilityRole="alert" style={{ color: c.red, fontSize: 13, fontFamily: font.medium }}>{REQUIRED}</Text>}
    </View>;
  };
  const submit = () => {
    setTried(true);
    if (!complete) { setMessage('Complete the fields marked in red.'); return; }
    void guarded(async (owner) => {
      const body: Record<string, string> = { ...form, accountName: status.account_name || '', phoneNumber: status.profile?.phoneNo || '', email: status.profile?.email || '' };
      Object.keys(body).filter((k) => k.startsWith('addr_')).forEach((k) => delete body[k]);
      const result = await banking.upgrade(target, body);
      if (!current(owner)) return;
      setMessage(result.message || (result.success ? 'Upgrade approved.' : 'We could not submit your upgrade.'));
      if (result.success || result.pending) { setForm(fresh()); setReady(new Set()); setSubmitted(true); await load(); }
    });
  };

  if (submitted) return <Screen header={<Header title={`Upgrade to Tier ${target}`} />}>
    <View style={{ gap: 16 }}><Card><Text style={text}>{message || 'Your upgrade is being reviewed.'}</Text></Card>
      <Btn label="Done" onPress={() => router.back()} /></View>
  </Screen>;

  return <Screen header={<Header title={`Upgrade to Tier ${target}`} />}>
    <View style={{ gap: 16, paddingBottom: 32 }}>
      <Text style={heading}>NIN</Text>
      {askBvn && <Field editable={!busy} label="BVN" value={form.bvn || ''} maxLength={11} keyboardType="number-pad" secureTextEntry autoComplete="off"
        error={elevenDigits('bvn', 'BVN')} onChangeText={(v) => set('bvn', digits(v, 11))} />}
      <Field editable={!busy} label="NIN" value={form.nin || ''} maxLength={11} keyboardType="number-pad" secureTextEntry autoComplete="off"
        error={elevenDigits('nin', 'NIN')} onChangeText={(v) => set('nin', digits(v, 11))} />
      {fileRow('idCardFront', ready.has('idCardFront') ? 'NIN slip uploaded' : 'Upload NIN slip', () => void upload('idCardFront'))}

      {target === 2 && <><Text style={heading}>Home address</Text>{addressFields('')}</>}

      <Text style={heading}>Face and documents</Text>
      {fileRow('userPhoto', ready.has('userPhoto') ? 'Face verified' : 'Verify your face', () => void verifyFace())}
      {fileRow('utilityBill', ready.has('utilityBill') ? 'Utility bill uploaded' : 'Upload utility bill', () => void upload('utilityBill'))}
      {fileRow('customerSignature', ready.has('customerSignature') ? 'Signature uploaded' : 'Upload signature', () => void upload('customerSignature'))}

      <View style={{ gap: 8 }}><Text style={heading}>Are you a politically exposed person?</Text>
        <PillTabs scroll={false} options={[{ v: 'NO', label: 'No' }, { v: 'YES', label: 'Yes' }]} value={form.pep} onChange={(v) => { if (!busy) set('pep', v); }} /></View>
      {notice}
      <Btn label={busy ? 'Please wait…' : `Upgrade to Tier ${target}`} disabled={busy} onPress={submit} />
    </View>
  </Screen>;
}

export default function UpgradeScreen() {
  return <AuthGuard><UpgradeContent /></AuthGuard>;
}
