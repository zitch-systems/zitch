import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import * as WebBrowser from 'expo-web-browser';
import { router } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { walletCapabilities, walletCapabilityMessage, walletService, type VirtualAccount } from '@/lib/services/wallet';
import { isAccountOtpPending, kycService, resolveIdentityOtpRoute } from '@/lib/services/kyc';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Btn, Field } from '@/components/design/ui';
import { Label } from '@/components/design/flowkit';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

type DediAccount = { account_number: string; account_name: string; bank_name: string };

// Funding is bank-transfer only. The partner bank creates the dedicated NUBAN asynchronously
// after BVN consent by SMS OTP or its hosted face-verification alternative.
const AddMoney = () => {
  const { c } = useTheme();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [account, setAccount] = useState<DediAccount | null>(null);
  const [fundingState, setFundingState] = useState<VirtualAccount | null>(null);
  const [vasIdentityKind, setVasIdentityKind] = useState<'bvn' | 'nin'>('bvn');
  const [vasIdentity, setVasIdentity] = useState('');
  const [vasConsent, setVasConsent] = useState(false);
  const [bvn, setBvn] = useState('');
  const [creating, setCreating] = useState(false);
  const [trackingId, setTrackingId] = useState('');
  const [otp, setOtp] = useState('');
  const loadGeneration = useRef(0);
  const actionInFlight = useRef(false);
  const facePollGeneration = useRef(0);
  const mounted = useRef(true);
  const capabilityMessage = walletCapabilityMessage(walletCapabilities(fundingState));
  const validationAccountNumber = fundingState?.provider === 'wema_vas'
    && fundingState.test_mode === true
    && fundingState.account_setup_state === 'vas_validation'
    && /^711\d{7}$/.test(fundingState.validation_account_number || '')
    ? fundingState.validation_account_number : '';

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
      if (r?.success && r.account_number && (r.provider !== 'wema_vas' ||
          (r.test_mode !== true && r.available === true && r.has_account === true && r.account_setup_state === 'ready'))) {
        setAccount(r as DediAccount);
        setLoadError('');
      } else if (r?.offline) {
        setLoadError('We could not load your funding account. Check your connection and try again.');
      } else {
        setAccount(null);
        setLoadError('');
      }
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
    void loadAccount();
    return () => {
      mounted.current = false;
      loadGeneration.current += 1;
      facePollGeneration.current += 1;
    };
  }, [loadAccount]);

  const copyAccount = async () => {
    if (!account) return;
    await Clipboard.setStringAsync(account.account_number);
    notify('Copied', 'Account number copied to clipboard');
  };

  // Display the NUBAN grouped 4-3-3 ("9012 345 678"); copy stays the raw digits.
  const grouped = (n: string) => n.replace(/^(\d{4})(\d{3})(\d{3}).*$/, '$1 $2 $3');

  const enrollVas = async () => {
    if (!vasConsent || vasIdentity.length !== 11 || !fundingState?.enrollment_available || !beginAction()) return;
    try {
      const result = await walletService.enrollVas(
        vasIdentityKind === 'bvn' ? { bvn: vasIdentity } : { nin: vasIdentity },
      );
      if (!mounted.current) return;
      if (result.success) {
        setVasConsent(false);
        await loadAccount();
      } else {
        notify('Account setup incomplete', result.message || 'Your verified identity could not be confirmed. Please check your verification status or contact support.');
      }
    } catch {
      if (mounted.current) notify('Account setup incomplete', 'We could not confirm the result. Refresh your account status before trying again.');
    } finally {
      if (mounted.current) setVasIdentity('');
      endAction();
    }
  };

  const createAccount = async () => {
    if (bvn.length !== 11 || !beginAction()) return;
    try {
      const r = await walletService.createAccount(bvn);
      if (r?.success && r.account_number) {
        setAccount(r as DediAccount);
      } else if (r?.success && r.otp_required && r.tracking_id) {
        setTrackingId(String(r.tracking_id));
        notify('Verification code sent', r.message || 'Enter the SMS code sent to the phone registered on your BVN.', 'success');
      } else if (r?.success) {
        notify('Account creation in progress', r.message || 'Our partner bank is creating your account number. We will update this page when it is ready.', 'success');
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
      const r = await walletService.verifyWemaOtp(trackingId, otp, { bvn });
      if (r.success && r.account_number) setAccount(r as DediAccount);
      else if (r.success || r.pending) {
        setTrackingId(''); setOtp('');
        notify('Identity accepted', r.message || 'Your account number is being created.', 'success');
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
    if (bvn.length !== 11 || !beginAction()) return;
    const generation = ++facePollGeneration.current;
    const isCurrent = () => mounted.current && facePollGeneration.current === generation;
    try {
      const started = await kycService.startIdentityFace({ bvn });
      if (!isCurrent()) return;
      const otpRoute = resolveIdentityOtpRoute(started, 'bvn');
      if (otpRoute) {
        if (otpRoute.kind === 'nin') {
          // This screen owns a BVN field and its confirm/resend actions are
          // consequently BVN-scoped. Never put a NIN tracking reference into
          // that form; let KYC resume the server-selected identity route.
          router.push({
            pathname: '/kyc',
            params: {
              pending_identity: 'nin',
              pending_tracking_id: otpRoute.trackingId,
              pending_otp_destination: started.delivery || started.otp_destination || '',
            },
          });
          return;
        }
        // A face request may hand back the existing bank OTP attempt. Keep its
        // tracking reference on the SMS form instead of calling it a face outage.
        setTrackingId(otpRoute.trackingId);
        setOtp('');
        notify('SMS verification required', started.message || 'Enter the bank code sent to the phone registered on your BVN.', 'info');
        return;
      }
      if (isAccountOtpPending(started)) {
        notify('SMS verification pending', started.message || 'Your bank verification is waiting for an SMS code. Please start the verification again.', 'info');
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
          if (refreshed.success && refreshed.account_number) setAccount(refreshed as DediAccount);
          else notify('Identity verified', 'Our partner bank is creating your account number. We will update it automatically.', 'success');
          setTrackingId(''); setOtp('');
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
      ) : fundingState?.provider === 'wema_vas' && !account ? (
        <View style={{ paddingTop: 12 }}>
          <Label>{fundingState.account_setup_state === 'restricted' ? 'Account restricted' : fundingState.test_mode ? 'Account testing' : 'Your new funding account'}</Label>
          <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21 }}>
            {fundingState.migration_message || 'Your new funding account is not available yet. Please check again shortly.'}
          </Text>
          <Text style={{ color: c.ink3, fontFamily: font.regular, lineHeight: 20, marginTop: 12 }}>
            {capabilityMessage} Only send money when this page shows an active funding account.
          </Text>
          {!!validationAccountNumber && (
            <View style={{ backgroundColor: c.surface, borderRadius: 18, borderWidth: 1, borderColor: c.line, padding: 18, marginTop: 18 }}>
              <Text style={{ color: c.ink1, fontFamily: font.bold }}>Test account only</Text>
              <Text accessibilityRole="alert" style={{ color: c.ink2, fontFamily: font.semibold, lineHeight: 20, marginTop: 8 }}>Do not send money to this account. This sample number is for the approved bank integration tests only.</Text>
              <Text style={{ fontSize: 26, color: c.ink1, fontFamily: font.extrabold, marginTop: 12, fontVariant: ['tabular-nums'] }}>{grouped(validationAccountNumber)}</Text>
              {!!fundingState.validation_account_name && <Text style={{ color: c.ink2, fontFamily: font.regular, marginTop: 4 }}>{fundingState.validation_account_name}</Text>}
              <View style={{ marginTop: 12 }}>
                <Btn label="Copy test account number" icon="copy" variant="ghost" onPress={async () => {
                  await Clipboard.setStringAsync(validationAccountNumber);
                  notify('Test number copied', 'Share only for the approved bank tests. Do not fund this account.');
                }} />
              </View>
            </View>
          )}
          {fundingState.enrollment_available && fundingState.account_setup_state === 'vas_enrollment_required' ? (
            <>
              <Text style={{ color: c.ink2, fontFamily: font.regular, lineHeight: 21, marginVertical: 18 }}>
                Re-enter the BVN or NIN you have already verified with Zitch. Your existing verification and balance are preserved.
              </Text>
              <View style={{ flexDirection: 'row', gap: 12, marginBottom: 14 }}>
                {(['bvn', 'nin'] as const).map((kind) => (
                  <Pressable key={kind} accessibilityRole="radio" accessibilityLabel={`Use ${kind.toUpperCase()}`} accessibilityState={{ selected: vasIdentityKind === kind }} disabled={creating} onPress={() => { setVasIdentityKind(kind); setVasIdentity(''); }}>
                    <Text style={{ color: vasIdentityKind === kind ? c.brand : c.ink3, fontFamily: font.bold }}>{kind.toUpperCase()}</Text>
                  </Pressable>
                ))}
              </View>
              <Field label={`Verified ${vasIdentityKind.toUpperCase()}`} value={vasIdentity} onChangeText={(value) => setVasIdentity(value.replace(/\D/g, '').slice(0, 11))} secureTextEntry keyboardType="number-pad" maxLength={11} autoComplete="off" autoCorrect={false} editable={!creating} placeholder={`Enter your verified ${vasIdentityKind.toUpperCase()}`} />
              <Pressable accessibilityRole="checkbox" accessibilityLabel="Consent to VAS identity storage and sharing" accessibilityState={{ checked: vasConsent }} disabled={creating} onPress={() => setVasConsent(!vasConsent)} style={{ flexDirection: 'row', gap: 10, marginVertical: 18 }}>
                <Text style={{ color: c.brand, fontFamily: font.bold }}>{vasConsent ? '☑' : '☐'}</Text>
                <Text style={{ flex: 1, color: c.ink2, fontFamily: font.regular, lineHeight: 20 }}>I consent to Zitch securely storing my verified identity details in encrypted form and sharing them with Wema Bank to operate my virtual account.</Text>
              </Pressable>
              <Btn label={creating ? 'Please wait…' : 'Set up virtual account'} disabled={creating || !vasConsent || vasIdentity.length !== 11} onPress={enrollVas} />
              <View style={{ marginTop: 14 }}>
                <Btn label="Confirm my verified name" variant="ghost" disabled={creating} onPress={() => router.push({ pathname: '/(auth)/kyc', params: { verify_identity: vasIdentityKind } })} />
                <Text style={{ color: c.ink3, fontFamily: font.regular, lineHeight: 20, marginTop: 8 }}>If your earlier verification did not retain your legal name, confirm it with a new identity verification code, then return here.</Text>
              </View>
            </>
          ) : null}
          <View style={{ marginTop: 14 }}><Btn label="Refresh account status" variant="ghost" disabled={creating} onPress={() => void loadAccount()} /></View>
        </View>
      ) : account ? (
        <>
          <Label>Fund by bank transfer</Label>
          <View style={{ backgroundColor: c.surface, borderRadius: 18, borderWidth: 1, borderColor: c.line, padding: 18 }}>
            <Text style={{ fontSize: 13, color: c.ink3, fontFamily: font.regular }}>
              {fundingState?.provider === 'wema_vas'
                ? 'Bank transfers to this account appear in your Zitch wallet after the payment is confirmed.'
                : 'Transfer any amount to this account from any bank app — your Zitch wallet is credited automatically, usually within seconds.'}
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
              Enter your BVN to instantly get a dedicated account for funding by bank transfer — no
              card needed. It&apos;s verified securely; we never store it.
            </Text>
          </View>

          <View style={{ height: 22 }} />
          {trackingId ? (
            <Field label="Verification code" value={otp} onChangeText={(v) => setOtp(v.replace(/\D/g, '').slice(0, 6))} keyboardType="number-pad" placeholder="Enter 6-digit SMS code" />
          ) : (
            <Field label="Bank Verification Number (BVN)" value={bvn} onChangeText={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))} keyboardType="number-pad" placeholder="Enter your 11-digit BVN" />
          )}
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 8, paddingHorizontal: 2 }}>
            <ZIcon name="lock" size={13} color={c.ink3} />
            <Text style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.regular }}>
              Dial *565*0# on your registered line to get your BVN.
            </Text>
          </View>

          <View style={{ height: 22 }} />
          <Btn label={creating ? 'Please wait…' : trackingId ? 'Confirm code' : 'Get my account'} icon="bank" disabled={creating || (trackingId ? otp.length !== 6 : bvn.length !== 11)} onPress={trackingId ? confirmOtp : createAccount} />
          {trackingId && (
            <>
              <View style={{ height: 10 }} />
              <Btn label="Use face verification instead" variant="ghost" disabled={creating} onPress={useFaceVerification} />
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
