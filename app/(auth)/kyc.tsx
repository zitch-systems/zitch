import React, { useCallback, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useFocusEffect } from 'expo-router';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import * as WebBrowser from 'expo-web-browser';
import { notify } from '@/components/design/Notify';
import { getToken } from '@/lib/secureStore';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { apiJson } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, money, NText, SelectRow, PickerSheet } from '@/components/design/ui';
import { NIGERIAN_STATES, canonicalState } from '@/constants/nigeria';
import { useTheme, font } from '@/lib/theme';
import AuthGuard from '@/components/AuthGuard';

type Status = {
  tier: number; tier_name?: string; transaction_limit: string;
  daily_transfer_limit?: string; daily_bill_limit?: string;
  bvn_verified: boolean; nin_verified: boolean; face_verified: boolean;
  address_verified?: boolean; id_document_verified?: boolean;
  email?: string; email_verified?: boolean; email_verification_required?: boolean;
  bank_tier?: number;
  bank_tier_limits?: { single_inflow?: string | null; daily_spend?: string | null; max_balance?: string | null };
  // Which rail each step runs on. The server decides, because it is the only side
  // that knows which bank products are actually keyed on this deploy — a screen
  // that hardcoded "bank" would show a button that 503s, and one that hardcoded
  // "document" would ask for a utility bill nobody reads.
  face_rail?: 'wema' | 'document';
  address_rail?: 'wema' | 'document';
};

const MAX_IMAGE_BASE64 = 2_800_000;

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
  const [bvnDelivery, setBvnDelivery] = useState('your registered phone');
  const [nin, setNin] = useState('');
  const [ninImage, setNinImage] = useState(''); // base64 of the NIN slip
  const [ninOtp, setNinOtp] = useState('');
  const [ninSent, setNinSent] = useState(false);
  const [ninDelivery, setNinDelivery] = useState('your registered phone');
  const [address, setAddress] = useState('');
  const [city, setCity] = useState('');
  const [stateName, setStateName] = useState('');
  const [statePicker, setStatePicker] = useState(false);
  const [docSource, setDocSource] = useState(false);
  const [addressDoc, setAddressDoc] = useState(''); // base64 proof of address
  const [idImage, setIdImage] = useState(''); // base64 of the government ID
  const [emailOtp, setEmailOtp] = useState('');
  const [emailAddr, setEmailAddr] = useState('');
  const [emailOtpSent, setEmailOtpSent] = useState(false);
  const [busy, setBusy] = useState(false);
  // Bank face check: which identity the customer is presenting, and the number
  // itself (held only long enough to build the bank's URL — never persisted).
  const [faceIdType, setFaceIdType] = useState<'bvn' | 'nin'>('bvn');
  const [faceId, setFaceId] = useState('');
  const [facePolling, setFacePolling] = useState(false);
  // The address rail decides whether a proof-of-address document is even asked for.
  const bankAddress = status?.address_rail === 'wema';

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
      if (res.success) {
        setBvnDelivery(res.delivery || 'your registered phone');
        setBvnSent(true);
        notify('Code sent', res.message || 'Enter the verification code.');
      }
      else notify('Error', res.message || 'Could not start BVN verification');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

  // --- NIN: provider + document check -> ownership code -> confirm ---
  const startNin = async () => {
    setBusy(true);
    try {
      const res = await apiJson('/api/kyc/nin/', { nin, nin_image: ninImage });
      if (res.success && res.otp_required) {
        setNinDelivery(res.delivery || 'your registered phone');
        setNinSent(true);
        notify('Code sent', res.message || 'Enter the verification code.');
      } else if (res.success) {
        // Local development keeps its provider mock immediate; production and
        // production-simulation always take the ownership challenge above.
        setStatus(res);
        setNin('');
        setNinImage('');
        notify('Success', 'NIN verified');
      } else notify('Error', res.message || 'NIN verification failed');
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };
  const confirmNin = async () => {
    setBusy(true);
    try {
      const res = await apiJson('/api/kyc/nin/confirm/', { otp: ninOtp });
      if (res.success) {
        setStatus(res);
        setNinSent(false);
        setNin('');
        setNinImage('');
        setNinOtp('');
        notify('Success', 'NIN verified');
      } else notify('Error', res.message || 'Incorrect code');
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

  // --- Generic photo picker (NIN slip / ID document / proof of address) ---
  //
  // `crop` is opt-IN. It used to be forced on for every document, and the crop UI
  // imposes an aspect ratio: on a full-page utility bill that means the customer
  // trims their own proof of address, and the line the verifier is looking for —
  // the address — is one of the things most easily trimmed off. Nothing here
  // needs a squared-off image, so nothing here asks for one by default.
  const pickImage = async (set: (b64: string) => void, crop = false) => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) { notify('Photos needed', 'Allow photo access to upload your document.'); return; }
    beginExternalActivity(); // don't let the app-lock fire while the picker is up
    try {
      const res = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images, base64: true, quality: 0.25, allowsEditing: crop,
      });
      if (res.canceled || !res.assets?.[0]?.base64) return;
      if (res.assets[0].base64.length > MAX_IMAGE_BASE64) {
        notify('Image too large', 'Choose a photo under 2 MB.');
        return;
      }
      set(res.assets[0].base64);
    } finally { endExternalActivity(); }
  };
  const pickNinSlip = () => pickImage(setNinImage);

  // --- PDF picker (proof of address) ---
  //
  // A bank statement or utility bill arrives as a PDF far more often than as a
  // photo — it is emailed, not photographed — and the photo picker cannot see one.
  //
  // Kept SEPARATE from the image path rather than merged into one file browser,
  // because the two need different handling: ImagePicker re-encodes a photo at
  // quality 0.25, which is what keeps a phone camera's 4MB original under the
  // upload cap. A document browser hands back the bytes as they are, so routing
  // photos through it would start rejecting exactly the uploads that work today.
  const pickPdf = async (set: (b64: string) => void) => {
    beginExternalActivity();
    try {
      const res = await DocumentPicker.getDocumentAsync({
        type: 'application/pdf', copyToCacheDirectory: true, multiple: false,
      });
      if (res.canceled || !res.assets?.[0]?.uri) return;
      const asset = res.assets[0];
      // Checked before reading, not after: base64 of the file is ~1.37x its size,
      // and there is no reason to pull a 20MB statement into memory to find that
      // out.
      if (typeof asset.size === 'number' && asset.size * 1.37 > MAX_IMAGE_BASE64) {
        notify('File too large', 'Choose a PDF under 2 MB.');
        return;
      }
      const FS = await import('expo-file-system/legacy');
      const b64 = await FS.readAsStringAsync(asset.uri, { encoding: 'base64' });
      if (b64.length > MAX_IMAGE_BASE64) {
        notify('File too large', 'Choose a PDF under 2 MB.');
        return;
      }
      set(b64);
    } catch {
      notify('Could not read that file', 'Try another file, or upload a photo instead.');
    } finally { endExternalActivity(); }
  };

  // --- Face check, bank rail: the BANK runs liveness in its own hosted verifier.
  //
  // We open it, and that is all we do. The result never comes back through the
  // browser — the bank POSTs it to our server, which is the only version an app
  // cannot fake by driving its own WebView. So the flow is: start (server mints a
  // one-time session), open, then poll our own API until the server says verified.
  //
  // The identity number is asked for again rather than reused, because we keep only
  // a keyed hash of it: the raw BVN/NIN is sent to the bank and dropped. Retyping
  // eleven digits is the cost of not storing them.
  const verifyFaceWithBank = async () => {
    const raw = faceId.trim();
    if (raw.length !== 11) { notify('Check the number', 'Enter your 11-digit BVN or NIN.'); return; }
    setBusy(true);
    try {
      const res = await apiJson('/api/kyc/face/start/', faceIdType === 'bvn' ? { bvn: raw } : { nin: raw });
      if (!res.success || !res.url) {
        notify('Not available', res.message || 'Face verification is unavailable right now.');
        return;
      }
      setFacePolling(true);
      beginExternalActivity(); // the app-lock must not fire while the bank's page is up
      try {
        await WebBrowser.openBrowserAsync(res.url);
      } finally { endExternalActivity(); }
      // Closing the browser is not the same as passing: the customer may have
      // abandoned it, and the bank's callback may still be in flight. Poll rather
      // than assume either way.
      await pollFace(res.session);
    } catch { notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); setFacePolling(false); }
  };

  const pollFace = async (session: string) => {
    for (let i = 0; i < 20; i += 1) {
      try {
        const res = await apiJson('/api/kyc/face/status/', { session });
        if (res.status === 'verified') {
          setStatus(res);
          setFaceId('');
          notify('Success', 'Face verification complete');
          return;
        }
        if (res.status === 'failed' || res.status === 'expired') {
          notify('Not verified', 'The face check did not complete. You can try again.');
          return;
        }
      } catch { /* keep polling — a dropped request is not a failed check */ }
      await new Promise((r) => setTimeout(r, 3000));
    }
    // Out of patience, not out of hope: the callback can still land, and the next
    // screen load will show it. Never tell the customer it failed when we don't know.
    notify('Still checking', 'We haven’t heard back yet. Reopen this screen shortly.');
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
        cameraType: ImagePicker.CameraType.front, base64: true, quality: 0.25, allowsEditing: false,
      });
    } finally { endExternalActivity(); }
    if (shot.canceled || !shot.assets?.[0]?.base64) return;
    if (shot.assets[0].base64.length > MAX_IMAGE_BASE64) {
      notify('Image too large', 'Retake the photo at a lower device resolution.');
      return;
    }
    submit('/api/kyc/face/', { selfie: shot.assets[0].base64 }, 'Selfie');
  };

  return (
    <Screen>
      <Header title="Verify identity" sub="Zitch and partner-bank limits are separate" onBack={() => router.back()} />

      {status && (
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: c.surface3, borderRadius: 16, padding: 16, marginBottom: 4 }}>
          <View>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>Zitch verification level</Text>
            <Text style={{ fontSize: 20, fontFamily: font.extrabold, color: c.ink1 }}>Tier {status.tier}{status.tier_name ? ` · ${status.tier_name}` : ''}</Text>
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>Per-transaction limit</Text>
            <Text style={{ fontSize: 16, fontFamily: font.bold, color: c.brand, fontVariant: ['tabular-nums'] }}>{money(Number(status.transaction_limit))}</Text>
          </View>
        </View>
      )}

      {status && (
        <View style={{ backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, borderRadius: 16, padding: 16, marginTop: 10, marginBottom: 2 }}>
          <Text style={{ fontFamily: font.bold, color: c.ink1, fontSize: 14.5 }}>
            Partner bank account tier {status.bank_tier ? status.bank_tier : 'not yet synced'}
          </Text>
          {status.bank_tier === 1 && (
            <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 12.5, lineHeight: 19, marginTop: 6 }}>
              Single inflow ₦50,000 · daily spend ₦30,000 · maximum balance ₦300,000.
            </Text>
          )}
          {status.bank_tier === 2 && (
            <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 12.5, lineHeight: 19, marginTop: 6 }}>
              Single inflow ₦100,000 · daily spend ₦100,000 · maximum balance ₦500,000.
            </Text>
          )}
          {status.bank_tier === 3 && (
            <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 12.5, lineHeight: 19, marginTop: 6 }}>
              No bank tier inflow, daily-spend or balance cap.
            </Text>
          )}
          {!status.bank_tier && (
            <Text style={{ fontFamily: font.regular, color: c.ink3, fontSize: 12.5, lineHeight: 19, marginTop: 6 }}>
              Set up your funding account to read its bank tier. Until it syncs, assume Tier 1 limits.
            </Text>
          )}
        </View>
      )}

      {status && !status.email_verified ? (
        <KycRow icon="mail" title="Confirm your email"
          sub={status.email
            ? `We'll send a code to ${status.email} — ${status.email_verification_required ? 'required before identity verification' : 'required to reach Tier 1'}`
            : 'Add and confirm an email — required to reach Tier 1'}
          done={false}>
          {/* Tier 1 requires a verified email for every account. For chat-onboarded
              accounts the identity steps below are additionally closed server-side
              until it's confirmed, so surfacing it first saves a confusing 403. */}
          {!emailOtpSent ? (
            <>
              {!status.email ? (
                <>
                  <Field value={emailAddr} onChangeText={setEmailAddr} keyboardType="email-address" autoCapitalize="none" placeholder="you@example.com" />
                  <View style={{ height: 10 }} />
                </>
              ) : null}
              <Btn label="Send code" size="md" disabled={busy || (!status.email && !emailAddr.includes('@'))}
                onPress={async () => {
                  setBusy(true);
                  try {
                    const res = await apiJson('/api/email/verify/start/', emailAddr ? { email: emailAddr.trim() } : {});
                    if (res.success) { setEmailOtpSent(true); notify('Code sent', res.message || 'Check your inbox.'); }
                    else notify('Error', res.message || 'Could not send the code');
                  } catch { notify('Error', 'Something went wrong.'); }
                  finally { setBusy(false); }
                }} />
            </>
          ) : (
            <>
              <Field value={emailOtp} onChangeText={(v) => setEmailOtp(v.replace(/\D/g, '').slice(0, 6))} keyboardType="number-pad" placeholder="6-digit code from the email" />
              <View style={{ height: 10 }} />
              <Btn label="Confirm email" size="md" disabled={busy || emailOtp.length !== 6}
                onPress={() => submit('/api/email/verify/confirm/', { otp: emailOtp }, 'Email')} />
              <Text onPress={() => setEmailOtpSent(false)} style={{ textAlign: 'center', marginTop: 10, fontSize: 13, color: c.brand, fontFamily: font.semibold }}>Resend code</Text>
            </>
          )}
        </KycRow>
      ) : null}

      <KycRow icon="bank" title="BVN" sub="We'll send a code to verify it's yours" done={!!status?.bvn_verified}>
        {!bvnSent ? (
          <>
            <Field value={bvn} onChangeText={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))} keyboardType="number-pad" placeholder="Enter 11-digit BVN" />
            <View style={{ height: 10 }} />
            <Btn label="Send verification code" size="md" disabled={busy || bvn.length !== 11} onPress={startBvn} />
          </>
        ) : (
          <>
            <Text style={{ fontSize: 12.5, color: c.ink3, marginBottom: 8, fontFamily: font.regular }}>Enter the code sent to {bvnDelivery}.</Text>
            <Field value={bvnOtp} onChangeText={(v) => setBvnOtp(v.replace(/\D/g, '').slice(0, 6))} keyboardType="number-pad" placeholder="6-digit code" />
            <View style={{ height: 10 }} />
            <Btn label="Confirm BVN" size="md" disabled={busy || bvnOtp.length !== 6} onPress={confirmBvn} />
            <Text onPress={() => { setBvnSent(false); setBvnOtp(''); }} style={{ textAlign: 'center', marginTop: 10, fontSize: 13, color: c.brand, fontFamily: font.semibold }}>Change BVN</Text>
          </>
        )}
      </KycRow>

      <KycRow icon="user" title="NIN" sub="Number + a photo of your NIN slip" done={!!status?.nin_verified}>
        {!ninSent ? (
          <>
            <Field value={nin} onChangeText={(v) => setNin(v.replace(/\D/g, '').slice(0, 11))} keyboardType="number-pad" placeholder="Enter 11-digit NIN" />
            <View style={{ height: 10 }} />
            <Btn label={ninImage ? 'NIN slip added ✓' : 'Upload your NIN slip'} icon="copy" size="md" variant="outline" disabled={busy} onPress={pickNinSlip} />
            <View style={{ height: 10 }} />
            <Btn label="Send verification code" size="md" disabled={busy || nin.length !== 11 || !ninImage} onPress={startNin} />
          </>
        ) : (
          <>
            <Text style={{ fontSize: 12.5, color: c.ink3, marginBottom: 8, fontFamily: font.regular }}>Enter the code sent to {ninDelivery}.</Text>
            <Field value={ninOtp} onChangeText={(v) => setNinOtp(v.replace(/\D/g, '').slice(0, 6))} keyboardType="number-pad" placeholder="6-digit code" />
            <View style={{ height: 10 }} />
            <Btn label="Confirm NIN" size="md" disabled={busy || ninOtp.length !== 6} onPress={confirmNin} />
            <Text onPress={() => { setNinSent(false); setNinOtp(''); }} style={{ textAlign: 'center', marginTop: 10, fontSize: 13, color: c.brand, fontFamily: font.semibold }}>Change NIN</Text>
          </>
        )}
      </KycRow>

      {status?.face_rail === 'wema' ? (
        <KycRow icon="faceid" title="Face verification"
          sub="Verified by your bank — unlocks Tier 2" done={!!status?.face_verified}>
          <Text style={{ fontSize: 12.5, color: c.ink3, marginBottom: 10, fontFamily: font.regular, lineHeight: 19 }}>
            Your bank runs this check against your BVN or NIN. We open their secure page —
            your face is never sent to or stored by Zitch.
          </Text>
          <View style={{ flexDirection: 'row', gap: 10, marginBottom: 10 }}>
            {(['bvn', 'nin'] as const).map((k) => (
              <View key={k} style={{ flex: 1 }}>
                <Btn label={k.toUpperCase()} size="md"
                  variant={faceIdType === k ? 'primary' : 'outline'}
                  disabled={busy}
                  onPress={() => { setFaceIdType(k); setFaceId(''); }} />
              </View>
            ))}
          </View>
          <Field value={faceId} onChangeText={(v) => setFaceId(v.replace(/\D/g, '').slice(0, 11))}
            keyboardType="number-pad" placeholder={`Enter your 11-digit ${faceIdType.toUpperCase()}`} />
          <View style={{ height: 10 }} />
          <Btn label={facePolling ? 'Waiting for your bank…' : 'Verify with your bank'} icon="faceid" size="md"
            disabled={busy || facePolling || faceId.length !== 11} onPress={verifyFaceWithBank} />
          {facePolling ? (
            <Text style={{ fontSize: 12, color: c.ink3, marginTop: 10, textAlign: 'center', fontFamily: font.regular }}>
              Finish the check in the page that opened, then come back here.
            </Text>
          ) : null}
        </KycRow>
      ) : (
        <KycRow icon="faceid" title="Selfie verification" sub="A quick selfie — unlocks Tier 2" done={!!status?.face_verified}>
          <Btn label="Take a selfie" icon="faceid" size="md" variant="outline" disabled={busy} onPress={verifySelfie} />
        </KycRow>
      )}

      <KycRow icon="home"
        title="Residential address"
        sub={bankAddress ? 'Verified by your bank — unlocks Tier 2'
                         : 'Address + proof of address — unlocks Tier 2'}
        done={!!status?.address_verified}>
        <Field value={address} onChangeText={setAddress} placeholder="Street address" />
        <View style={{ height: 10 }} />
        <View style={{ flexDirection: 'row', gap: 10 }}>
          <View style={{ flex: 1 }}><Field value={city} onChangeText={setCity} placeholder="City / LGA" /></View>
          {/* A closed list, not free text. "Lagos", "lagos", "Lagos State" and
              "LAG" all used to arrive as different values for one place, and
              every reader downstream — address verification, the compliance
              export, anything grouping by region — had to guess which meant the
              same thing. The sheet is searchable, so 37 entries stay a list you
              type at rather than one you scroll. */}
          <View style={{ flex: 1 }}>
            <SelectRow compact placeholder="State" value={stateName} onPress={() => setStatePicker(true)} />
          </View>
        </View>
        <PickerSheet
          open={statePicker}
          onClose={() => setStatePicker(false)}
          title="State"
          searchable
          searchPlaceholder="Type a state"
          emptyLabel="No Nigerian state matches that."
          options={NIGERIAN_STATES.map((v) => ({ v, label: v }))}
          value={stateName}
          onPick={setStateName}
        />
        <View style={{ height: 10 }} />
        {/* On the bank rail the document section is not merely optional — it is
            absent. Wema verifies the address itself and lifts the NUBAN to its
            Tier 3 on that; asking for a utility bill nobody reads would be
            theatre, and a slow, 2 MB one at that. */}
        {bankAddress ? (
          <Text style={{ fontSize: 12.5, color: c.ink3, marginBottom: 8, fontFamily: font.regular, lineHeight: 19 }}>
            Your bank verifies this address directly — no document upload needed.
          </Text>
        ) : (
        <>
        {/* Named so the user knows what counts before opening the picker — the
            server refuses this step without a document, and a rejection after
            the fact is a worse way to learn the requirement. */}
        <Text style={{ fontSize: 12.5, color: c.ink3, marginBottom: 8, fontFamily: font.regular }}>
          Upload a utility bill, bank statement or tenancy agreement showing this address (issued in the last 3 months). JPEG, PNG or PDF, up to 2 MB.
        </Text>
        <Btn label={addressDoc ? 'Proof of address added ✓' : 'Upload proof of address'} icon="copy" size="md" variant="outline" disabled={busy} onPress={() => setDocSource(true)} />
        {/* Two sources, named, because they are genuinely different files: a photo
            of a bill, or the PDF the bank emailed. Asking is one tap and removes
            the guesswork of a file browser that may or may not show photos. */}
        <PickerSheet
          open={docSource}
          onClose={() => setDocSource(false)}
          title="Upload proof of address"
          value=""
          options={[
            { v: 'photo', label: 'Photo (JPEG or PNG)', sub: 'From your gallery', icon: 'copy' },
            { v: 'pdf', label: 'PDF file', sub: 'A statement or bill you were emailed', icon: 'copy' },
          ]}
          onPick={(v) => { if (v === 'pdf') pickPdf(setAddressDoc); else pickImage(setAddressDoc); }}
        />
        </>
        )}
        <View style={{ height: 10 }} />
        <Btn label={bankAddress ? 'Verify with your bank' : 'Verify address'} size="md"
          disabled={busy || address.trim().length < 6 || (!bankAddress && !addressDoc)}
          onPress={() => submit('/api/kyc/address/', { address, city, state: canonicalState(stateName) || stateName, document: addressDoc }, 'Address')} />
      </KycRow>

      <KycRow icon="shield" title="Government ID" sub="Passport, driver's licence or voter's card — unlocks Tier 3" done={!!status?.id_document_verified}>
        <Btn label={idImage ? 'ID added ✓' : 'Upload your government ID'} icon="copy" size="md" variant="outline" disabled={busy} onPress={() => pickImage(setIdImage)} />
        <View style={{ height: 10 }} />
        <Btn label="Verify ID document" size="md" disabled={busy || !idImage}
          onPress={() => submit('/api/kyc/id/', { image: idImage, doc_type: 'government_id' }, 'ID document')} />
      </KycRow>

      <NText style={{ fontSize: 12, color: c.ink3, marginTop: 16, lineHeight: 18, fontFamily: font.regular }}>
        Zitch app limits: Unverified ₦20,000 · Verified (BVN + NIN) ₦50,000 · Enhanced (+ selfie + address) ₦200,000 · Premium (+ government ID) ₦5,000,000 per transaction. These are additional to the partner bank limits above. Raw BVN, NIN, proof-of-address and ID images are not retained by Zitch.
      </NText>
    </Screen>
  );
};

// Post-login screen living in the unguarded (auth) group: gate it explicitly
// so a deep link can't render it without a valid, unlocked session (the API
// would 401 anyway — this keeps the surface consistent with the other groups).
const GuardedKyc = () => (
  <AuthGuard>
    <Kyc />
  </AuthGuard>
);

export default GuardedKyc;
