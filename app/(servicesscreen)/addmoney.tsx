import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import * as WebBrowser from 'expo-web-browser';
import { router, useFocusEffect } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { vasAccountStatusTitle, walletCapabilities, walletCapabilityMessage, walletService, type VasIdentityResult, type VirtualAccount } from '@/lib/services/wallet';
import { identityOtpKind, isAccountOtpPending, kycService, resolveIdentityOtpRoute } from '@/lib/services/kyc';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Btn, Field } from '@/components/design/ui';
import { Label } from '@/components/design/flowkit';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

type DediAccount = { account_number: string; account_name: string; bank_name: string };

// Reuse the signed-in profile for account setup. The server owns eligibility,
// the allocation mode, and whether an account may receive real transfers.
const AddMoney = () => {
  const { c } = useTheme();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [account, setAccount] = useState<DediAccount | null>(null);
  const [fundingState, setFundingState] = useState<VirtualAccount | null>(null);
  const [vasIdentityKind, setVasIdentityKind] = useState<'bvn' | 'nin'>('bvn');
  const [vasIdentity, setVasIdentity] = useState('');
  const [vasConsent, setVasConsent] = useState(false);
  const [vasChallenge, setVasChallenge] = useState('');
  const [vasOtp, setVasOtp] = useState('');
  const [vasDelivery, setVasDelivery] = useState('');
  const [vasNotice, setVasNotice] = useState('');
  const [vasIdentityVerified, setVasIdentityVerified] = useState(false);
  const [vasResendWait, setVasResendWait] = useState(0);
  const [bvn, setBvn] = useState('');
  const [identityKind, setIdentityKind] = useState<'bvn' | 'nin'>('bvn');
  const [trackingIdentityKind, setTrackingIdentityKind] = useState<'bvn' | 'nin'>('bvn');
  const [otpDestination, setOtpDestination] = useState('');
  const [creating, setCreating] = useState(false);
  const [trackingId, setTrackingId] = useState('');
  const [otp, setOtp] = useState('');
  const loadGeneration = useRef(0);
  const actionInFlight = useRef(false);
  const facePollGeneration = useRef(0);
  const mounted = useRef(true);
  const vasGeneration = useRef(0);
  const capabilityMessage = walletCapabilityMessage(walletCapabilities(fundingState));
  const enrollmentComplete = fundingState?.account_setup_state === 'vas_validation'
    || fundingState?.enrollment_status === 'enrolled';
  const vasSetupAvailable = fundingState?.account_setup_state === 'vas_enrollment_required'
    && (fundingState.enrollment_available || (fundingState.enrollment_status === 'verification_required'
      && fundingState.enrollment_blockers?.every((blocker) => blocker === 'identity_verification')));
  const reconnectVerifiedAccount = fundingState?.provider === 'partnership'
    && fundingState.account_setup_state === 'identity_verified' && !fundingState.partnership_setup_required;

  const clearVasChallenge = () => {
    setVasChallenge(''); setVasOtp(''); setVasDelivery(''); setVasNotice(''); setVasIdentityVerified(false); setVasResendWait(0);
  };

  const beginAction = () => {
    if (actionInFlight.current) return false;
    actionInFlight.current = true;
    setCreating(true);
    return true;
  };
  const endAction = () => {
    actionInFlight.current = false;
    if (mounted.current) setCreating(false);
  };

  const loadAccount = useCallback(async () => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setLoadError('');
    // Never let a slow/hanging backend (e.g. a slow Monnify call) leave the page
    // stuck on the spinner: show the screen within a few seconds no matter what.
    // Do not show the BVN setup form when the account state is unknown: that
    // can make an existing customer start provisioning again while offline.
    const guard = setTimeout(() => {
      if (loadGeneration.current === generation) {
        setLoading(false);
        setLoadError('Your account details are taking longer than expected. Check your connection and try again.');
      }
    }, 8000);
    try {
      const r = await walletService.getAccount();
      if (loadGeneration.current !== generation) return;
      setFundingState(r);
      if (r?.success && r.provider === 'partnership' && r.account_setup_state === 'otp_pending' && r.tracking_id) {
        setTrackingId(r.tracking_id);
        const kind = identityOtpKind(r, 'bvn');
        setIdentityKind(kind);
        setTrackingIdentityKind(kind);
        setOtpDestination(r.delivery || r.otp_destination || '');
      } else if (r?.success) {
        setTrackingId('');
        setOtp('');
      }
      if (r?.success && r.account_number && (r.provider !== 'wema_vas' ||
          (r.test_mode !== true && !/^711/.test(r.account_number) && r.available === true && r.has_account === true && r.account_setup_state === 'ready'))) {
        setAccount(r as DediAccount);
        setLoadError('');
      } else if (r?.success !== true) {
        setAccount(null);
        setLoadError(r?.message || 'We could not load your funding account. Check your connection and try again.');
      } else {
        setAccount(null);
        setLoadError('');
      }
      return r;
    } catch {
      if (loadGeneration.current === generation) {
        setLoadError('We could not load your funding account. Check your connection and try again.');
      }
    } finally {
      clearTimeout(guard);
      if (loadGeneration.current === generation) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      loadGeneration.current += 1;
      facePollGeneration.current += 1;
      vasGeneration.current += 1;
    };
  }, []);

  useEffect(() => {
    if (!vasChallenge || vasResendWait <= 0) return;
    const timer = setTimeout(() => setVasResendWait((wait) => Math.max(0, wait - 1)), 1000);
    return () => clearTimeout(timer);
  }, [vasChallenge, vasResendWait]);

  useFocusEffect(useCallback(() => {
    void loadAccount();
    return () => {
      loadGeneration.current += 1;
      facePollGeneration.current += 1;
      vasGeneration.current += 1;
      setVasIdentity('');
      setVasConsent(false);
      setVasChallenge(''); setVasOtp(''); setVasDelivery(''); setVasNotice(''); setVasIdentityVerified(false); setVasResendWait(0);
    };
  }, [loadAccount]));

  const copyAccount = async () => {
    if (!account) return;
    await Clipboard.setStringAsync(account.account_number);
    notify('Copied', 'Account number copied to clipboard');
  };

  // Display the NUBAN grouped 4-3-3 ("9012 345 678"); copy stays the raw digits.
  const grouped = (n: string) => n.replace(/^(\d{4})(\d{3})(\d{3}).*$/, '$1 $2 $3');

  const handleVasResult = async (result: VasIdentityResult) => {
    if (result.success && result.otp_required && result.challenge_id) {
      setVasChallenge(result.challenge_id);
      setVasOtp('');
      setVasDelivery(result.delivery || result.otp_destination || 'your verified contact');
      setVasNotice(result.delivery_notice || '');
      setVasResendWait(Math.max(0, Math.min(600, Number(result.resend_after) || 0)));
      return;
    }
    if (result.success) {
      clearVasChallenge(); setVasConsent(false);
      await loadAccount();
      return;
    }
    if (result.identity_verified && result.retry_available && result.challenge_id) {
      setVasChallenge(result.challenge_id); setVasOtp(''); setVasIdentityVerified(true);
      setVasNotice(result.message || 'Your identity is verified. Retry account setup when the review is complete.');
      return;
    }
    if (result.code === 'vas_identity_challenge_expired' || result.retry_available === false) {
      clearVasChallenge(); setVasConsent(false);
      // A completion response can be lost after the server consumes its input.
      // Refresh durable account state before offering a new identity attempt.
      const refreshed = await loadAccount();
      if (refreshed?.enrollment_status === 'enrolled' || refreshed?.account_setup_state === 'vas_validation'
          || refreshed?.account_setup_state === 'ready') return;
    }
    notify('Account setup incomplete', result.message || 'We could not finish your account setup. Check your details and try again.');
  };

  const enrollVas = async () => {
    if (!vasConsent || vasIdentity.length !== 11 || !vasSetupAvailable || !fundingState || !beginAction()) return;
    const generation = vasGeneration.current;
    try {
      const result = await walletService.startVasIdentity(vasIdentityKind, vasIdentity,
        { enrollment_mode: fundingState.enrollment_mode, consent_version: fundingState.consent_version },
      );
      if (!mounted.current || vasGeneration.current !== generation) return;
      await handleVasResult(result);
    } catch {
      if (mounted.current && vasGeneration.current === generation) notify('Account setup incomplete', 'We could not confirm the result. Refresh your account status before trying again.');
    } finally {
      if (mounted.current && vasGeneration.current === generation) setVasIdentity('');
      endAction();
    }
  };

  const confirmVasIdentity = async () => {
    if (!vasChallenge || (!vasIdentityVerified && vasOtp.length !== 6) || !beginAction()) return;
    const generation = vasGeneration.current;
    try {
      const result = await walletService.confirmVasIdentity(vasChallenge, vasIdentityVerified ? undefined : vasOtp);
      if (mounted.current && vasGeneration.current === generation) await handleVasResult(result);
    } catch {
      if (mounted.current && vasGeneration.current === generation) notify('Could not confirm', 'Check your connection and try again.');
    } finally { endAction(); }
  };

  const resendVasIdentity = async () => {
    if (!vasChallenge || vasIdentityVerified || vasResendWait > 0 || !beginAction()) return;
    const generation = vasGeneration.current;
    try {
      const result = await walletService.resendVasIdentity(vasChallenge);
      if (mounted.current && vasGeneration.current === generation) await handleVasResult(result);
    } catch {
      if (mounted.current && vasGeneration.current === generation) notify('Could not resend code', 'Check your connection and try again.');
    } finally { endAction(); }
  };

  const createAccount = async () => {
    if ((!reconnectVerifiedAccount && bvn.length !== 11) || !beginAction()) return;
    try {
      const r = await walletService.createAccount(reconnectVerifiedAccount ? {} : { [identityKind]: bvn });
      if (r?.success && r.account_number) {
        setFundingState(r);
        setAccount(r as DediAccount);
      } else if (r?.success && r.otp_required && r.tracking_id) {
        setTrackingId(String(r.tracking_id));
        setTrackingIdentityKind(identityOtpKind(r, identityKind));
        setOtpDestination(r.delivery || r.otp_destination || '');
        notify('Verification code sent', r.message || `Enter the SMS code sent to the phone registered on your ${identityKind.toUpperCase()}.`, 'success');
      } else if (r?.success || r?.pending) {
        setFundingState((current) => ({ ...current, ...r, provider: 'partnership', account_setup_state: 'processing' }));
        notify('Account creation in progress', r.message || 'Your account setup is processing. Check again shortly.', 'info');
      } else {
        notify('Error', r?.message || "We couldn't create your account. Please try again.");
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      endAction();
    }
  };

  const confirmOtp = async () => {
    if (!trackingId || otp.length !== 6 || !beginAction()) return;
    try {
      // The server owns the pending identity and tracking reference. A resumed
      // challenge does not need the customer to enter their identity again.
      const r = await walletService.verifyWemaOtp(trackingId, otp);
      if (r.success && r.account_number) {
        setFundingState(r);
        setAccount(r as DediAccount);
        setTrackingId(''); setOtp(''); setBvn('');
      }
      else if (r.success || r.pending) {
        setTrackingId(''); setOtp(''); setBvn('');
        setFundingState((current) => ({ ...current, ...r, provider: 'partnership', account_setup_state: 'processing' }));
        notify('Verification processing', r.message || 'Your account setup is processing. Check again shortly.', 'info');
      } else notify('Verification failed', r.message || 'Check the code and try again.');
    } catch { notify('Error', 'Could not confirm the code. Please try again.'); }
    finally { endAction(); }
  };

  const resendOtp = async () => {
    if (!trackingId || !beginAction()) return;
    try {
      const r = await walletService.resendWemaOtp(trackingId);
      notify(r.success ? 'Code resent' : 'Could not resend code', r.message, r.success ? 'success' : undefined);
    } catch { notify('Error', 'Could not resend the code.'); }
    finally { endAction(); }
  };

  const useFaceVerification = async () => {
    if (bvn.length !== 11) {
      router.push({ pathname: '/kyc', params: { verify_identity: trackingId ? trackingIdentityKind : identityKind } });
      return;
    }
    if (!beginAction()) return;
    const generation = ++facePollGeneration.current;
    const isCurrent = () => mounted.current && facePollGeneration.current === generation;
    try {
      const started = await kycService.startIdentityFace({ [identityKind]: bvn });
      if (!isCurrent()) return;
      const otpRoute = resolveIdentityOtpRoute(started, identityKind);
      if (otpRoute) {
        if (otpRoute.kind !== identityKind) {
          // A previously started challenge can belong to the other identity.
          // Let KYC resume that exact server-owned route without reusing the
          // number currently entered in this form.
          router.push({
            pathname: '/kyc',
            params: {
              pending_identity: otpRoute.kind,
              pending_tracking_id: otpRoute.trackingId,
              pending_otp_destination: started.delivery || started.otp_destination || '',
            },
          });
          return;
        }
        // A face request may hand back the existing bank OTP attempt. Keep its
        // tracking reference on the SMS form instead of calling it a face outage.
        setTrackingId(otpRoute.trackingId);
        setTrackingIdentityKind(otpRoute.kind);
        setOtpDestination(started.delivery || started.otp_destination || '');
        setOtp('');
        notify('SMS verification required', started.message || `Enter the bank code sent to the phone registered on your ${otpRoute.kind.toUpperCase()}.`, 'info');
        return;
      }
      if (isAccountOtpPending(started)) {
        notify('SMS verification pending', started.message || 'Your bank verification is waiting for an SMS code. Please start the verification again.', 'info');
        return;
      }
      if (started.pending) {
        setFundingState((current) => ({ ...current, provider: 'partnership', account_setup_state: 'processing' }));
        notify('Verification processing', started.message || 'Your verification is still processing. Check again shortly.', 'info');
        return;
      }
      if (!started.success || !started.url || !started.session) {
        notify('Face verification unavailable', started.message || 'Please use the SMS code.');
        return;
      }
      beginExternalActivity();
      try { await WebBrowser.openBrowserAsync(started.url); }
      finally { endExternalActivity(); }
      if (!isCurrent()) return;
      for (let attempt = 0; attempt < 15; attempt += 1) {
        const state = await kycService.getIdentityFaceStatus(started.session);
        if (!isCurrent()) return;
        if (state.status === 'verified') {
          const refreshed = await walletService.getAccount();
          if (!isCurrent()) return;
          if (refreshed.success && refreshed.account_number) {
            setFundingState(refreshed);
            setAccount(refreshed as DediAccount);
          } else {
            setFundingState((current) => ({ ...current, ...(refreshed.success ? refreshed : {}), provider: 'partnership', account_setup_state: 'processing' }));
            notify('Verification processing', 'Your identity check is complete. Check again for your account number.', 'info');
          }
          setTrackingId(''); setOtp(''); setBvn('');
          return;
        }
        if (state.status === 'failed' || state.status === 'expired') {
          notify('Face verification incomplete', 'Please retry or use the SMS code.');
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
        if (!isCurrent()) return;
      }
      notify('Still processing', 'Our partner bank is still confirming your face check. Please return shortly.');
    } catch { if (isCurrent()) notify('Error', 'Could not complete face verification.'); }
    finally { endAction(); }
  };

  if (loading) {
    return (
      <Screen>
        <Header title="Add money" onBack={() => router.back()} />
        <Loading />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Add money" onBack={() => router.back()} />

      {loadError ? (
        <View style={{ alignItems: 'center', paddingTop: 42, paddingHorizontal: 16 }}>
          <View style={{ width: 68, height: 68, borderRadius: 22, backgroundColor: c.surface, alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="help" size={30} color={c.ink3} />
          </View>
          <Text style={{ fontSize: 17, color: c.ink1, fontFamily: font.bold, marginTop: 18, textAlign: 'center' }}>Couldn&apos;t load funding details</Text>
          <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular, marginTop: 8, textAlign: 'center', lineHeight: 20 }}>{loadError}</Text>
          <View style={{ width: '100%', marginTop: 22 }}>
            <Btn label="Try again" onPress={() => void loadAccount()} />
          </View>
        </View>
      ) : fundingState?.provider === 'partnership' && fundingState.account_setup_state === 'processing' ? (
        <View style={{ paddingTop: 12 }}>
          <Label>Account setup is processing</Label>
          <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>
            Your account setup is being confirmed. You do not need to start again. Check again shortly for your account number.
          </Text>
          <View style={{ marginTop: 20 }}><Btn label="Check again" onPress={() => void loadAccount()} /></View>
        </View>
      ) : fundingState?.account_setup_state === 'partnership_review' ? (
        <View style={{ paddingTop: 12 }}>
          <Label>Account review in progress</Label>
          <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>{fundingState.migration_message}</Text>
          <View style={{ marginTop: 20 }}><Btn label="Check again" onPress={() => void loadAccount()} /></View>
        </View>
      ) : reconnectVerifiedAccount && !trackingId ? (
        <View style={{ paddingTop: 12 }}>
          <Label>Your identity is verified</Label>
          <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>We need to confirm your existing bank account number. Your BVN or NIN verification is saved; you do not need to enter it again.</Text>
          <View style={{ marginTop: 20 }}><Btn label={creating ? 'Checking…' : 'Check my account number'} disabled={creating} onPress={createAccount} /></View>
          <View style={{ marginTop: 10 }}><Btn label="Contact support" variant="ghost" disabled={creating} onPress={() => router.push('/support')} /></View>
        </View>
      ) : fundingState?.provider === 'wema_vas' && !account ? (
        <View style={{ paddingTop: 12 }}>
          <Label>{vasAccountStatusTitle(fundingState)}</Label>
          <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>
            {fundingState.enrollment_message || fundingState.migration_message || 'Your new funding account is not available yet. Please check again shortly.'}
          </Text>
          <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginTop: 12 }}>
            Continue account setup on this Zitch profile.
          </Text>
          <Text style={{ color: c.ink3, fontFamily: font.regular, lineHeight: 20, marginTop: 12 }}>
            {capabilityMessage} Only send money when this page shows an active funding account.
          </Text>
          {vasChallenge ? (
            <View style={{ marginTop: 18 }}>
              <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginBottom: 14 }}>
                {vasIdentityVerified ? 'Your identity has been verified.' : `Enter the code sent to ${vasDelivery}. You do not need to enter your identity number again.`}
              </Text>
              {vasNotice ? <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginBottom: 14 }}>{vasNotice}</Text> : null}
              {!vasIdentityVerified ? <Field label="Verification code" value={vasOtp} onChangeText={(value) => setVasOtp(value.replace(/\D/g, '').slice(0, 6))} secureTextEntry keyboardType="number-pad" maxLength={6} autoComplete="off" editable={!creating} placeholder="6-digit code" /> : null}
              <View style={{ marginTop: 14 }}><Btn label={creating ? 'Please wait…' : vasIdentityVerified ? 'Retry account setup' : 'Confirm and set up account'} disabled={creating || (!vasIdentityVerified && vasOtp.length !== 6)} onPress={confirmVasIdentity} /></View>
              {!vasIdentityVerified ? <Btn label={vasResendWait > 0 ? `Resend code in ${vasResendWait}s` : 'Resend code'} variant="ghost" disabled={creating || vasResendWait > 0} onPress={resendVasIdentity} /> : null}
              <Btn label="Start again" variant="ghost" disabled={creating} onPress={() => { clearVasChallenge(); setVasConsent(false); }} />
            </View>
          ) : vasSetupAvailable ? (
            <>
              <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginVertical: 18 }}>
                Enter your BVN or NIN once to verify your identity and complete account setup.
              </Text>
              <View style={{ flexDirection: 'row', gap: 12, marginBottom: 14 }}>
                {(['bvn', 'nin'] as const).map((kind) => (
                  <Pressable key={kind} accessibilityRole="radio" accessibilityLabel={`Use ${kind.toUpperCase()}`} accessibilityState={{ selected: vasIdentityKind === kind }} disabled={creating} onPress={() => { setVasIdentityKind(kind); setVasIdentity(''); }}>
                    <Text style={{ color: vasIdentityKind === kind ? c.brand : c.ink3, fontFamily: font.bold }}>{kind.toUpperCase()}</Text>
                  </Pressable>
                ))}
              </View>
              <Field label={vasIdentityKind.toUpperCase()} value={vasIdentity} onChangeText={(value) => setVasIdentity(value.replace(/\D/g, '').slice(0, 11))} secureTextEntry keyboardType="number-pad" maxLength={11} autoComplete="off" autoCorrect={false} editable={!creating} placeholder={`Enter your ${vasIdentityKind.toUpperCase()}`} />
              <Pressable accessibilityRole="checkbox" accessibilityLabel="Consent to VAS identity storage and sharing" accessibilityState={{ checked: vasConsent }} disabled={creating} onPress={() => setVasConsent(!vasConsent)} style={{ flexDirection: 'row', gap: 10, marginVertical: 18 }}>
                <Text style={{ color: c.brand, fontFamily: font.bold }}>{vasConsent ? '☑' : '☐'}</Text>
                <Text style={{ flex: 1, color: c.ink2, fontFamily: font.regular, lineHeight: 20 }}>{fundingState.test_mode === true
                  ? 'I consent to Prembly identity verification, encrypted storage of my identity details, and sharing them with Wema Bank for integration validation. Account activation remains pending.'
                  : 'I consent to Prembly identity verification, encrypted storage of my identity details, and sharing them with Wema Bank to operate my account.'}</Text>
              </Pressable>
              <Btn label={creating ? 'Please wait…' : 'Set up account'} disabled={creating || !vasConsent || vasIdentity.length !== 11} onPress={enrollVas} />
            </>
          ) : null}
          <View style={{ marginTop: 14 }}>
            <Btn label="Review verification" variant="ghost" disabled={creating} onPress={() => router.push('/(auth)/kyc')} />
            {!enrollmentComplete && !vasSetupAvailable && !vasChallenge && fundingState.account_setup_state !== 'restricted' ? (
              <>
                <Btn label="Confirm my verified name" variant="ghost" disabled={creating} onPress={() => router.push({ pathname: '/(auth)/kyc', params: { verify_identity: vasIdentityKind } })} />
                <Text style={{ color: c.ink3, fontFamily: font.regular, lineHeight: 20, marginTop: 8 }}>If your earlier verification did not retain your legal name, confirm the same identity with a new verification code, then return here. Verification alone does not make an account eligible for setup.</Text>
              </>
            ) : null}
          </View>
          <View style={{ marginTop: 14 }}><Btn label="Refresh account status" variant="ghost" disabled={creating} onPress={() => void loadAccount()} /></View>
        </View>
      ) : account ? (
        <>
          <Label>Fund by bank transfer</Label>
          <View style={{ backgroundColor: c.surface, borderRadius: 18, borderWidth: 1, borderColor: c.line, padding: 18 }}>
            <Text style={{ fontSize: 13, color: c.ink3, fontFamily: font.regular }}>
              {fundingState?.provider === 'wema_vas'
                ? 'Bank transfers to this account appear in your Zitch wallet after the payment is confirmed.'
                : 'Transfer within your account limits from any bank app. Your Zitch balance updates after the payment is confirmed.'}
            </Text>
            {capabilityMessage ? <Text style={{ color: c.ink2, fontFamily: font.semibold, lineHeight: 20, marginTop: 12 }}>{capabilityMessage} {fundingState?.migration_message}</Text> : null}
            <View style={{ height: 1, backgroundColor: c.line, marginVertical: 14 }} />
            {/* Design order top-to-bottom: bank name, grouped number, account name */}
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>
              {account.bank_name}
            </Text>
            <Text style={{ fontSize: 26, color: c.ink1, fontFamily: font.extrabold, letterSpacing: 1.5, marginTop: 6, marginBottom: 2, fontVariant: ['tabular-nums'] }}>
              {grouped(account.account_number)}
            </Text>
            {account.account_name ? (
              <Text style={{ fontSize: 13, color: c.ink2, fontFamily: font.semibold }}>
                {account.account_name}
              </Text>
            ) : null}
            <View style={{ marginTop: 14 }}>
              <Btn label="Copy account number" icon="copy" variant="ghost" onPress={copyAccount} />
            </View>
          </View>

          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 9, marginTop: 18, paddingHorizontal: 4 }}>
            <ZIcon name="check" size={16} color={c.lime} stroke={2.6} />
            <Text style={{ flex: 1, fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>
              {fundingState?.provider === 'wema_vas' ? 'Use the active account shown here for new bank transfers. Check this page for changes before funding.' : 'Save this account — it\'s permanently yours. Transfers reflect automatically, no need to confirm anything here.'}
            </Text>
          </View>

          {/* other ways to fund */}
          <Label>Other ways to add money</Label>
          {[
            { icon: 'bank', title: 'Cash Deposit', sub: 'Deposit cash at a nearby Zitch agent', msg: 'Agent cash deposit is rolling out soon.' },
            { icon: 'qr', title: 'Show my QR code', sub: 'Let someone scan to pay you', msg: 'Your receive-QR is coming soon.' },
          ].map((m) => (
            <Pressable key={m.title} onPress={() => notify('Coming soon', m.msg)}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: c.surface, borderRadius: 16, borderWidth: 1, borderColor: c.line, padding: 14, marginBottom: 10 }}>
                <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
                  <ZIcon name={m.icon} size={20} color={c.brand} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>{m.title}</Text>
                  <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>{m.sub}</Text>
                </View>
                <ZIcon name="right" size={18} color={c.ink3} />
              </View>
            </Pressable>
          ))}
        </>
      ) : (
        <View style={{ paddingTop: 12 }}>
          <View style={{ alignItems: 'center', paddingHorizontal: 16 }}>
            <View style={{ width: 84, height: 84, borderRadius: 26, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name="bank" size={40} color={c.brand} />
            </View>
            <Text style={{ fontSize: 19, color: c.ink1, fontFamily: font.extrabold, marginTop: 22, textAlign: 'center' }}>
              Get your Zitch account number
            </Text>
            <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular, marginTop: 10, textAlign: 'center', lineHeight: 21 }}>
              {fundingState?.partnership_setup_required
                ? 'Your Zitch identity verification is saved. Confirm the same BVN or NIN with your bank to finish setting up your funding account.'
                : 'Choose BVN or NIN, then verify by SMS code or face to set up your funding account.'}
            </Text>
          </View>

          <View style={{ height: 22 }} />
          {trackingId ? (
            <>
              <Text style={{ color: c.ink2, fontFamily: font.regular, marginBottom: 12 }}>Enter the SMS code{otpDestination ? ` sent to ${otpDestination}` : ` sent to the phone linked to your ${trackingIdentityKind.toUpperCase()}` }.</Text>
              <Field label="Verification code" value={otp} onChangeText={(v) => setOtp(v.replace(/\D/g, '').slice(0, 6))} keyboardType="number-pad" placeholder="Enter 6-digit SMS code" />
            </>
          ) : (
            <>
              <View style={{ flexDirection: 'row', gap: 20, marginBottom: 14 }}>
                {(['bvn', 'nin'] as const).map((kind) => <Pressable key={kind} accessibilityRole="radio" accessibilityLabel={`Use ${kind.toUpperCase()}`} accessibilityState={{ selected: identityKind === kind }} disabled={creating} onPress={() => { setIdentityKind(kind); setBvn(''); }}>
                  <Text style={{ color: identityKind === kind ? c.brand : c.ink3, fontFamily: font.bold }}>{kind.toUpperCase()}</Text>
                </Pressable>)}
              </View>
              <Field label={identityKind === 'bvn' ? 'Bank Verification Number (BVN)' : 'National Identification Number (NIN)'} value={bvn} onChangeText={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))} keyboardType="number-pad" placeholder={`Enter your 11-digit ${identityKind.toUpperCase()}`} />
            </>
          )}
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 8, paddingHorizontal: 2 }}>
            <ZIcon name="lock" size={13} color={c.ink3} />
            <Text style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.regular }}>
              {identityKind === 'bvn' ? 'Dial *565*0# on your registered line to get your BVN.' : 'Use the NIN issued to you. Your identity details are protected.'}
            </Text>
          </View>

          <View style={{ height: 22 }} />
          <Btn label={creating ? 'Please wait…' : trackingId ? 'Confirm code' : 'Get my account'} icon="bank" disabled={creating || (trackingId ? otp.length !== 6 : bvn.length !== 11)} onPress={trackingId ? confirmOtp : createAccount} />
          <View style={{ height: 10 }} />
          <Btn label="Use face verification instead" variant="ghost" disabled={creating || (!trackingId && bvn.length !== 11)} onPress={useFaceVerification} />
          {trackingId && (
            <>
              <Pressable disabled={creating} onPress={resendOtp} style={{ marginTop: 14 }}>
                <Text style={{ textAlign: 'center', color: c.brand, fontFamily: font.semibold }}>Resend SMS code</Text>
              </Pressable>
            </>
          )}
        </View>
      )}
    </Screen>
  );
};

export default AddMoney;
