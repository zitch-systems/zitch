import React, { useEffect, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { router } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { apiJson } from '@/lib/api';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Btn, Field } from '@/components/design/ui';
import { Label } from '@/components/design/flowkit';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

type DediAccount = {
  account_number: string;
  account_name: string;
  bank_name: string;
  bank_tier?: number;
};

// Funding is by bank transfer to a dedicated Zitch (Wema/ALAT) NUBAN — minted via
// Wema's reserved-account onboarding (enter BVN; Wema verifies it and issues the
// NUBAN over a one-time OTP). Wema has no hosted checkout, so there is no instant
// card/bank pay-in here; deposits to the NUBAN are credited automatically by the
// reconcile poller.
const AddMoney = () => {
  const { c } = useTheme();
  const [loading, setLoading] = useState(true);
  const [account, setAccount] = useState<DediAccount | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  // The customer's registered legal name — shown as the account name whenever the
  // provider omits it, so the funding card never renders a nameless account (which
  // a payer can't confirm before transferring).
  const [holderName, setHolderName] = useState('');
  const [bvn, setBvn] = useState('');
  const [creating, setCreating] = useState(false);

  // Wema/ALAT flow: account creation is a BVN + OTP round-trip. When the backend
  // answers otp_required, we hold the tracking id and show the OTP step.
  const [otpFlow, setOtpFlow] = useState<{ trackingId: string; destination: string } | null>(null);
  const [otp, setOtp] = useState('');
  const [verifying, setVerifying] = useState(false);

  const loadAccount = () => {
    let alive = true;
    setLoading(true);
    setLoadFailed(false);
    // Never let a slow/hanging backend leave the page stuck on the spinner: show
    // the screen within a few seconds no matter what. If the account lookup
    // resolves later, it still fills in (account state).
    const guard = setTimeout(() => { if (alive) setLoading(false); }, 8000);
    apiJson('/api/wallet/account/')
      .then((r) => {
        if (!alive || !r?.success) return;
        if (r.holder_name) setHolderName(r.holder_name);
        if (r.account_number) setAccount(r as DediAccount);
      })
      .catch(() => { if (alive) setLoadFailed(true); })
      .finally(() => { if (alive) { clearTimeout(guard); setLoading(false); } });
    return () => { alive = false; clearTimeout(guard); };
  };

  useEffect(() => {
    let cleanup: undefined | (() => void);
    const timer = setTimeout(() => { cleanup = loadAccount(); }, 0);
    return () => { clearTimeout(timer); cleanup?.(); };
  }, []);

  const copyAccount = async () => {
    if (!account) return;
    await Clipboard.setStringAsync(account.account_number);
    notify('Copied', 'Account number copied to clipboard');
  };

  const createAccount = async () => {
    if (bvn.length !== 11) return;
    setCreating(true);
    try {
      const r = await apiJson('/api/wallet/account/create/', { bvn });
      if (r?.success && r.account_number) {
        setAccount(r as DediAccount);
      } else if (r?.success && r.otp_required) {
        // Wema flow: an OTP was sent to the user's phone — collect it next.
        setOtpFlow({ trackingId: String(r.tracking_id || ''), destination: String(r.otp_destination || '') });
        setOtp('');
        notify('OTP sent', `Enter the code we sent to ${r.otp_destination || 'your phone'}`);
      } else {
        notify('Error', r?.message || "We couldn't create your account. Please try again.");
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setCreating(false);
    }
  };

  const verifyOtp = async () => {
    if (!otpFlow || otp.length < 4) return;
    setVerifying(true);
    try {
      const r = await apiJson('/api/wallet/wema/verify-otp/', {
        // The server binds this opaque tracking ID to the identity used to start
        // the flow.  Never resend the BVN (or let a client swap identity/type) at
        // verification time.
        otp, tracking_id: otpFlow.trackingId,
      });
      if (r?.success && r.account_number) {
        setAccount(r as DediAccount);
        setOtpFlow(null);
        notify('Account ready', 'Your Zitch account number is ready — fund it by bank transfer.');
      } else {
        notify('Error', r?.message || 'OTP verification failed. Please try again.');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setVerifying(false);
    }
  };

  const resendOtp = async () => {
    if (!otpFlow) return;
    try {
      const r = await apiJson('/api/wallet/wema/resend-otp/', {
        tracking_id: otpFlow.trackingId,
      });
      notify(r?.success ? 'OTP resent' : 'Error', r?.message || (r?.success ? 'Check your phone' : "Couldn't resend the OTP"));
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    }
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

      {loadFailed && !account && (
        <View style={{ backgroundColor: c.surface, borderRadius: 14, borderWidth: 1, borderColor: c.line, padding: 14, marginBottom: 16 }}>
          <Text style={{ fontFamily: font.regular, fontSize: 13, color: c.ink3, lineHeight: 19 }}>
            We couldn’t check your funding account. Retry before creating a new one.
          </Text>
          <Pressable onPress={loadAccount} accessibilityRole="button" style={{ marginTop: 9 }}>
            <Text style={{ fontFamily: font.bold, fontSize: 13, color: c.brand }}>Retry</Text>
          </Pressable>
        </View>
      )}

      {/* Fund by bank transfer to a dedicated Wema/ALAT NUBAN. Wema has no hosted
          checkout — deposits are credited automatically by the reconcile poller. */}
      <Label>Fund by bank transfer</Label>
      {account ? (
        <>
          <View style={{ backgroundColor: c.surface, borderRadius: 18, borderWidth: 1, borderColor: c.line, padding: 18 }}>
            <Text style={{ fontSize: 13, color: c.ink3, fontFamily: font.regular }}>
              Transfer any amount to this account from any bank app — your Zitch wallet is credited
              automatically, usually within seconds.
            </Text>
            {/* Account number + one-tap copy */}
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 16 }}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.medium, marginBottom: 2 }}>Account number</Text>
                <Text style={{ fontSize: 26, color: c.ink1, fontFamily: font.extrabold, letterSpacing: 1.5 }}>
                  {account.account_number}
                </Text>
              </View>
              <Pressable
                onPress={copyAccount}
                hitSlop={10}
                accessibilityRole="button"
                accessibilityLabel="Copy account number"
                style={{ flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: 'rgba(15,162,149,.12)', borderRadius: 12, paddingVertical: 10, paddingHorizontal: 14 }}
              >
                <ZIcon name="copy" size={15} color={c.brand} />
                <Text style={{ fontSize: 13.5, color: c.brand, fontFamily: font.bold }}>Copy</Text>
              </Pressable>
            </View>
            {/* Bank + account name — the details a payer confirms before sending.
                account_name always resolves to a name (provider → registered name),
                so the card is never nameless. */}
            <View style={{ height: 1, backgroundColor: c.line, marginVertical: 14 }} />
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
              <View style={{ minWidth: 0 }}>
                <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.medium }}>Bank</Text>
                <Text style={{ fontSize: 14.5, color: c.ink1, fontFamily: font.bold, marginTop: 3 }}>
                  {account.bank_name || 'Wema Bank'}
                </Text>
              </View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.medium, textAlign: 'right' }}>Account name</Text>
                <Text numberOfLines={2} style={{ fontSize: 14.5, color: c.ink1, fontFamily: font.bold, marginTop: 3, textAlign: 'right' }}>
                  {account.account_name || holderName || 'Your Zitch account'}
                </Text>
              </View>
            </View>
          </View>

          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 9, marginTop: 18, paddingHorizontal: 4 }}>
            <ZIcon name="check" size={16} color={c.lime} stroke={2.6} />
            <Text style={{ flex: 1, fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>
              Save this account — it’s permanently yours. Transfers reflect automatically, no need to confirm anything here.
            </Text>
          </View>
          <View style={{ marginTop: 12, paddingHorizontal: 4 }}>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular, lineHeight: 19 }}>
              Partner-bank tier {account.bank_tier || 1}: single inflow up to {account.bank_tier === 2 ? '₦100,000' : account.bank_tier === 3 ? 'no stated limit' : '₦50,000'}; maximum balance {account.bank_tier === 2 ? '₦500,000' : account.bank_tier === 3 ? 'has no stated limit' : '₦300,000'}.
            </Text>
          </View>
        </>
      ) : otpFlow ? (
        <View style={{ paddingTop: 6 }}>
          <View style={{ alignItems: 'center', paddingHorizontal: 16 }}>
            <View style={{ width: 72, height: 72, borderRadius: 22, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name="lock" size={34} color={c.brand} />
            </View>
            <Text style={{ fontSize: 17, color: c.ink1, fontFamily: font.extrabold, marginTop: 16, textAlign: 'center' }}>
              Enter the OTP
            </Text>
            <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular, marginTop: 8, textAlign: 'center', lineHeight: 20 }}>
              We sent a one-time code to {otpFlow.destination || 'your phone'} to confirm your account.
            </Text>
          </View>

          <View style={{ height: 18 }} />
          <Field
            label="One-time code"
            value={otp}
            onChangeText={(v) => setOtp(v.replace(/\D/g, '').slice(0, 8))}
            keyboardType="number-pad"
            placeholder="Enter the code"
          />
          <View style={{ height: 18 }} />
          <Btn
            label={verifying ? 'Confirming…' : 'Confirm code'}
            icon="check"
            disabled={verifying || otp.length < 4}
            onPress={verifyOtp}
          />
          <Pressable onPress={resendOtp} hitSlop={10} style={{ alignItems: 'center', marginTop: 16 }}>
            <Text style={{ fontSize: 13.5, color: c.brand, fontFamily: font.bold }}>Resend code</Text>
          </Pressable>
          <Pressable onPress={() => { setOtpFlow(null); setOtp(''); }} hitSlop={10} style={{ alignItems: 'center', marginTop: 12 }}>
            <Text style={{ fontSize: 13, color: c.ink3, fontFamily: font.medium }}>Start over</Text>
          </Pressable>
        </View>
      ) : (
        <View style={{ paddingTop: 6 }}>
          <View style={{ alignItems: 'center', paddingHorizontal: 16 }}>
            <View style={{ width: 72, height: 72, borderRadius: 22, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
              <ZIcon name="bank" size={34} color={c.brand} />
            </View>
            <Text style={{ fontSize: 17, color: c.ink1, fontFamily: font.extrabold, marginTop: 16, textAlign: 'center' }}>
              Get a dedicated account number
            </Text>
            <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular, marginTop: 8, textAlign: 'center', lineHeight: 20 }}>
              Enter your BVN to get a dedicated account for funding by bank transfer. It’s verified
              securely; we never store it.
            </Text>
            {holderName ? (
              <Text style={{ fontSize: 12.5, color: c.ink2, fontFamily: font.semibold, marginTop: 10, textAlign: 'center' }}>
                Opened in your name · {holderName}
              </Text>
            ) : null}
          </View>

          <View style={{ height: 18 }} />
          <Field
            label="Bank Verification Number (BVN)"
            value={bvn}
            onChangeText={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))}
            keyboardType="number-pad"
            placeholder="Enter your 11-digit BVN"
          />
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 8, paddingHorizontal: 2 }}>
            <ZIcon name="lock" size={13} color={c.ink3} />
            <Text style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.regular }}>
              Dial *565*0# on your registered line to get your BVN.
            </Text>
          </View>

          <View style={{ height: 18 }} />
          <Btn
            label={creating ? 'Creating your account…' : 'Get my account'}
            icon="bank"
            disabled={creating || bvn.length !== 11}
            onPress={createAccount}
          />
        </View>
      )}
    </Screen>
  );
};

export default AddMoney;
