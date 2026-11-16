import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { Loading } from '@/components/design/Loading';
import FaceLivenessModal, { type FaceCapture } from '@/components/design/FaceLivenessModal';
import AuthGuard from '@/components/AuthGuard';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Card } from '@/components/design/ui';
import { FaceCheckRow, StepTitle } from '@/components/kyc/Onboarding';
import { TierBadge, TierDetails, TierPill } from '@/components/design/TierRow';
import { getSessionGeneration } from '@/lib/secureStore';
import { kycService, type KycStatus } from '@/lib/services/kyc';
import { tierLadder, type LadderTier } from '@/lib/tierLadder';
import { useTheme, font } from '@/lib/theme';

type Status = KycStatus & { tier_ladder?: LadderTier[]; has_wema_account?: boolean };


/** 3456 7890 123 — grouped for reading; the digits alone are sent. */
const group = (digits: string) => [digits.slice(0, 4), digits.slice(4, 8), digits.slice(8)].filter(Boolean).join(' ');
const digits = (value: string) => value.replace(/\D/g, '').slice(0, 11);

/** A Tier 2 refusal that a new live face check fixes (not a BVN/NIN problem). */
const faceRefused = (res: { success?: boolean; pending?: boolean; code?: string }) =>
  !res.success && !res.pending && (/^(selfie|liveness)_/.test(res.code || '')
    || (res as { _httpStatus?: number })._httpStatus === 502);

/**
 * Upgrade to Tier 2 (design 72), on the bank's contract: the customer types
 * their BVN and NIN, takes the live face check (FaceLivenessModal: turn left,
 * turn right, smile, checked by our server), and one request sends the
 * account, both numbers and the checked photo to the bank, as soon as the
 * check passes with both numbers typed. Only the bank's read-back raises the
 * tier.
 */
const Tier2 = () => {
  const { c } = useTheme();
  const params = useLocalSearchParams<{ from?: string; recheck?: string }>();
  const [status, setStatus] = useState<Status | null>(null);
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [bvn, setBvn] = useState('');
  const [nin, setNin] = useState('');
  const [face, setFace] = useState<FaceCapture | null>(null);
  const [cameraOpen, setCameraOpen] = useState(false);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const screenGeneration = useRef(getSessionGeneration()).current;
  const loadGeneration = useRef(0);

  const live = useCallback(() => mounted.current && screenGeneration === getSessionGeneration(), [screenGeneration]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
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
    return () => { inFlight.current = false; setBusy(false); setCameraOpen(false); };
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

  const finished = () => {
    // Tier 3 is its own request (the address); nothing is started from here.
    notify('You’re now Tier 2', 'Your new limits apply right away.', 'success');
    if (params.from === 'limits' && router.canGoBack()) router.back();
    else router.replace('/accountlimits');
  };

  const idsDone = bvn.length === 11 && nin.length === 11;
  // From Verify Tier 2 when the bank did not confirm what sign-up sent (or never answered).
  const notConfirmed = params.recheck === '1' && !!status && status.tier2_state === 'not_confirmed'
    && (status.tier2_review === 'rejected' || status.tier2_review === 'pending');

  // The bank already confirmed the details sent at sign-up: moving up needs no new check.
  const claim = async () => {
    if (!begin()) return;
    try {
      const res = await kycService.claimTier(2);
      if (!live()) return;
      if (res.success && res.state === 'upgraded') { await load(); finished(); }
      else { await load(); if (res.message) notify('Not upgraded yet', res.message, 'info'); }
    } catch { if (live()) notify('Error', 'Check your connection and try again.'); }
    finally { end(); }
  };

  const submit = async (capture: FaceCapture | null = face) => {
    if (!idsDone || !capture || !begin()) return;
    try {
      const res = await kycService.upgradeTier2(bvn, nin, capture.image, capture.token);
      if (!live()) return;
      if (res.success) {
        const fresh = await load();
        if (!live()) return;
        if (fresh?.face_verified && Number(fresh.tier) >= 2) { setBvn(''); setNin(''); setFace(null); finished(); }
        else notify('Almost there', fresh?.message || 'Your bank is finishing your upgrade. Check again shortly.', 'info');
      } else if (res.pending) {
        setBvn(''); setNin(''); setFace(null);
        notify('Your bank is checking your face', res.message || 'We’ll update your tier shortly.', 'info');
        await load();
      } else {
        if (faceRefused(res)) setFace(null);
        notify('Not upgraded yet', res.message || 'Please try again.');
      }
    } catch { if (live()) notify('Error', 'Check your connection and try again.'); }
    finally { end(); }
  };

  const checkAgain = async () => {
    if (!begin()) return;
    try {
      const fresh = await load();
      if (live() && fresh?.face_verified && Number(fresh.tier) >= 2) finished();
    } finally { end(); }
  };

  if (!status) {
    return (
      <Screen>
        <Header title="Upgrade to Tier 2" sub={notConfirmed ? 'Enter your NIN and retake the face check' : 'Your BVN, NIN and a live face check'} onBack={() => router.back()} />
        {loadError ? (
          <Card>
            <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginBottom: 16 }}>{loadError}</Text>
            <Btn label="Try again" onPress={() => void load()} />
          </Card>
        ) : <Loading label="Checking your verification…" />}
      </Screen>
    );
  }

  const tier2 = tierLadder(status.tier_ladder).find((t) => t.tier === 2);
  const complete = !!status.face_verified && Number(status.tier) >= 2;
  const unavailable = status.tier2_face_available === false;
  const pending = !complete && status.tier2_face_pending === true;
  const ready = !complete && status.tier2_state === 'ready';
  const noAccount = status.has_wema_account === false;
  const canSubmit = idsDone && !!face && !busy;
  const hint = !idsDone ? 'Enter your BVN and NIN to submit.' : !face ? 'Take the live face check to submit.' : '';

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

  const Notice = ({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) => (
    <Card style={{ marginTop: 14, gap: 10 }}>
      <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>{title}</Text>
      <Text style={{ fontSize: 13, fontFamily: font.regular, color: c.ink2, lineHeight: 19 }}>{body}</Text>
      {action}
    </Card>
  );

  return (
    <>
      <Screen>
        <Header title="Upgrade to Tier 2" sub={notConfirmed ? 'Enter your NIN and retake the face check' : 'Your BVN, NIN and a live face check'} onBack={() => router.back()} />

        {/* The same Tier 2 row as Account limits, so the two screens read alike. */}
        {tier2 ? (
          <Card>
            <View style={{ flexDirection: 'row', gap: 14 }}>
              <TierBadge tier={2} done={Number(status.tier) >= 2} current={Number(status.tier) === 2} />
              <View style={{ flex: 1 }}>
                <TierDetails t={tier2} done={Number(status.tier) >= 2}
                  pill={<TierPill label={Number(status.tier) >= 2 ? 'Your tier' : `Tier ${status.tier} now`} tone={Number(status.tier) >= 2 ? 'current' : 'neutral'} />} />
              </View>
            </View>
          </Card>
        ) : null}

        {complete ? (
          <Notice title="You’re on Tier 2" body="Your face check passed and your bank confirmed the upgrade." />
        ) : ready ? (
          <Notice title="Your bank confirmed your details"
            body="The NIN and face check from your sign-up are confirmed. Move up now and your new limits apply right away."
            action={<Btn label={busy ? 'Moving up…' : 'Move to Tier 2'} disabled={busy} onPress={() => void claim()} />} />
        ) : pending ? (
          <Notice title="Your bank is checking your face"
            body="You don’t need to do anything. We’ll update your tier as soon as your bank confirms."
            action={<OutlineBtn label={busy ? 'Checking…' : 'Check again'} icon="scan" disabled={busy} onPress={() => void checkAgain()} />} />
        ) : unavailable ? (
          <Notice title="Tier 2 is temporarily unavailable"
            body={status.tier2_unavailable_reason || 'Your verified identity is saved. Please try again later.'}
            action={<OutlineBtn label="Contact support" icon="help" onPress={() => router.push('/support')} />} />
        ) : noAccount ? (
          <Notice title="Set up your account first"
            body="Tier 2 upgrades your Zitch account at the bank. Finish setting up your account number, then come back here." />
        ) : (
          <>
            {notConfirmed ? (
              <View accessibilityRole="alert" style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginTop: 14, paddingVertical: 12, paddingHorizontal: 14,
                borderRadius: 16, backgroundColor: 'rgba(220,38,38,0.07)', borderWidth: 1, borderColor: 'rgba(220,38,38,0.25)' }}>
                <View style={{ marginTop: 1 }}><ZIcon name="help" size={17} color={c.red} stroke={2.2} /></View>
                <Text style={{ flex: 1, fontSize: 13, fontFamily: font.semibold, color: c.red, lineHeight: 19 }}>
                  We couldn’t confirm the NIN or face check from your sign-up. Check your NIN and take the face check again.
                </Text>
              </View>
            ) : null}
            <Card style={{ marginTop: 14, gap: 14 }}>
              <StepTitle n={1} title="Your BVN and NIN" done={idsDone} />
              <Field tint editable={!busy} label="Bank Verification Number (BVN)" placeholder="11 digits" keyboardType="number-pad"
                value={group(bvn)} onChangeText={(v) => setBvn(digits(v))}
                prefix={<ZIcon name="insurance" size={19} color={c.ink3} stroke={1.9} />} />
              <Field tint editable={!busy} label="National Identification Number (NIN)" placeholder="11 digits" keyboardType="number-pad"
                value={group(nin)} onChangeText={(v) => setNin(digits(v))}
                prefix={<ZIcon name="card" size={19} color={c.ink3} stroke={1.9} />} />
              <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, marginTop: -6, lineHeight: 17 }}>
                Your bank needs both with your face check. We match them to your verified details and don’t keep them.
              </Text>
            </Card>

            <Card style={{ marginTop: 14, gap: 12 }}>
              <StepTitle n={2} title="Live face check" done={!!face} />
              {/* One short row (review, Nov 2026): no long instructions; the camera explains each move. */}
              <FaceCheckRow done={!!face} busy={busy} onStart={() => setCameraOpen(true)} />
            </Card>

            <View style={{ marginTop: 20 }}>
              <Btn label={busy ? 'Submitting…' : 'Submit for Tier 2'} disabled={!canSubmit} onPress={() => void submit()} />
            </View>
            {!!hint && <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, marginTop: 10, textAlign: 'center' }}>{hint}</Text>}
          </>
        )}
      </Screen>
      <FaceLivenessModal
        visible={cameraOpen}
        onClose={() => setCameraOpen(false)}
        onCapture={(capture) => {
          if (!live()) return;
          setCameraOpen(false);
          setFace(capture);
          // Both numbers typed: send it now, as the WhatsApp page does.
          if (idsDone && !busy) void submit(capture);
          else notify('Face check passed', 'Enter your BVN and NIN, then submit for Tier 2.', 'success');
        }}
      />
    </>
  );
};

const Tier2Screen = () => <AuthGuard fresh><Tier2 /></AuthGuard>;

export default Tier2Screen;
