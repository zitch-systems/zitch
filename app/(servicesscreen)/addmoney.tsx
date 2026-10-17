import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import * as WebBrowser from 'expo-web-browser';
import { router, useFocusEffect } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { isFundingAccountReady, walletCapabilities, walletCapabilityMessage, walletService, type FundingAccount } from '@/lib/services/wallet';
import { identityOtpKind, isAccountOtpPending, kycService, resolveIdentityOtpRoute } from '@/lib/services/kyc';
import { getSessionGeneration } from '@/lib/secureStore';
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
  const [fundingState, setFundingState] = useState<FundingAccount | null>(null);
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
  const sessionGeneration = useRef(getSessionGeneration());
  const isCurrentSession = useCallback(() => mounted.current && sessionGeneration.current === getSessionGeneration(), []);
  const capabilityMessage = walletCapabilityMessage(walletCapabilities(fundingState));
  const accountReview = fundingState?.provider !== 'partnership' || fundingState.legacy_funding_review === true
    || fundingState.account_setup_state === 'legacy_funding_review'
    || (!account && ['ready', 'bank_history_review'].includes(fundingState.account_setup_state || ''));
  const reconnectVerifiedAccount = fundingState?.provider === 'partnership'
    && fundingState.account_setup_state === 'identity_verified' && !fundingState.partnership_setup_required;

  const beginAction = () => {
    if (!isCurrentSession() || actionInFlight.current) return false;
    actionInFlight.current = true;
    setCreating(true);
    return true;
  };
  const endAction = () => {
    actionInFlight.current = false;
    if (isCurrentSession()) setCreating(false);
  };

  const loadAccount = useCallback(async () => {
    if (!isCurrentSession()) return;
    const generation = ++loadGeneration.current;
    setLoading(true);
    setLoadError('');
    // Never let a slow backend leave the page
    // stuck on the spinner: show the screen within a few seconds no matter what.
    // Do not show the BVN setup form when the account state is unknown: that
    // can make an existing customer start provisioning again while offline.
    const guard = setTimeout(() => {
      if (isCurrentSession() && loadGeneration.current === generation) {
        setLoading(false);
        setLoadError('Your account details are taking longer than expected. Check your connection and try again.');
      }
    }, 8000);
    try {
      const r = await walletService.getAccount();
      if (!isCurrentSession() || loadGeneration.current !== generation) return;
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
      if (r?.success && isFundingAccountReady(r)) {
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
      if (isCurrentSession() && loadGeneration.current === generation) {
        setLoadError('We could not load your funding account. Check your connection and try again.');
      }
    } finally {
      clearTimeout(guard);
      if (isCurrentSession() && loadGeneration.current === generation) setLoading(false);
    }
  }, [isCurrentSession]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      loadGeneration.current += 1;
      facePollGeneration.current += 1;
    };
  }, []);

  useFocusEffect(useCallback(() => {
    void loadAccount();
    return () => {
      loadGeneration.current += 1;
      facePollGeneration.current += 1;
    };
  }, [loadAccount]));

  const copyAccount = async () => {
    if (!isCurrentSession() || !account) return;
    try {
      await Clipboard.setStringAsync(account.account_number);
      if (isCurrentSession()) notify('Copied', 'Account number copied to clipboard');
    } catch {
      if (isCurrentSession()) notify('Could not copy', 'Please copy the account number shown on this page manually.');
    }
  };

  // Display the NUBAN grouped 4-3-3 ("9012 345 678"); copy stays the raw digits.
  const grouped = (n: string) => n.replace(/^(\d{4})(\d{3})(\d{3}).*$/, '$1 $2 $3');

  const createAccount = async () => {
    if ((!reconnectVerifiedAccount && bvn.length !== 11) || !beginAction()) return;
    try {
      const r = await walletService.createAccount(reconnectVerifiedAccount ? {} : { [identityKind]: bvn });
      if (!isCurrentSession()) return;
      if (r?.success && r.account_number) {
        await loadAccount();
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
      if (isCurrentSession()) notify('Error', 'Something went wrong. Please try again later.');
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
      if (!isCurrentSession()) return;
      if (r.success && r.account_number) {
        await loadAccount();
        if (!isCurrentSession()) return;
        setTrackingId(''); setOtp(''); setBvn('');
      }
      else if (r.success || r.pending) {
        setTrackingId(''); setOtp(''); setBvn('');
        setFundingState((current) => ({ ...current, ...r, provider: 'partnership', account_setup_state: 'processing' }));
        notify('Verification processing', r.message || 'Your account setup is processing. Check again shortly.', 'info');
      } else notify('Verification failed', r.message || 'Check the code and try again.');
    } catch { if (isCurrentSession()) notify('Error', 'Could not confirm the code. Please try again.'); }
    finally { endAction(); }
  };

  const resendOtp = async () => {
    if (!trackingId || !beginAction()) return;
    try {
      const r = await walletService.resendWemaOtp(trackingId);
      if (!isCurrentSession()) return;
      notify(r.success ? 'Code resent' : 'Could not resend code', r.message, r.success ? 'success' : undefined);
    } catch { if (isCurrentSession()) notify('Error', 'Could not resend the code.'); }
    finally { endAction(); }
  };

  const useFaceVerification = async () => {
    if (!isCurrentSession()) return;
    if (bvn.length !== 11) {
      router.push({ pathname: '/kyc', params: { verify_identity: trackingId ? trackingIdentityKind : identityKind } });
      return;
    }
    if (!beginAction()) return;
    const generation = ++facePollGeneration.current;
    const isCurrent = () => isCurrentSession() && facePollGeneration.current === generation;
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
          if (refreshed.success && isFundingAccountReady(refreshed)) {
            setFundingState(refreshed);
            setAccount(refreshed as DediAccount);
          } else {
            setFundingState((current) => refreshed.success ? refreshed
              : { ...current, provider: 'partnership', account_setup_state: 'processing' });
            if (refreshed.success && (refreshed.legacy_funding_review || refreshed.account_setup_state === 'legacy_funding_review')) {
              notify('Account review', refreshed.migration_message || 'Your account needs review. Contact support before adding money.', 'info');
            } else {
              notify('Verification processing', 'Your identity check is complete. Check again for your account number.', 'info');
            }
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
      ) : accountReview ? (
        <View style={{ paddingTop: 12 }}>
          <Label>Account review in progress</Label>
          <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>{fundingState?.migration_message || 'Your funding account needs review. Contact support before adding money.'}</Text>
          <View style={{ marginTop: 12 }}><Btn label="Contact support" variant="outline" onPress={() => router.push('/support')} /></View>
          <View style={{ marginTop: 20 }}><Btn label="Check again" onPress={() => void loadAccount()} /></View>
        </View>
      ) : fundingState?.provider === 'partnership' && fundingState.account_setup_state === 'processing' ? (
        <View style={{ paddingTop: 12 }}>
          <Label>Account setup is processing</Label>
          <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>
            Your account setup is being confirmed. You do not need to start again. Check again shortly for your account number.
          </Text>
          <View style={{ marginTop: 20 }}><Btn label="Check again" onPress={() => void loadAccount()} /></View>
        </View>
      ) : reconnectVerifiedAccount && !trackingId ? (
        <View style={{ paddingTop: 12 }}>
          <Label>Your identity is verified</Label>
          <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>We need to confirm your existing bank account number. Your BVN or NIN verification is saved; you do not need to enter it again.</Text>
          <View style={{ marginTop: 20 }}><Btn label={creating ? 'Checking…' : 'Check my account number'} disabled={creating} onPress={createAccount} /></View>
          <View style={{ marginTop: 10 }}><Btn label="Contact support" variant="ghost" disabled={creating} onPress={() => router.push('/support')} /></View>
        </View>
      ) : account ? (
        <>
          <Label>Fund by bank transfer</Label>
          <View style={{ backgroundColor: c.surface, borderRadius: 18, borderWidth: 1, borderColor: c.line, padding: 18 }}>
            <Text style={{ fontSize: 13, color: c.ink3, fontFamily: font.regular }}>
              Transfer within your account limits from any bank app. Your Zitch balance updates after the payment is confirmed.
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
              Use the current account shown here for new bank transfers. Check this page before funding.
            </Text>
          </View>

          {/* other ways to fund */}
          <Label>Other ways to add money</Label>
          {[
            { icon: 'bank', title: 'Cash Deposit', sub: 'Coming soon — agent cash deposits are not available yet.' },
            { icon: 'qr', title: 'Show my QR code', sub: 'Coming soon — use your funding account to receive money.' },
          ].map((m) => (
            <View key={m.title} accessible accessibilityLabel={`${m.title}. ${m.sub}`}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: c.surface, borderRadius: 16, borderWidth: 1, borderColor: c.line, padding: 14, marginBottom: 10 }}>
                <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
                  <ZIcon name={m.icon} size={20} color={c.brand} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>{m.title}</Text>
                  <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>{m.sub}</Text>
                </View>
              </View>
            </View>
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
                {(['bvn', 'nin'] as const).map((kind) => <Pressable key={kind} accessibilityRole="radio" accessibilityLabel={`Use ${kind.toUpperCase()}`} accessibilityState={{ selected: identityKind === kind }} disabled={creating} style={{ minWidth: 64, minHeight: 44, alignItems: 'center', justifyContent: 'center' }} onPress={() => { setIdentityKind(kind); setBvn(''); }}>
                  <Text style={{ color: identityKind === kind ? c.brand : c.ink3, fontFamily: font.bold }}>{kind.toUpperCase()}</Text>
                </Pressable>)}
              </View>
              <Field label={identityKind === 'bvn' ? 'Bank Verification Number (BVN)' : 'National Identification Number (NIN)'} value={bvn} onChangeText={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))} keyboardType="number-pad" placeholder={`Enter your 11-digit ${identityKind.toUpperCase()}`} />
            </>
          )}
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 8, paddingHorizontal: 2 }}>
            <ZIcon name="lock" size={13} color={c.ink3} />
            <Text style={{ flex: 1, fontSize: 11.5, color: c.ink3, fontFamily: font.regular }}>
              {identityKind === 'bvn' ? 'Dial *565*0# on your registered line to get your BVN.' : 'Use the NIN issued to you. Your identity details are protected.'}
            </Text>
          </View>

          <View style={{ height: 22 }} />
          <Btn label={creating ? 'Please wait…' : trackingId ? 'Confirm code' : 'Get my account'} icon="bank" disabled={creating || (trackingId ? otp.length !== 6 : bvn.length !== 11)} onPress={trackingId ? confirmOtp : createAccount} />
          <View style={{ height: 10 }} />
          <Btn label="Use face verification instead" variant="ghost" disabled={creating || (!trackingId && bvn.length !== 11)} onPress={useFaceVerification} />
          {trackingId && (
            <>
              <Pressable accessibilityRole="button" accessibilityLabel="Resend SMS code" accessibilityState={{ disabled: creating }} disabled={creating} onPress={resendOtp} style={{ marginTop: 14, minHeight: 44, justifyContent: 'center' }}>
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
