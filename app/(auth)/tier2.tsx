import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { Loading } from '@/components/design/Loading';
import FaceVerifyModal from '@/components/design/FaceVerifyModal';
import AuthGuard from '@/components/AuthGuard';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Card, NText, money } from '@/components/design/ui';
import { StepTitle } from '@/components/kyc/Onboarding';
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import { getSessionGeneration } from '@/lib/secureStore';
import { classifyKycResponse, kycService, resolveOwnershipOtpRoute, type KycStatus } from '@/lib/services/kyc';
import { tierLadder, type LadderTier } from '@/lib/tierLadder';
import { useTheme, font } from '@/lib/theme';

type Status = KycStatus & { tier_ladder?: LadderTier[] };

/** The only page the live face check may load (same check as kyc.tsx runPrembly). */
const FACE_ORIGIN = 'https://sdk-live.prembly.com';
const isTrustedFaceUrl = (url: string) => {
  try {
    const target = new URL(url);
    return target.origin === FACE_ORIGIN && !target.username && !target.password;
  } catch { return false; }
};

/** While the face page is open the server is asked this often whether it is done. */
const POLL_MS = 3000;
const GREEN = '#15803D';

const amountLabel = (value: unknown) => (typeof value === 'string' || typeof value === 'number')
  && String(value).trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0
  ? money(Number(value)) : 'Not confirmed';

/** 3456 7890 123 — grouped for reading; the digits alone are sent. */
const groupNin = (digits: string) => [digits.slice(0, 4), digits.slice(4, 8), digits.slice(8)].filter(Boolean).join(' ');

const faceVerified = (r: Partial<Status> & { status?: unknown }) =>
  !!r.success && r.status === 'verified' && r.face_verified === true;

const Done = ({ label }: { label: string }) => (
  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginLeft: 34 }}>
    <ZIcon name="check" size={14} color={GREEN} stroke={3} />
    <Text style={{ fontSize: 13, fontFamily: font.bold, color: GREEN }}>{label}</Text>
  </View>
);

const Tier2 = () => {
  const { c, theme } = useTheme();
  const params = useLocalSearchParams<{ from?: string }>();
  const [status, setStatus] = useState<Status | null>(null);
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [nin, setNin] = useState('');
  const [ninSent, setNinSent] = useState(false);
  const [ninOtp, setNinOtp] = useState('');
  const [ninTrackingId, setNinTrackingId] = useState('');
  const [ninDestination, setNinDestination] = useState('');
  const [faceUrl, setFaceUrl] = useState('');
  const [faceSession, setFaceSession] = useState('');
  const [faceOpen, setFaceOpen] = useState(false);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const screenGeneration = useRef(getSessionGeneration()).current;
  const loadGeneration = useRef(0);
  const pollGeneration = useRef(0);

  const live = useCallback(() => mounted.current && screenGeneration === getSessionGeneration(), [screenGeneration]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; pollGeneration.current += 1; };
  }, []);

  const load = useCallback(async (): Promise<Status | null> => {
    const request = ++loadGeneration.current;
    const current = () => live() && request === loadGeneration.current;
    try {
      const res = await kycService.getStatus() as Status;
      if (!current()) return null;
      if (res?.success) { setStatus(res); setLoadError(''); return res; }
      setLoadError(res?.message || 'We could not load your verification status. Please try again.');
    } catch {
      if (current()) setLoadError('We could not load your verification status. Please try again.');
    }
    return null;
  }, [live]);

  useFocusEffect(useCallback(() => {
    void load();
    return () => { pollGeneration.current += 1; inFlight.current = false; setBusy(false); setFaceOpen(false); };
  }, [load]));

  const begin = () => {
    if (inFlight.current || !live()) return false;
    inFlight.current = true;
    setBusy(true);
    return true;
  };
  const end = () => {
    inFlight.current = false;
    if (mounted.current) setBusy(false);
  };

  const ninDone = !!status?.nin_verified;
  const faceDone = !!status?.face_verified;
  const faceUnavailable = status?.tier2_face_available === false;

  // --- Step 1: NIN, with the ownership code when the server asks for one ---
  const verifyNin = async () => {
    if (nin.length !== 11 || !begin()) return;
    try {
      const res = await kycService.startNin(nin);
      if (!live()) return;
      if (res.pending) {
        notify('Verification processing', res.message || 'Your NIN check is still processing. Check again shortly.', 'info');
      } else if (res.otp_required) {
        const challenge = resolveOwnershipOtpRoute(res);
        if (!challenge) {
          notify('Error', 'We could not start a secure verification challenge. Please try again.');
        } else {
          setNinTrackingId(challenge.trackingId);
          setNinDestination(res.delivery || res.otp_destination || '');
          setNinOtp('');
          setNinSent(true);
          notify('NIN code requested', res.message || 'Enter the verification code to finish.', 'success');
        }
      } else if (res.identity_review_required) {
        notify('Verification needs review', res.message || 'Your NIN needs review.', 'info');
        await load();
      } else if (res.success && !res.upgrade_required) {
        const fresh = await load();
        if (!live()) return;
        if (fresh?.nin_verified) { setNin(''); notify('NIN verified', undefined, 'success'); }
        else notify('Verification submitted', fresh?.message || 'Your NIN was received. Check again shortly for the result.', 'info');
      } else {
        notify('Could not verify NIN', res.message || 'Check the number and try again.');
      }
    } catch { if (live()) notify('Error', 'Something went wrong.'); }
    finally { end(); }
  };

  const confirmNin = async () => {
    if (ninOtp.length !== 6 || !begin()) return;
    try {
      const res = await kycService.confirmNin(ninTrackingId, ninOtp);
      if (!live()) return;
      const outcome = classifyKycResponse(res, ['nin_verified']);
      if (outcome === 'pending') {
        notify('Verification processing', res.message || 'Your NIN check is still processing. Check again shortly.', 'info');
      } else if (outcome === 'review') {
        notify('Verification needs review', res.message || 'Your NIN needs review.', 'info');
        await load();
      } else if (outcome === 'success' || outcome === 'unverified') {
        const fresh = await load();
        if (!live()) return;
        if (fresh?.nin_verified) {
          setNin(''); setNinOtp(''); setNinSent(false); setNinTrackingId(''); setNinDestination('');
          notify('NIN verified', undefined, 'success');
        } else notify('Verification submitted', fresh?.message || 'Your NIN was received. Check again shortly for the result.', 'info');
      } else notify('Verification failed', res.message || 'Check the code and try again.');
    } catch { if (live()) notify('Verification failed', 'Check your connection and try again.'); }
    finally { end(); }
  };

  const resendNin = async () => {
    if (!ninSent || !begin()) return;
    try {
      const res = await kycService.resendNin(ninTrackingId, nin);
      if (!live()) return;
      const route = resolveOwnershipOtpRoute(res);
      if (res.success && (ninTrackingId || route)) {
        if (!ninTrackingId && route) setNinTrackingId(route.trackingId);
        setNinOtp('');
        setNinDestination(res.delivery || res.otp_destination || '');
        notify('NIN code resent', res.message || 'Enter the latest verification code.', 'success');
      } else notify('Error', res.message || 'Could not resend the NIN code');
    } catch { if (live()) notify('Error', 'Something went wrong.'); }
    finally { end(); }
  };

  // --- Step 2: the live face check, held in-app; the server says when it is done ---
  const checkFace = useCallback(async (session: string, generation: number): Promise<boolean> => {
    const res = await kycService.getLivenessStatus(session);
    if (!live() || generation !== pollGeneration.current) return false;
    if (faceVerified(res)) {
      pollGeneration.current += 1;
      setFaceOpen(false);
      setFaceSession('');
      setStatus((s) => (s ? { ...s, ...res } : res as Status));
      notify('Face check complete', undefined, 'success');
      return true;
    }
    if (res.status === 'failed' || res.status === 'expired') {
      pollGeneration.current += 1;
      setFaceOpen(false);
      setFaceSession('');
      notify('Face check not confirmed', res.message || 'Please start the face check again.', 'info');
      return true;
    }
    return false;
  }, [live]);

  useEffect(() => {
    if (!faceOpen || !faceSession) return undefined;
    const generation = ++pollGeneration.current;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = () => {
      timer = setTimeout(async () => {
        if (generation !== pollGeneration.current) return;
        let finished = false;
        try { finished = await checkFace(faceSession, generation); } catch { /* keep waiting */ }
        if (!finished && generation === pollGeneration.current) tick();
      }, POLL_MS);
    };
    tick();
    return () => { if (timer) clearTimeout(timer); };
  }, [faceOpen, faceSession, checkFace]);

  const startFace = async () => {
    if (faceUnavailable || !begin()) return;
    try {
      const started = await kycService.startLiveness();
      if (!live()) return;
      if (!started.success || !started.session || !started.url) {
        notify('Face check', started.message || 'Could not start the face check.', 'info');
        return;
      }
      if (!isTrustedFaceUrl(started.url)) {
        notify('Face check unavailable', 'The verification link could not be confirmed.');
        return;
      }
      setFaceUrl(started.url);
      setFaceSession(started.session);
      setFaceOpen(true);
    } catch { if (live()) notify('Face check', 'Could not start the face check. Please try again.'); }
    finally { end(); }
  };

  const closeFace = () => {
    setFaceOpen(false);
    const session = faceSession;
    if (!session) return;
    const generation = ++pollGeneration.current;
    void checkFace(session, generation).catch(() => undefined);
  };

  // --- Submit: the server confirms Tier 2, then the address check is asked to start ---
  const submit = async () => {
    if (!ninDone || !faceDone || !begin()) return;
    try {
      const fresh = await load();
      if (!live()) return;
      if (fresh?.nin_verified && fresh.face_verified && fresh.tier >= 2) {
        // Best effort: Tier 3 is the sign-up address; its check needs no input.
        void apiJson(EP.kyc.address).catch(() => undefined);
        notify('You’re now Tier 2', 'Your new limits apply right away.', 'success');
        if (params.from === 'limits' && router.canGoBack()) router.back();
        else router.replace('/accountlimits');
      } else {
        notify('Not finished yet', fresh?.message || 'Finish both steps to submit.', 'info');
      }
    } catch { if (live()) notify('Error', 'Something went wrong.'); }
    finally { end(); }
  };

  if (!status) {
    return (
      <Screen>
        <Header title="Upgrade to Tier 2" sub="Your NIN and a quick face check" onBack={() => router.back()} />
        {loadError ? (
          <Card>
            <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginBottom: 16 }}>{loadError}</Text>
            <Btn label="Try again" onPress={() => void load()} />
          </Card>
        ) : <Loading label="Checking your verification…" />}
      </Screen>
    );
  }

  const tint = theme === 'dark' ? 'rgba(234,251,247,0.07)' : 'rgba(6,110,102,0.06)';
  const tier2 = tierLadder(status.tier_ladder).find((t) => t.tier === 2);
  const canSubmit = ninDone && faceDone && !busy;
  const hint = !faceDone ? 'Finish the face check to submit.' : !ninDone ? 'Verify your NIN to submit.' : '';

  const OutlineBtn = ({ label, icon, onPress, disabled }: { label: string; icon: string; onPress: () => void; disabled?: boolean }) => (
    <Pressable
      onPress={disabled ? undefined : onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: !!disabled }}
      style={({ pressed }) => ({ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, minHeight: 46, borderRadius: 999, borderWidth: 1.5, borderColor: c.brand, opacity: disabled ? 0.5 : pressed ? 0.8 : 1 })}
    >
      <ZIcon name={icon} size={17} color={c.brand} stroke={2} />
      <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.brand }}>{label}</Text>
    </Pressable>
  );

  return (
    <>
      <Screen>
        <Header title="Upgrade to Tier 2" sub="Your NIN and a quick face check" onBack={() => router.back()} />

        <Card>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <Text style={{ fontSize: 14, fontFamily: font.extrabold, color: c.ink1 }}>Tier 2 limits</Text>
            <View style={{ paddingVertical: 6, paddingHorizontal: 10, borderRadius: 999, backgroundColor: 'rgba(6,110,102,0.10)' }}>
              <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.brand }}>{`Tier ${status.tier} now`}</Text>
            </View>
          </View>
          <View style={{ flexDirection: 'row', paddingVertical: 12, paddingHorizontal: 14, borderRadius: 16, backgroundColor: tint }}>
            {[['Per transfer', tier2?.per_transfer], ['Daily', tier2?.daily_transfer]].map(([label, value]) => (
              <View key={label} style={{ flex: 1 }}>
                <Text style={{ fontSize: 11.5, fontFamily: font.semibold, color: c.ink3, letterSpacing: 0.2 }}>{label}</Text>
                <NText style={{ fontSize: 15, marginTop: 2, fontFamily: font.extrabold, color: c.ink1, fontVariant: ['tabular-nums'] }}>{amountLabel(value)}</NText>
              </View>
            ))}
          </View>
        </Card>

        <Card style={{ marginTop: 14, gap: 14 }}>
          <StepTitle n={1} title="Your NIN" done={ninDone} />
          {ninDone ? <Done label="Verified" /> : !ninSent ? (
            <>
              <Field tint editable={!busy} label="National Identification Number (NIN)" placeholder="11 digits" keyboardType="number-pad"
                value={groupNin(nin)} onChangeText={(v) => setNin(v.replace(/\D/g, '').slice(0, 11))}
                prefix={<ZIcon name="card" size={19} color={c.ink3} stroke={1.9} />} />
              <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, marginTop: -6, lineHeight: 17 }}>We check your NIN to confirm it’s yours.</Text>
              {nin.length === 11 && <OutlineBtn label={busy ? 'Checking…' : 'Verify NIN'} icon="shield" disabled={busy} onPress={() => void verifyNin()} />}
            </>
          ) : (
            <>
              <Text style={{ fontSize: 13, fontFamily: font.regular, color: c.ink2, lineHeight: 19 }}>
                {`Enter the 6-digit code${ninDestination ? ` sent to ${ninDestination}` : ' sent for your NIN'}.`}
              </Text>
              <Field tint editable={!busy} label="Verification code" autoComplete="sms-otp" textContentType="oneTimeCode" maxLength={6} placeholder="6-digit code" keyboardType="number-pad"
                value={ninOtp} onChangeText={(v) => setNinOtp(v.replace(/\D/g, '').slice(0, 6))} prefix={<ZIcon name="lock" size={18} color={c.ink3} />} />
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <Pressable accessibilityRole="button" accessibilityLabel="Change NIN" disabled={busy} onPress={() => { setNinSent(false); setNinOtp(''); setNinTrackingId(''); setNinDestination(''); }} style={{ minHeight: 44, justifyContent: 'center' }}>
                  <Text style={{ fontSize: 12.5, color: c.brand, fontFamily: font.semibold }}>Change NIN</Text>
                </Pressable>
                <Pressable accessibilityRole="button" accessibilityLabel="Resend NIN code" disabled={busy} onPress={() => void resendNin()} style={{ minHeight: 44, justifyContent: 'center' }}>
                  <Text style={{ fontSize: 12.5, color: busy ? c.ink3 : c.brand, fontFamily: font.semibold }}>Resend code</Text>
                </Pressable>
              </View>
              <OutlineBtn label={busy ? 'Confirming…' : 'Confirm NIN'} icon="check" disabled={busy || ninOtp.length !== 6} onPress={() => void confirmNin()} />
            </>
          )}
        </Card>

        <Card style={{ marginTop: 14, gap: 12 }}>
          <StepTitle n={2} title="Face check" done={faceDone} />
          {faceDone ? <Done label="Done" /> : (
            <>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                <View style={{ width: 52, height: 52, borderRadius: 26, backgroundColor: 'rgba(6,110,102,0.08)', alignItems: 'center', justifyContent: 'center' }}>
                  <ZIcon name="faceid" size={26} color={c.brand} stroke={1.7} />
                </View>
                <Text style={{ flex: 1, fontSize: 13, fontFamily: font.regular, color: c.ink2, lineHeight: 19 }}>
                  A short selfie video. We match it to your NIN photo. It takes about a minute.
                </Text>
              </View>
              {faceUnavailable ? (
                <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: c.ink3, lineHeight: 18 }}>
                  {status.tier2_unavailable_reason || 'Face checks aren’t available right now. Please try again later.'}
                </Text>
              ) : null}
              <OutlineBtn label="Start face check" icon="scan" disabled={busy || faceUnavailable} onPress={() => void startFace()} />
            </>
          )}
        </Card>

        <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginTop: 14, paddingVertical: 12, paddingHorizontal: 14, borderRadius: 16, backgroundColor: tint }}>
          <View style={{ marginTop: 1 }}><ZIcon name="home" size={17} color={c.brand} stroke={2} /></View>
          <Text style={{ flex: 1, fontSize: 12.5, fontFamily: font.medium, color: c.ink2, lineHeight: 18 }}>
            Your sign-up address is sent with this, so the Tier 3 check starts straight away.
          </Text>
        </View>

        <View style={{ marginTop: 20 }}>
          <Btn label={busy && ninDone && faceDone ? 'Submitting…' : 'Submit for Tier 2'} disabled={!canSubmit} onPress={() => void submit()} />
        </View>
        {!!hint && <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, marginTop: 10, textAlign: 'center' }}>{hint}</Text>}
      </Screen>
      {!!faceUrl && (
        <FaceVerifyModal
          url={faceUrl}
          visible={faceOpen}
          onClose={closeFace}
          title="Face check"
          subtitle="Secure verification page"
          footer="Your video is used only for this check and is never stored by Zitch."
        />
      )}
    </>
  );
};

const Tier2Screen = () => <AuthGuard fresh><Tier2 /></AuthGuard>;

export default Tier2Screen;
