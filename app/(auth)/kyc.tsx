import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { getSessionGeneration, getToken } from '@/lib/secureStore';
import { classifyKycResponse, isAccountOtpPending, kycService, resolveIdentityOtpRoute, resolveOwnershipOtpRoute, type KycStatus, type KycVerificationFlag } from '@/lib/services/kyc';
import type { FundingAccount } from '@/lib/services/wallet';
import FaceLivenessModal, { type FaceCapture } from '@/components/design/FaceLivenessModal';
import FaceVerifyModal from '@/components/design/FaceVerifyModal';
import { CodeBoxes, resendLabel } from '@/components/design/CodeBoxes';
import { PasteCode } from '@/components/design/PasteCode';
import { extractCode } from '@/lib/code';
import AuthGuard from '@/components/AuthGuard';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Header, Field, Btn, Tap } from '@/components/design/ui';
import { EMPTY_ADDRESS as EMPTY_SIGNUP_ADDRESS, IdentityWaits, OnboardingHeading, OnboardingIdentity, StepTitle, ViaOption, type AddressValue, type SignupFace } from '@/components/kyc/Onboarding';
import { useTheme, font } from '@/lib/theme';
import { tierLadder, type LadderTier } from '@/lib/tierLadder';
import { useTierClaim } from '@/components/kyc/TierClaim';

type Status = {
  tier: number; transaction_limit: string;
  bvn_verified: boolean; nin_verified: boolean; face_verified: boolean;
  message?: string;
  address_verified?: boolean;
  address_verification_pending?: boolean;
  address_verification_state?: 'pending' | 'verified' | 'not_verified';
  address_verification_required?: boolean;
  email?: string;
  email_verified?: boolean;
  phone_verified?: boolean;
  pending?: boolean;
  identity_review_required?: boolean;
  id_document_verified?: boolean;
  identity_face_available?: boolean;
  identity_verification_methods?: string[];
  identity_verification_provider?: 'prembly' | 'wema';
  account_provider?: string;
  legacy_funding_review?: boolean;
  account_setup_state?: string;
  migration_message?: string;
  face_rail?: 'document' | 'wema';
  tier2_face_rail?: 'prembly' | 'wema';
  tier2_face_flow?: 'prembly_sdk' | 'wema_selfie';
  // Bank-rail Tier 2: one selfie, no BVN/NIN re-entry once Tier 1 is verified.
  tier2_face_pending?: boolean;
  tier2_face_available?: boolean;
  tier2_unavailable_reason?: string;
  tier3_address_available?: boolean;
  tier3_address_unavailable_reason?: string;
  // Where Verify Tier 2 / Tier 3 stand (same as Account limits).
  tier2_state?: 'done' | 'ready' | 'processing' | 'not_confirmed';
  tier3_state?: 'done' | 'ready' | 'processing' | 'not_confirmed' | 'not_sent';
  address_rail?: 'document' | 'wema' | 'none';
  // Set once the bank has opened the account number: from then on it will not
  // accept a lone BVN or NIN, only all of it at once. Read here so the menu can
  // send the customer straight to the step that works.
  identity_upgrade_required?: boolean;
  bank_upgrade_required?: boolean;
  // The customer's address as they gave it (sign-up saves it; nothing verified).
  address?: string;
  /** The bank has opened the account number (sign-up sends Tier 2 and Tier 3 only then). */
  has_wema_account?: boolean;
  tier_ladder?: LadderTier[];
};

type Method = 'menu' | 'email' | 'bvn' | 'nin' | 'upgrade' | 'address';

// Method accent colours — EXACT per the design handoff.
const C_BVN = '#0FA295';
const C_NIN = '#2D7FF9';
const C_SELFIE = '#7A5CFF';

/** How often an open face page asks our server for the result. */
const FACE_POLL_MS = 3000;
/** Seconds before the BVN code can be sent again (design 09). */
const BVN_RESEND_SECONDS = 60;

/**
 * The bank's identity face page held in the app (FaceVerifyModal). It reports
 * nothing itself: the result reaches our server from the bank, so while it is
 * up the screen polls, and it checks once more when the customer closes it.
 * `done` is set by whichever check handled the outcome first, so a poll and
 * the closing check that land together cannot both act on it.
 */
type FaceSheet = {
  kind: 'identity';
  url: string;
  session: string;
  isCurrent: () => boolean;
  done: boolean;
};

/** A Tier 2 refusal that a new live face check fixes (not a BVN/NIN problem). */
const faceCheckRefused = (res: { success?: boolean; pending?: boolean; code?: string }) =>
  !res.success && !res.pending && (/^(selfie|liveness)_/.test(res.code || '')
    || (res as { _httpStatus?: number })._httpStatus === 502);

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
    verify_identity?: string;
    onboarding?: string;
  }>();
  // Sign-up's "BVN and address" page: the same BVN flow, then the address, then
  // the PIN set-up. Reached from the phone-and-password step.
  const onboarding = params.onboarding === '1';
  const title = onboarding ? undefined : 'Identity verification';
  const toSetup = () => router.replace('/setup');
  const [status, setStatus] = useState<Status | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [statusError, setStatusError] = useState('');
  const [method, setMethod] = useState<Method>('menu');
  useEffect(() => {
    if (params.verify_identity === 'bvn' || params.verify_identity === 'nin') {
      setMethod(params.verify_identity);
    }
  }, [params.verify_identity]);
  const [bvn, setBvn] = useState('');
  const [email, setEmail] = useState('');
  const [emailOtp, setEmailOtp] = useState('');
  const [emailSent, setEmailSent] = useState(false);
  const [emailProof, setEmailProof] = useState('');
  const [emailProofIsPin, setEmailProofIsPin] = useState(false);
  const [emailProofRequired, setEmailProofRequired] = useState(false);
  const [bvnOtp, setBvnOtp] = useState('');
  const [bvnSent, setBvnSent] = useState(false);
  const [bvnTrackingId, setBvnTrackingId] = useState('');
  const [bvnOtpDestination, setBvnOtpDestination] = useState('');
  const [bvnDeliveryNotice, setBvnDeliveryNotice] = useState('');
  const [nin, setNin] = useState('');
  const [ninOtp, setNinOtp] = useState('');
  const [ninTrackingId, setNinTrackingId] = useState('');
  const [ninOtpDestination, setNinOtpDestination] = useState('');
  const [ninDeliveryNotice, setNinDeliveryNotice] = useState('');
  const [ninSent, setNinSent] = useState(false);
  // Combined existing-account upgrade. The bank scores all three together, so
  // they are collected before anything is sent — a partial submission is just a
  // refusal with the customer's identity already handed over.
  const [upBvn, setUpBvn] = useState('');
  const [upNin, setUpNin] = useState('');
  const [upFace, setUpFace] = useState<FaceCapture | null>(null);
  // Sign-up's NIN and face check: sent to the bank (as the Tier 2 request) when
  // the customer taps Continue, alongside the address (the Tier 3 request).
  const [signupNin, setSignupNin] = useState('');
  const [signupFace, setSignupFace] = useState<SignupFace | null>(null);
  const [signupAddress, setSignupAddress] = useState<AddressValue>(EMPTY_SIGNUP_ADDRESS);
  // The BVN form is cleared once the BVN is verified, but the bank needs the
  // BVN again with the NIN (Zitch keeps only its hash). Sign-up holds the one
  // just typed in memory for that request, so it is not asked for twice.
  const [signupBvn, setSignupBvn] = useState('');
  useEffect(() => { if (onboarding && bvn.length === 11) setSignupBvn(bvn); }, [onboarding, bvn]);
  const faceFor = useRef<'upgrade' | 'signup'>('upgrade');
  const [busy, setBusy] = useState(false);
  const [bvnVia, setBvnVia] = useState<'sms' | 'face'>('sms');
  const [faceSheet, setFaceSheet] = useState<FaceSheet | null>(null);
  const faceChecking = useRef(false);
  const [bvnResendIn, setBvnResendIn] = useState(0);
  const [upgradeCameraOpen, setUpgradeCameraOpen] = useState(false);
  const actionInFlight = useRef(false);
  const facePollGeneration = useRef(0);
  const mounted = useRef(true);
  const screenGeneration = useRef(getSessionGeneration()).current;
  const upgradeCaptureOwner = useRef<(() => boolean) | null>(null);
  const loadGeneration = useRef(0);

  const bindFlow = () => {
    const flow = facePollGeneration.current;
    return () => mounted.current && screenGeneration === getSessionGeneration() && flow === facePollGeneration.current;
  };

  const beginAction = () => {
    if (actionInFlight.current || !mounted.current || screenGeneration !== getSessionGeneration()) return false;
    actionInFlight.current = true;
    setBusy(true);
    return true;
  };
  const endAction = (isCurrent: () => boolean) => {
    if (!isCurrent()) return;
    actionInFlight.current = false;
    if (mounted.current) setBusy(false);
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      facePollGeneration.current += 1;
    };
  }, []);

  // Add-money can discover that the bank's pending attempt belongs to NIN
  // rather than the BVN form that started face verification. Resume that exact
  // server-owned attempt here so the confirmation and resend actions use the
  // matching identity route.
  useEffect(() => {
    const identity = params.pending_identity === 'nin' || params.pending_identity === 'bvn'
      ? params.pending_identity
      : '';
    const trackingId = typeof params.pending_tracking_id === 'string' ? params.pending_tracking_id : '';
    if (!identity || !trackingId) return;
    const destination = typeof params.pending_otp_destination === 'string' ? params.pending_otp_destination : '';
    facePollGeneration.current += 1;
    actionInFlight.current = false;
    setBusy(false);
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
    const request = ++loadGeneration.current;
    const current = () => mounted.current && screenGeneration === getSessionGeneration() && request === loadGeneration.current;
    try {
      const t = await getToken();
      if (!current()) return null;
      if (!t) throw new Error('Session unavailable');
      const res = await kycService.getStatus();
      if (!current()) return null;
      if (res.success) {
        setStatus(res);
        setStatusError('');
        return res;
      }
      setStatusError(res.message || 'We could not load your verification status. Please try again.');
    } catch { if (current()) setStatusError('We could not load your verification status. Please try again.'); }
    finally { if (current()) setLoadingStatus(false); }
    return null;
  }, [screenGeneration]);
  useFocusEffect(useCallback(() => {
    void load();
    return () => {
      facePollGeneration.current += 1;
      actionInFlight.current = false;
      upgradeCaptureOwner.current = null;
      setBusy(false);
      setUpgradeCameraOpen(false);
      setFaceSheet(null);
    };
  }, [load]));

  // The BVN code's resend countdown.
  useEffect(() => {
    if (bvnResendIn <= 0) return;
    const timer = setTimeout(() => setBvnResendIn((value) => value - 1), 1000);
    return () => clearTimeout(timer);
  }, [bvnResendIn]);

  // Return to the method menu and clear any in-flight sub-flow state.
  const goMenu = () => {
    facePollGeneration.current += 1;
    actionInFlight.current = false;
    upgradeCaptureOwner.current = null;
    setMethod('menu');
    setBvnSent(false);
    setEmail(''); setEmailOtp(''); setEmailSent(false);
    setEmailProof(''); setEmailProofRequired(false); setEmailProofIsPin(false);
    setBvnTrackingId(''); setBvnOtpDestination('');
    setBvnDeliveryNotice('');
    setNinSent(false);
    setNinTrackingId(''); setNinOtpDestination('');
    setNinDeliveryNotice('');
    setUpBvn(''); setUpNin(''); setUpFace(null);
    setUpgradeCameraOpen(false);
    setFaceSheet(null);
    setBusy(false);
  };
  const onBack = onboarding
    ? (method === 'bvn' && bvnSent ? () => { setBvnSent(false); setBvnOtp(''); setBvnTrackingId(''); setBvnOtpDestination(''); } : undefined)
    : method === 'menu' ? () => router.back() : goMenu;
  // In sign-up the BVN form is the starting point, and every route back to the
  // menu lands on it again until the BVN is verified.
  useEffect(() => {
    if (onboarding && status && !status.bvn_verified && method === 'menu') setMethod('bvn');
  }, [onboarding, status, method]);
  // Both identities are still outstanding to the BANK in this state even when
  // one is verified with us, because the upgrade request carries both.
  const needsUpgrade = !!status?.identity_upgrade_required
    && !(status?.bvn_verified && status?.nin_verified);
  const hasTier1Identity = !!(status?.bvn_verified || status?.nin_verified);
  // Wema's Tier 2 upgrade takes the account number, BVN, NIN and a live selfie
  // in one request. Zitch keeps only hashes, so the customer always types both.
  const askIdentity = true;
  const needsBankUpgrade = !!status?.bank_upgrade_required || needsUpgrade;
  const tier2Unavailable = status?.tier2_face_available === false;
  const addressVerificationAvailable = status?.account_provider === 'partnership'
    && status?.address_verification_required !== false
    && status?.address_rail !== 'none';
  // Tapping Tier 2 or Tier 3 here does what it does on Account limits (Zitch's
  // own limiter: nothing sign-up sent is shown or applied before this tap).
  const { claim, busy: claimBusy, sheets: claimSheets } = useTierClaim({
    ladder: tierLadder(status?.tier_ladder), address: status?.address, from: 'kyc',
    onClosed: () => { void load(); },
  });
  // The bank's face route can verify the BVN a moment before the account number
  // is in. Sign-up sends Tier 2 and Tier 3 only once it is, so check until then.
  const waitingForAccount = onboarding && !!status?.bvn_verified && status?.has_wema_account === false;
  useEffect(() => {
    if (!waitingForAccount) return;
    const timer = setInterval(() => { void load(); }, 3000);
    return () => clearInterval(timer);
  }, [waitingForAccount, load]);

  const startEmail = async () => {
    const target = (email || status?.email || '').trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(target) || !beginAction()) return;
    const isCurrent = bindFlow();
    try {
      const proof = emailProof
        ? (emailProofIsPin ? { transaction_pin: emailProof } : { current_password: emailProof })
        : undefined;
      const res = await kycService.startEmail(target, proof);
      if (!isCurrent()) return;
      if (res.success) {
        setEmail(target);
        setEmailOtp('');
        setEmailSent(true);
        setEmailProof('');
        setEmailProofRequired(false);
        notify('Email code sent', res.message || `Enter the code sent to ${target}.`, 'success');
      } else {
        if (res.code === 'reauth_required' || res.code === 'pin_locked') setEmailProofRequired(true);
        if (res.code === 'email_locked') { setEmail(''); setMethod('menu'); void load(); }
        notify('Could not send code', res.message || 'Check the email address and try again.');
      }
    } catch { if (isCurrent()) notify('Could not send code', 'Check your connection and try again.'); }
    finally { endAction(isCurrent); }
  };

  const confirmEmail = async () => {
    if (emailOtp.length !== 6 || !beginAction()) return;
    const isCurrent = bindFlow();
    try {
      const res = await kycService.confirmEmail(emailOtp);
      if (!isCurrent()) return;
      if (res.success && res.email_verified) {
        notify('Email verified', `${status?.email || 'Your email'} is verified. It can’t be changed now.`, 'success');
        setEmail(''); setEmailOtp(''); setEmailSent(false); setMethod('menu');
        await load();
      } else notify('Verification failed', res.message || 'Check the code and try again.');
    } catch { if (isCurrent()) notify('Verification failed', 'Check your connection and try again.'); }
    finally { endAction(isCurrent); }
  };

  // Shared submit: on success update tier status, toast the design copy, reset
  // the sub-flow fields and bounce back to the menu.
  const submit = async (call: () => Promise<KycStatus | FundingAccount>, successTitle: string, requiredFlags: KycVerificationFlag[] = []) => {
    if (!beginAction()) return;
    const isCurrent = bindFlow();
    try {
      const res = await call();
      if (!isCurrent()) return;
      const outcome = classifyKycResponse(res, requiredFlags);
      if (outcome === 'pending') {
        setStatus((current) => current ? { ...current, pending: true, message: res.message } : current);
        setBvn(''); setBvnOtp(''); setBvnSent(false);
        setNin(''); setNinOtp(''); setNinSent(false);
        notify('Verification processing', res.message || 'The verification service is still processing this submission. Check your status again shortly.', 'info');
      } else if (outcome === 'review') {
        notify('Verification needs review', res.message || 'Your account opened, but the verification service needs to review the identity details.', 'info');
        setBvn(''); setBvnOtp(''); setBvnSent(false); setBvnTrackingId(''); setBvnOtpDestination('');
        setNin(''); setNinOtp(''); setNinTrackingId(''); setNinOtpDestination(''); setNinSent(false);
        setUpBvn(''); setUpNin(''); setUpFace(null);
        setMethod('menu');
        await load();
      } else if (outcome === 'success' || outcome === 'unverified') {
        const authoritative = requiredFlags.length ? await load() : null;
        if (!isCurrent()) return;
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
        setNin(''); setNinOtp(''); setNinTrackingId(''); setNinOtpDestination(''); setNinSent(false);
        setUpBvn(''); setUpNin(''); setUpFace(null);
        setMethod('menu');
        await load();
      } else notify('Error', res.message || 'Verification failed');
    } catch { if (isCurrent()) notify('Error', 'Something went wrong.'); }
    finally { endAction(isCurrent); }
  };

  // --- BVN: enter number -> we send a one-time code -> confirm it ---
  const startBvn = async () => {
    if (!beginAction()) return;
    const isCurrent = bindFlow();
    try {
      const res = await kycService.startBvn(bvn);
      if (!isCurrent()) return;
      if (res.pending) {
        setStatus((current) => current ? { ...current, pending: true, message: res.message } : current);
        notify('Verification processing', res.message || 'Your BVN verification is still processing. Check your status again shortly.', 'info');
      } else if (res.upgrade_required) {
        setUpBvn(bvn);
        setMethod('upgrade');
      } else if (res.otp_required) {
        const challenge = resolveOwnershipOtpRoute(res);
        if (!challenge) {
          notify('Error', 'We could not start a secure verification challenge. Please try again.');
        } else {
          setBvnTrackingId(challenge.trackingId);
          setBvnOtp('');
          setBvnOtpDestination(res.delivery || res.otp_destination || '');
          setBvnDeliveryNotice(res.delivery_notice || '');
          setBvnSent(true);
          setBvnResendIn(BVN_RESEND_SECONDS);
          notify('BVN code requested', res.message || 'Enter the verification code to finish.', 'success');
        }
      } else if (res.identity_review_required) {
        notify('Verification needs review', res.message || 'Your account opened, but your BVN needs review.', 'info');
        await load();
        if (!isCurrent()) return;
        setBvn(''); setBvnOtp(''); setBvnTrackingId(''); setBvnOtpDestination(''); setBvnSent(false);
        setMethod('menu');
      } else if (res.success) {
        const authoritative = await load();
        if (!isCurrent()) return;
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
    } catch { if (isCurrent()) notify('Error', 'Something went wrong.'); }
    finally { endAction(isCurrent); }
  };
  const resendBvn = async () => {
    if (!bvnSent || !beginAction()) return;
    const isCurrent = bindFlow();
    try {
      const res = await kycService.resendBvn(bvnTrackingId, bvn);
      if (!isCurrent()) return;
      if (res.success && (bvnTrackingId || resolveOwnershipOtpRoute(res))) {
        if (!bvnTrackingId) setBvnTrackingId(resolveOwnershipOtpRoute(res)!.trackingId);
        setBvnOtp('');
        setBvnOtpDestination(res.delivery || res.otp_destination || '');
        setBvnDeliveryNotice(res.delivery_notice || '');
        setBvnResendIn(BVN_RESEND_SECONDS);
        notify('BVN code resent', res.message || 'Enter the latest verification code.', 'success');
      } else if (res.pending) {
        notify('Verification processing', res.message || 'Your BVN verification is still processing. Check your status again shortly.', 'info');
      } else notify('Error', res.message || 'Could not resend the BVN code');
    } catch { if (isCurrent()) notify('Error', 'Something went wrong.'); }
    finally { endAction(isCurrent); }
  };
  const confirmBvn = () => submit(() => kycService.confirmBvn(bvnTrackingId, bvnOtp), 'BVN verified', ['bvn_verified']);

  const startIdentityFaceVerification = async (identity: { bvn?: string; nin?: string }) => {
    if (!beginAction()) return;
    let handedOff = false;
    const generation = ++facePollGeneration.current;
    const isCurrent = () => mounted.current && screenGeneration === getSessionGeneration() && facePollGeneration.current === generation;
    try {
      const started = await kycService.startIdentityFace(identity);
      if (!isCurrent()) return;
      const otpRoute = resolveIdentityOtpRoute(started, identity.bvn ? 'bvn' : 'nin');
      if (otpRoute) {
        // Face-start can return the existing bank OTP attempt instead of a URL.
        // Keep the server's KYC flags and tracking reference, then continue in
        // the matching confirmation screen so the bank owns the identity state.
        setStatus((current) => current ? { ...current, ...started } : started);
        if (otpRoute.kind === 'bvn') {
          setBvnTrackingId(otpRoute.trackingId);
          setBvnOtpDestination(started.delivery || started.otp_destination || '');
          setBvnDeliveryNotice(started.delivery_notice || '');
          setBvnOtp('');
          setBvnSent(true);
          setBvnResendIn(BVN_RESEND_SECONDS);
          setBvnVia('sms');
          setMethod('bvn');
        } else {
          setNinTrackingId(otpRoute.trackingId);
          setNinOtpDestination(started.delivery || started.otp_destination || '');
          setNinDeliveryNotice(started.delivery_notice || '');
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
        setStatus((current) => current ? { ...current, pending: true, message: started.message } : current);
        notify('Verification processing', started.message || 'The verification service is still processing this request. Check your status again shortly.', 'info');
        return;
      }
      if (!started.success || !started.url || !started.session) {
        notify('Face verification unavailable', started.message || 'Please use the SMS code for now.');
        return;
      }
      // The page opens inside the app; polling and the closing check finish it.
      // The action stays in flight until then, so nothing else starts under it.
      handedOff = true;
      setFaceSheet({ kind: 'identity', url: started.url, session: started.session, isCurrent, done: false });
    } catch { if (isCurrent()) notify('Error', 'Could not complete face verification. Please use the SMS code or try again.'); }
    finally { if (!handedOff) endAction(isCurrent); }
  };

  /**
   * One status check for the open face page. Returns once the outcome is known
   * or still pending; `final` is the check made as the page closes, and only it
   * tells the customer "still processing".
   */
  const checkFace = async (sheet: FaceSheet, final: boolean) => {
    if (sheet.done || (faceChecking.current && !final)) return;
    faceChecking.current = true;
    const finish = () => {
      sheet.done = true;
      setFaceSheet((open) => (open === sheet ? null : open));
      endAction(sheet.isCurrent);
    };
    try {
      const result = await kycService.getIdentityFaceStatus(sheet.session);
      if (sheet.done || !sheet.isCurrent()) return;
      if (result.status === 'verified') {
        finish();
        setStatus(result);
        setBvn(''); setBvnOtp(''); setBvnSent(false); setBvnTrackingId(''); setBvnOtpDestination('');
        setNin(''); setNinOtp(''); setNinTrackingId(''); setNinOtpDestination(''); setNinSent(false);
        setMethod('menu');
        notify('Identity verified', result.message || 'Your face check is confirmed.', 'success');
        return;
      }
      if (result.status === 'failed' || result.status === 'expired') {
        finish();
        notify('Face verification incomplete', 'The face check was not confirmed. You can retry or use SMS.');
        return;
      }
      if (final) {
        finish();
        notify('Still processing', 'Your face check is still being confirmed. This page will refresh when you return.');
        await load();
      }
    } catch {
      if (final && !sheet.done && sheet.isCurrent()) {
        finish();
        notify('Face verification', 'Could not check your verification. Please try again.');
      }
    } finally {
      faceChecking.current = false;
    }
  };

  // While a face page is up, ask every few seconds; it closes itself on a result.
  useEffect(() => {
    if (!faceSheet) return;
    const sheet = faceSheet;
    const timer = setInterval(() => { void checkFace(sheet, false); }, FACE_POLL_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [faceSheet]);

  const closeFaceSheet = () => {
    const sheet = faceSheet;
    setFaceSheet(null);
    if (sheet && !sheet.done) void checkFace(sheet, true);
  };

  // --- NIN: the server selects identity lookup and its ownership challenge ---
  const startNin = async () => {
    if (!beginAction()) return;
    const isCurrent = bindFlow();
    try {
      const res = await kycService.startNin(nin);
      if (!isCurrent()) return;
      if (res.pending) {
        setStatus((current) => current ? { ...current, pending: true, message: res.message } : current);
        notify('Verification processing', res.message || 'Your NIN verification is still processing. Check your status again shortly.', 'info');
      } else if (res.upgrade_required) {
        // Not an error the customer can retry out of: this account can only be
        // finished by the combined upgrade. Carry the NIN they already typed
        // across so they are not asked for it twice.
        setUpNin(nin);
        setMethod('upgrade');
      } else if (res.otp_required) {
        const challenge = resolveOwnershipOtpRoute(res);
        if (!challenge) {
          notify('Error', 'We could not start a secure verification challenge. Please try again.');
        } else {
          setNinTrackingId(challenge.trackingId);
          setNinOtp('');
          setNinOtpDestination(res.delivery || res.otp_destination || '');
          setNinDeliveryNotice(res.delivery_notice || '');
          setNinSent(true);
          notify('NIN code requested', res.message || 'Enter the verification code to finish.', 'success');
        }
      } else if (res.identity_review_required) {
        notify('Verification needs review', res.message || 'Your account opened, but your NIN needs review.', 'info');
        await load();
        if (!isCurrent()) return;
        setNin(''); setNinOtp(''); setNinTrackingId(''); setNinOtpDestination(''); setNinSent(false);
        setMethod('menu');
      } else if (res.success) {
        const authoritative = await load();
        if (!isCurrent()) return;
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
    } catch { if (isCurrent()) notify('Error', 'Something went wrong.'); }
    finally { endAction(isCurrent); }
  };
  const confirmNin = () => submit(
    () => kycService.confirmNin(ninTrackingId, ninOtp),
    'NIN verified',
    ['nin_verified'],
  );
  const resendNin = async () => {
    if (!ninSent || !beginAction()) return;
    const isCurrent = bindFlow();
    try {
      const res = await kycService.resendNin(ninTrackingId, nin);
      if (!isCurrent()) return;
      if (res.success && (ninTrackingId || resolveOwnershipOtpRoute(res))) {
        if (!ninTrackingId) setNinTrackingId(resolveOwnershipOtpRoute(res)!.trackingId);
        setNinOtp('');
        setNinOtpDestination(res.delivery || res.otp_destination || '');
        setNinDeliveryNotice(res.delivery_notice || '');
        notify('NIN code resent', res.message || 'Enter the latest verification code.', 'success');
      } else if (res.pending) {
        notify('Verification processing', res.message || 'Your NIN verification is still processing. Check your status again shortly.', 'info');
      } else notify('Error', res.message || 'Could not resend the NIN code');
    } catch { if (isCurrent()) notify('Error', 'Something went wrong.'); }
    finally { endAction(isCurrent); }
  };

  const verifyNin = startNin;

  // --- Combined upgrade: BVN + NIN + the live face check, submitted together ---
  // The checked photo and its proof are HELD rather than posted, because the
  // bank scores the photo against the two numbers in one request. Posting it
  // alone would spend the attempt on a call that cannot complete the upgrade.
  const captureUpgradeSelfie = () => {
    if (busy) return;
    faceFor.current = 'upgrade';
    upgradeCaptureOwner.current = bindFlow();
    setUpgradeCameraOpen(true);
  };
  const captureSignupFace = () => {
    if (busy) return;
    faceFor.current = 'signup';
    upgradeCaptureOwner.current = bindFlow();
    setUpgradeCameraOpen(true);
  };
  const submitUpgrade = (face: FaceCapture | null = upFace) => submit(async () => {
    const res = await kycService.upgradeTier2(askIdentity ? upBvn : '', askIdentity ? upNin : '',
      face?.image || '', face?.token);
    // A refused, expired or unusable face check needs a new one; a BVN/NIN
    // problem keeps it.
    if (faceCheckRefused(res)) setUpFace(null);
    return res;
  },
    askIdentity ? 'Identity verified — tier upgraded' : 'Tier 2 verified',
    askIdentity ? ['bvn_verified', 'nin_verified', 'face_verified'] : ['face_verified'],
  );



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
      <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.regular }}>Your identity details are protected.</Text>
    </View>
  );

  const MethodCard = ({ id, icon, color, title, sub, badge, done, doneSub, onPress }: { id: Method; icon: string; color: string; title: string; sub: string; badge?: string; done?: boolean; doneSub?: string; onPress?: () => void }) => (
    <Tap accessibilityLabel={`${title}, ${done ? (doneSub || 'Verified') : sub}`} disabled={!!done || busy} onPress={() => { if (!done) { if (onPress) onPress(); else setMethod(id); } }} style={{ marginTop: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 13, padding: 15, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, ...cardShadow }}>
        <View style={{ width: 46, height: 46, borderRadius: 13, backgroundColor: color + '22', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name={icon} size={22} color={color} stroke={1.9} />
        </View>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>{title}</Text>
          <Text numberOfLines={1} style={{ fontSize: 12.5, color: done ? c.lime : c.ink3, marginTop: 1, fontFamily: font.regular }}>{done ? (doneSub || 'Verified') : sub}</Text>
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

  const Tier2Unavailable = () => <View style={{ marginTop: 16, padding: 16, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line }}>
    <Text style={{ color: c.ink1, fontFamily: font.bold, fontSize: 16 }}>Tier 2 verification is temporarily unavailable</Text>
    <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginTop: 8 }}>{status?.tier2_unavailable_reason || 'Your current verification is saved. Contact support for help with this upgrade.'}</Text>
    <View style={{ marginTop: 12 }}><Btn label="Contact support" variant="ghost" onPress={() => router.push('/support')} /></View>
  </View>;

  if (loadingStatus || statusError || !status) {
    return <Screen>
      <Header title={title} onBack={onboarding ? undefined : () => router.back()} />
      <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>{statusError || 'Checking your verification status…'}</Text>
      {!!statusError && <Btn label="Try again" onPress={() => { setLoadingStatus(true); void load(); }} />}
    </Screen>;
  }

  if (status?.legacy_funding_review === true || status?.account_setup_state === 'legacy_funding_review'
      || (status?.account_provider != null && status.account_provider !== 'partnership')) {
    return <Screen>
      <Header title={title} onBack={onBack} />
      <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>{status.migration_message || 'Your account needs review. Contact support before adding or spending money.'}</Text>
      <View style={{ marginTop: 20 }}><Btn label="Contact support" onPress={() => router.push('/support')} /></View>
    </Screen>;
  }

  if (status?.pending || status?.account_setup_state === 'processing') {
    return <Screen>
      <Header title={title} onBack={onboarding ? undefined : () => router.back()} />
      <Text style={{ color: c.ink1, fontFamily: font.bold, fontSize: 18, marginBottom: 12 }}>Verification processing</Text>
      <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>{status.message || 'Your verification is being confirmed. You do not need to start again.'}</Text>
      <View style={{ marginTop: 20 }}><Btn label="Check again" onPress={() => void load()} /></View>
    </Screen>;
  }

  return (
    <>
    <Screen>
      <Header title={title} onBack={onBack} />

      {method === 'menu' && onboarding && status.bvn_verified && (
        <OnboardingIdentity savedAddress={status.address || ''} onDone={toSetup} bvn={bvn.length === 11 ? bvn : signupBvn}
          accountReady={status.has_wema_account !== false}
          nin={signupNin} onNin={setSignupNin} face={signupFace} onStartFace={captureSignupFace}
          onFaceRefused={() => setSignupFace(null)} faceBusy={busy}
          address={signupAddress} onAddress={setSignupAddress} />
      )}

      {method === 'menu' && !onboarding && (
        <View>
          <Text style={{ fontSize: 13.5, color: c.ink3, lineHeight: 20, marginBottom: 6, fontFamily: font.regular }}>
            Verify your identity and check the services available to your account.
          </Text>

          {status && (
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: c.surface3, borderRadius: 16, padding: 16, marginTop: 12 }}>
              <View>
                <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>Current tier</Text>
                <Text style={{ fontSize: 20, fontFamily: font.extrabold, color: c.ink1 }}>Tier {status.tier}</Text>
              </View>
            </View>
          )}

          {/* The email from sign-up (17 Nov 2026): verify it here, and change it
              first if it is wrong. Once verified it is fixed, so the card is
              done and cannot be opened again. */}
          <MethodCard id="email" icon="remita" color={C_NIN} title="Email address"
            sub={status.email ? `${status.email} · Tap to verify` : 'Add your email and verify it'}
            done={!!status.email_verified} doneSub={status.email ? `${status.email} · Verified` : 'Verified'} />

          {needsUpgrade && !hasTier1Identity ? (
            <>
              <View style={{ flexDirection: 'row', gap: 10, padding: 14, borderRadius: 14, backgroundColor: 'rgba(45,127,249,.10)', marginTop: 14 }}>
                <ZIcon name="help" size={16} color={C_NIN} />
                <Text style={{ flex: 1, fontSize: 12.5, color: c.ink2, lineHeight: 19, fontFamily: font.regular }}>
                  Your bank has already opened an account number. Confirm your identity details together to finish verification on this account.
                </Text>
              </View>
              {tier2Unavailable ? <Tier2Unavailable /> : <MethodCard id="upgrade" icon="insurance" color={C_BVN} title="Finish account verification" sub="BVN, NIN and face verification" badge="Finish" />}
            </>
          ) : (
            <>
              {!hasTier1Identity && <Text style={{ color: c.ink2, fontFamily: font.regular, marginTop: 14 }}>Tier 1: choose BVN or NIN. Verify with an SMS code or face verification.</Text>}
              {(!hasTier1Identity || status.bvn_verified) && <MethodCard id="bvn" icon="insurance" color={C_BVN} title="BVN verification" sub="Tier 1 · SMS code or face verification" done={!!status.bvn_verified} />}
              {(!hasTier1Identity || status.nin_verified) && <MethodCard id="nin" icon="card" color={C_NIN} title="NIN verification" sub="Tier 1 · SMS code or face verification" done={!!status.nin_verified} />}
            </>
          )}
          {hasTier1Identity && (tier2Unavailable && (needsBankUpgrade || !status.face_verified)
            ? <Tier2Unavailable /> : needsBankUpgrade
            ? <MethodCard id="upgrade" icon="user" color={C_SELFIE} title="Tier 2 · Face verification"
                sub="Your BVN and NIN, then a live face check" />
            // Zitch's own limiter (17 Nov 2026): what sign-up sent the bank is not
            // shown here. The tap asks where it stands (useTierClaim): confirmed
            // applies now, refused opens "Details need review".
            : <MethodCard id="upgrade" icon="user" color={C_SELFIE} title="Face verification"
                sub={claimBusy === 2 ? 'Checking…' : 'Tier 2'}
                done={(status.tier ?? 0) >= 2} onPress={() => { if ((status.tier ?? 0) < 2) void claim(2); }} />)}
          {addressVerificationAvailable && status?.tier !== undefined && status.tier >= 2 && (
            <MethodCard id="address" icon="home" color={C_BVN} title="Home address" sub={claimBusy === 3 ? 'Checking…' : 'Tier 3'}
              done={status.tier >= 3} onPress={() => { if (status.tier < 3) void claim(3); }} />)}
          <Footer />
        </View>
      )}

      {method === 'email' && (
        <View>
          {/* Once the code is sent, say where it went and that it goes in the box
              below (17 Nov 2026: "where will the code be inputed"). */}
          <Hero icon="remita" color={C_NIN} title="Verify your email" sub={emailSent
            ? `Enter the 6-digit code we sent to ${(email || status?.email || 'your email').trim()}.`
            : 'This is the email you signed up with. Change it here if it’s wrong. Once it’s verified it can’t be changed. We’ll send a 6-digit code to your inbox.'} />
          <View style={{ marginTop: 22 }}>
            {!emailSent ? (
              <>
                <Field editable={!busy} label="Email address" autoCorrect={false} autoComplete="email" placeholder="you@example.com" keyboardType="email-address" autoCapitalize="none"
                  value={email || status?.email || ''} onChangeText={setEmail} prefix={<ZIcon name="remita" size={18} color={c.ink3} />} />
                {emailProofRequired && (
                  <>
                    <Field editable={!busy} label={emailProofIsPin ? 'Transaction PIN' : 'Current password'}
                      secureTextEntry autoCapitalize="none" autoCorrect={false}
                      keyboardType={emailProofIsPin ? 'number-pad' : 'default'}
                      value={emailProof} onChangeText={setEmailProof} />
                    <Tap disabled={busy} onPress={() => { setEmailProof(''); setEmailProofIsPin(!emailProofIsPin); }}>
                      <Text style={{ color: c.brand, fontFamily: font.semibold }}>
                        {emailProofIsPin ? 'Use current password' : 'Use transaction PIN'}
                      </Text>
                    </Tap>
                  </>
                )}
                <View style={{ height: 22 }} />
                <Btn label={busy ? 'Sending…' : 'Send email code'} disabled={busy || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test((email || status?.email || '').trim())} onPress={startEmail} />
              </>
            ) : (
              <>
                <Field editable={!busy} label="Email verification code" textContentType="oneTimeCode" placeholder="6-digit code" keyboardType="number-pad" value={emailOtp}
                  onChangeText={(value) => setEmailOtp(extractCode(value, 6))} prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
                  suffix={<PasteCode onCode={setEmailOtp} disabled={busy} />} />
                <Tap
                  disabled={busy}
                  onPress={() => { setEmailSent(false); setEmailOtp(''); }}
                  accessibilityLabel="Change email or resend code"
                  hitSlop={8}
                  style={{ alignSelf: 'flex-start', marginTop: 10, minHeight: 44, justifyContent: 'center' }}
                >
                  <Text style={{ fontSize: 12.5, color: c.brand, fontFamily: font.semibold }}>Change email or resend code</Text>
                </Tap>
                <View style={{ height: 22 }} />
                <Btn label={busy ? 'Confirming…' : 'Confirm email'} disabled={busy || emailOtp.length !== 6} onPress={confirmEmail} />
              </>
            )}
          </View>
        </View>
      )}

      {/* Sign-up keeps the whole BVN step on one page (design 09): the number,
          the SMS-or-face choice and, once a code is sent, the boxes for it. */}
      {method === 'bvn' && onboarding && (
        <View>
          <OnboardingHeading sub="Verify your BVN and NIN, take a quick face check, then add the address where you live." />
          <StepTitle n={1} title="Your BVN" />
          <View style={{ marginTop: 14 }}>
            <Field editable={!busy} label="Bank Verification Number (BVN)" placeholder="11 digits" keyboardType="number-pad" value={bvn}
              onChangeText={(v) => {
                const next = v.replace(/\D/g, '').slice(0, 11);
                // A different BVN needs its own code.
                if (bvnSent && next !== bvn) { setBvnSent(false); setBvnOtp(''); setBvnTrackingId(''); setBvnOtpDestination(''); setBvnDeliveryNotice(''); }
                setBvn(next);
              }} prefix={<ZIcon name="card" size={18} color={c.ink3} />} />
            {!bvnSent && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }}>
                <ZIcon name="help" size={14} color={c.amber} />
                <Text style={{ flex: 1, color: c.ink3, fontSize: 12.5, fontFamily: font.regular }}>Dial *565*0# on your registered line to get your BVN.</Text>
              </View>
            )}
            {status.identity_face_available && (
              <View style={{ marginTop: 16 }}>
                <Text style={{ fontSize: 13, fontFamily: font.semibold, color: c.ink2, marginBottom: 8 }}>Verify with</Text>
                <View accessibilityRole="radiogroup" style={{ flexDirection: 'row', gap: 10 }}>
                  <ViaOption on={bvnVia === 'sms'} icon="chat" label="SMS code" sub="Sent to the phone on your BVN" disabled={busy} onPress={() => setBvnVia('sms')} />
                  <ViaOption on={bvnVia === 'face'} icon="scan" label="Face check" sub="A quick selfie to match your BVN" disabled={busy} onPress={() => setBvnVia('face')} />
                </View>
              </View>
            )}
            {bvnSent && (!status.identity_face_available || bvnVia === 'sms') ? (
              <View style={{ marginTop: 18 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                  <Text style={{ flex: 1, fontSize: 13, fontFamily: font.semibold, color: c.ink2 }}>
                    Enter the code sent to {bvnOtpDestination || 'the phone on your BVN'}
                  </Text>
                  <PasteCode onCode={setBvnOtp} disabled={busy} />
                </View>
                {!!bvnDeliveryNotice && <Text accessibilityRole="alert" style={{ marginTop: 8, color: c.ink2, fontFamily: font.regular }}>{bvnDeliveryNotice}</Text>}
                <CodeBoxes
                  value={bvnOtp}
                  onChange={setBvnOtp}
                  editable={!busy}
                  autoFocus
                  accessibilityLabel="6-digit BVN verification code"
                  marginTop={10}
                  marginBottom={10}
                />
                <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                  <Text style={{ flex: 1, fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>It can take a minute to arrive.</Text>
                  <Tap accessibilityLabel="Resend BVN code" disabled={busy || bvnResendIn > 0} onPress={() => void resendBvn()} style={{ minHeight: 44, justifyContent: 'center' }}>
                    <Text style={{ fontSize: 12.5, color: busy || bvnResendIn > 0 ? c.ink3 : c.brand, fontFamily: font.semibold }}>
                      {bvnResendIn > 0 ? resendLabel(bvnResendIn) : 'Resend code'}
                    </Text>
                  </Tap>
                </View>
                <View style={{ height: 18 }} />
                <Btn label={busy ? 'Confirming…' : 'Continue'} disabled={busy || bvnOtp.length !== 6} onPress={confirmBvn} />
              </View>
            ) : (
              <>
                <View style={{ height: 22 }} />
                {status.identity_face_available && bvnVia === 'face'
                  ? <Btn label={busy ? 'Opening face check…' : 'Start face check'} icon="scan" disabled={busy || bvn.length !== 11} onPress={() => startIdentityFaceVerification({ bvn })} />
                  : <Btn label={busy ? 'Sending code…' : 'Send code'} disabled={busy || bvn.length !== 11} onPress={startBvn} />}
              </>
            )}
          </View>
          <IdentityWaits nin={signupNin} onNin={setSignupNin} editable={!busy}
            face={signupFace} onStartFace={captureSignupFace} faceBusy={busy}
            address={signupAddress} onAddress={setSignupAddress} />
        </View>
      )}

      {method === 'bvn' && !bvnSent && !onboarding && (
        <View>
          <Hero icon="insurance" color={C_BVN} title="BVN verification" sub="Enter your 11-digit BVN. We’ll send an SMS code to the phone number linked to it." />
          <View style={{ marginTop: 22 }}>
            <Field editable={!busy} label="Bank Verification Number (BVN)" placeholder="Enter your 11-digit BVN" keyboardType="number-pad" value={bvn} onChangeText={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))} prefix={<ZIcon name="insurance" size={18} color={c.ink3} />} />
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }}>
              <ZIcon name="help" size={14} color={c.amber} />
              <Text style={{ flex: 1, color: c.ink3, fontSize: 12.5, fontFamily: font.regular }}>Dial *565*0# on your registered line to get your BVN.</Text>
            </View>
            <View style={{ height: 22 }} />
            <Btn label={busy ? 'Sending code…' : 'Send verification code'} disabled={busy || bvn.length !== 11} onPress={startBvn} />
            {status.identity_face_available && <View style={{ marginTop: 12 }}><Btn label="Use face verification instead" variant="ghost" disabled={busy || bvn.length !== 11} onPress={() => startIdentityFaceVerification({ bvn })} /></View>}
          </View>
          <Footer />
        </View>
      )}

      {method === 'bvn' && bvnSent && !onboarding && (
        <View>
          <Hero icon="insurance" color={C_BVN} title="Confirm your BVN" sub={`Enter the 6-digit code${bvnOtpDestination ? ` sent to ${bvnOtpDestination}` : ' sent for your BVN'}.`} />
          {!!bvnDeliveryNotice && <Text accessibilityRole="alert" style={{ marginTop: 12, color: c.ink2, fontFamily: font.regular }}>{bvnDeliveryNotice}</Text>}
          <View style={{ marginTop: 22 }}>
            <Field editable={!busy} label="Verification code" autoComplete="sms-otp" textContentType="oneTimeCode" placeholder="6-digit code" keyboardType="number-pad" value={bvnOtp} onChangeText={(v) => setBvnOtp(extractCode(v, 6))} prefix={<ZIcon name="lock" size={18} color={c.ink3} />} suffix={<PasteCode onCode={setBvnOtp} disabled={busy} />} />
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 10 }}>
              <Tap accessibilityLabel="Change BVN" disabled={busy} onPress={() => { setBvnSent(false); setBvnOtp(''); setBvnTrackingId(''); setBvnOtpDestination(''); }} style={{ minHeight: 44, justifyContent: 'center' }}><Text style={{ fontSize: 12.5, color: c.brand, fontFamily: font.semibold }}>Change BVN</Text></Tap>
              <Tap accessibilityLabel="Resend BVN code" disabled={busy} onPress={() => void resendBvn()} style={{ minHeight: 44, justifyContent: 'center' }}><Text style={{ fontSize: 12.5, color: busy ? c.ink3 : c.brand, fontFamily: font.semibold }}>Resend code</Text></Tap>
            </View>
            <View style={{ height: 22 }} />
            <Btn label={busy ? 'Confirming…' : 'Confirm BVN'} disabled={busy || bvnOtp.length !== 6} onPress={confirmBvn} />
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
          <Hero icon="card" color={C_NIN} title="NIN verification" sub="Enter your 11-digit NIN. We’ll send an SMS code to the phone number registered on it." />
          <View style={{ marginTop: 22 }}>
            <Field editable={!busy} label="National Identification Number (NIN)" placeholder="Enter your 11-digit NIN" keyboardType="number-pad" value={nin} onChangeText={(v) => setNin(v.replace(/\D/g, '').slice(0, 11))} prefix={<ZIcon name="card" size={18} color={c.ink3} />} />
            <View style={{ height: 12 }} />
            <View style={{ height: 18 }} />
            <Btn label={busy ? 'Requesting code…' : 'Send NIN verification code'} disabled={busy || nin.length !== 11} onPress={verifyNin} />
            {status.identity_face_available && <View style={{ marginTop: 12 }}><Btn label="Use face verification instead" variant="ghost" disabled={busy || nin.length !== 11} onPress={() => startIdentityFaceVerification({ nin })} /></View>}
          </View>
          <Footer />
        </View>
      )}

      {method === 'nin' && ninSent && (
        <View>
          <Hero icon="card" color={C_NIN} title="Confirm your NIN" sub={`Enter the 6-digit code${ninOtpDestination ? ` sent to ${ninOtpDestination}` : ' sent for your NIN'}.`} />
          {!!ninDeliveryNotice && <Text accessibilityRole="alert" style={{ marginTop: 12, color: c.ink2, fontFamily: font.regular }}>{ninDeliveryNotice}</Text>}
          <View style={{ marginTop: 22 }}>
            <Field editable={!busy} label="Verification code" autoComplete="sms-otp" textContentType="oneTimeCode" placeholder="6-digit code" keyboardType="number-pad" value={ninOtp} onChangeText={(v) => setNinOtp(extractCode(v, 6))} prefix={<ZIcon name="lock" size={18} color={c.ink3} />} suffix={<PasteCode onCode={setNinOtp} disabled={busy} />} />
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 10 }}>
              <Tap accessibilityLabel="Change NIN" disabled={busy} onPress={() => { setNinSent(false); setNinOtp(''); setNinTrackingId(''); setNinOtpDestination(''); }} style={{ minHeight: 44, justifyContent: 'center' }}><Text style={{ fontSize: 12.5, color: c.brand, fontFamily: font.semibold }}>Change NIN</Text></Tap>
              <Tap accessibilityLabel="Resend NIN code" disabled={busy} onPress={() => void resendNin()} style={{ minHeight: 44, justifyContent: 'center' }}><Text style={{ fontSize: 12.5, color: busy ? c.ink3 : c.brand, fontFamily: font.semibold }}>Resend code</Text></Tap>
            </View>
            <View style={{ height: 22 }} />
            <Btn label={busy ? 'Confirming…' : 'Confirm NIN'} disabled={busy || ninOtp.length !== 6} onPress={confirmNin} />
            {status?.identity_face_available && (
              <View style={{ marginTop: 12 }}>
                <Btn label={busy ? 'Opening face verification…' : 'Use face verification instead'} variant="ghost" disabled={busy || nin.length !== 11} onPress={() => startIdentityFaceVerification({ nin })} />
              </View>
            )}
          </View>
          <Footer />
        </View>
      )}

      {method === 'upgrade' && tier2Unavailable && <Tier2Unavailable />}
      {method === 'upgrade' && !tier2Unavailable && (
        <View>
          <Hero icon="insurance" color={C_BVN} title="Tier 2 · Face verification"
                sub="Your bank needs your BVN, NIN and a live face check together for Tier 2. Your photo is matched to your BVN and NIN photo." />
          <View style={{ marginTop: 22 }}>
            {askIdentity && <>
            <Field editable={!busy} label="Bank Verification Number (BVN)" placeholder="Enter your 11-digit BVN" keyboardType="number-pad"
                   value={upBvn} onChangeText={(v) => setUpBvn(v.replace(/\D/g, '').slice(0, 11))}
                   prefix={<ZIcon name="insurance" size={18} color={c.ink3} />} />
            {status?.bvn_verified && (
              <Text style={{ fontSize: 12, color: c.ink3, marginTop: 6, lineHeight: 18, fontFamily: font.regular }}>
                Already verified with us — your bank still needs it inside this one
                request, so enter it once more. We don&apos;t re-store it.
              </Text>
            )}
            <View style={{ height: 14 }} />
            <Field editable={!busy} label="National Identification Number (NIN)" placeholder="Enter your 11-digit NIN" keyboardType="number-pad"
                   value={upNin} onChangeText={(v) => setUpNin(v.replace(/\D/g, '').slice(0, 11))}
                   prefix={<ZIcon name="card" size={18} color={c.ink3} />} />
            <View style={{ height: 14 }} />
            </>}
            <Tap disabled={busy} accessibilityLabel={upFace ? 'Retake the live face check' : 'Take the live face check'} onPress={captureUpgradeSelfie}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 14, borderWidth: 1.5, borderStyle: 'dashed', borderColor: upFace ? C_BVN : c.line, backgroundColor: upFace ? 'rgba(15,162,149,.08)' : c.surface2 }}>
                <View style={{ width: 40, height: 40, borderRadius: 11, backgroundColor: upFace ? 'rgba(15,162,149,.16)' : c.surface3, alignItems: 'center', justifyContent: 'center' }}>
                  <ZIcon name={upFace ? 'check' : 'user'} size={20} color={upFace ? C_BVN : c.ink3} stroke={2.4} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontSize: 14, fontFamily: font.semibold, color: c.ink1 }}>{upFace ? 'Face check passed' : 'Live face check'}</Text>
                  <Text style={{ fontSize: 12, color: c.ink3, marginTop: 1, fontFamily: font.regular }}>{upFace ? 'Tap to take it again' : 'Turn left, turn right, then smile'}</Text>
                </View>
              </View>
            </Tap>
            <View style={{ height: 22 }} />
            <Btn label={busy ? 'Verifying…' : 'Finish verification'}
                 disabled={busy || (askIdentity && (upBvn.length !== 11 || upNin.length !== 11)) || !upFace}
                 onPress={() => submitUpgrade()} />
          </View>
          <Footer />
        </View>
      )}


    </Screen>
    <FaceVerifyModal
      url={faceSheet?.url || ''}
      visible={!!faceSheet}
      onClose={closeFaceSheet}
      footer={onboarding
        ? 'Your photo is used only to match your BVN and is never stored by Zitch. When it’s done, you come straight back to finish sign-up.'
        : undefined}
    />
    <FaceLivenessModal
      visible={upgradeCameraOpen}
      onClose={() => { upgradeCaptureOwner.current = null; faceFor.current = 'upgrade'; setUpgradeCameraOpen(false); }}
      onCapture={(capture) => {
        if (!upgradeCaptureOwner.current?.()) return;
        upgradeCaptureOwner.current = null;
        if (faceFor.current === 'signup') {
          // Sign-up sends it with the BVN and NIN as the Tier 2 request, in the
          // background, as soon as both are there (OnboardingIdentity).
          faceFor.current = 'upgrade';
          setUpgradeCameraOpen(false); setSignupFace(capture);
          return;
        }
        setUpgradeCameraOpen(false); setUpFace(capture);
        // The face check passed: send it with the BVN and NIN already typed,
        // as the WhatsApp page does. Otherwise it is held for "Finish
        // verification".
        if (!busy && (!askIdentity || (upBvn.length === 11 && upNin.length === 11))) void submitUpgrade(capture);
      }}
    />
    {claimSheets}
    </>
  );
};

const KycScreen = () => <AuthGuard fresh><Kyc /></AuthGuard>;

export default KycScreen;
