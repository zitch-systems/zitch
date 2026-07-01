import React, { useEffect, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import * as WebBrowser from 'expo-web-browser';
import { router } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { apiJson } from '@/lib/api';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Btn, Field, Naira } from '@/components/design/ui';
import { Label } from '@/components/design/flowkit';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

type DediAccount = { account_number: string; account_name: string; bank_name: string };

// Two ways to fund:
//  1. Instant checkout (Kora hosted card/bank page) — works without a dedicated
//     account, credited by the pay-in webhook / verify. Always available.
//  2. A dedicated Zitch (Kora reserved) account for bank transfers — minted via
//     Kora's reserved-account onboarding (enter BVN; Kora verifies and issues the
//     NUBAN). Requires the Virtual Bank Account product to be enabled on the Kora
//     merchant account; until then, use instant checkout above.
const AddMoney = () => {
  const { c } = useTheme();
  const { reload } = useWallet();
  const [loading, setLoading] = useState(true);
  const [account, setAccount] = useState<DediAccount | null>(null);
  const [bvn, setBvn] = useState('');
  const [creating, setCreating] = useState(false);

  // Wema/ALAT flow: account creation is a BVN + OTP round-trip. When the backend
  // answers otp_required, we hold the tracking id and show the OTP step.
  const [otpFlow, setOtpFlow] = useState<{ trackingId: string; destination: string } | null>(null);
  const [otp, setOtp] = useState('');
  const [verifying, setVerifying] = useState(false);

  // Instant-checkout funding
  const [fundAmt, setFundAmt] = useState('');
  const [funding, setFunding] = useState(false);

  useEffect(() => {
    let alive = true;
    // Never let a slow/hanging backend leave the page stuck on the spinner: show
    // the screen within a few seconds no matter what. If the account lookup
    // resolves later, it still fills in (account state).
    const guard = setTimeout(() => { if (alive) setLoading(false); }, 8000);
    apiJson('/api/wallet/account/')
      .then((r) => { if (alive && r?.success && r.account_number) setAccount(r as DediAccount); })
      .catch(() => {})
      .finally(() => { if (alive) { clearTimeout(guard); setLoading(false); } });
    return () => { alive = false; clearTimeout(guard); };
  }, []);

  const copyAccount = async () => {
    if (!account) return;
    await Clipboard.setStringAsync(account.account_number);
    notify('Copied', 'Account number copied to clipboard');
  };

  // Instant funding: open Kora's hosted checkout, then confirm + refresh. The
  // pay-in webhook also credits idempotently, so verify is best-effort.
  const fundNow = async () => {
    const amt = Number(fundAmt);
    if (!Number.isFinite(amt) || amt < 100) { notify('Error', 'Minimum funding amount is ₦100'); return; }
    setFunding(true);
    try {
      const r = await apiJson<{ success?: boolean; reference?: string; authorization_url?: string; mock?: boolean; message?: string }>(
        '/api/fund/initialize/', { amount: String(amt) });
      if (!r?.success || !r.authorization_url) {
        notify('Error', r?.message || "Couldn't start payment. Please try again.");
        return;
      }
      if (r.mock || !/^https?:/.test(r.authorization_url)) {
        notify('Test mode', 'Funding is in test mode — no real charge was made.');
        return;
      }
      await WebBrowser.openBrowserAsync(r.authorization_url);
      // Back from checkout: confirm with the rail (idempotent) then refresh.
      if (r.reference) { try { await apiJson('/api/fund/verify/', { reference: r.reference }); } catch { /* webhook still credits */ } }
      await reload();
      setFundAmt('');
      notify('Funding', 'If your payment went through, your wallet has been credited.');
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setFunding(false);
    }
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
        otp, tracking_id: otpFlow.trackingId, using_bvn: true, bvn,
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
        tracking_id: otpFlow.trackingId, using_bvn: true,
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

      {/* 1) Instant funding via Kora hosted checkout (card / bank) */}
      <Label>Fund instantly</Label>
      <View style={{ backgroundColor: c.surface, borderRadius: 18, borderWidth: 1, borderColor: c.line, padding: 18 }}>
        <Text style={{ fontSize: 13, color: c.ink3, fontFamily: font.regular, marginBottom: 14 }}>
          Pay with your debit card or bank on Kora's secure checkout — your wallet is credited automatically.
        </Text>
        <Field
          value={fundAmt}
          onChangeText={(v) => setFundAmt(v.replace(/\D/g, ''))}
          keyboardType="number-pad"
          placeholder="Enter amount"
          prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />}
        />
        <View style={{ height: 14 }} />
        <Btn
          label={funding ? 'Starting checkout…' : 'Fund now'}
          icon="card"
          disabled={funding || Number(fundAmt) < 100}
          onPress={fundNow}
        />
      </View>

      <View style={{ height: 26 }} />

      {/* 2) Dedicated account for bank transfers (needs Kora VBA enabled) */}
      <Label>Or use a dedicated account</Label>
      {account ? (
        <>
          <View style={{ backgroundColor: c.surface, borderRadius: 18, borderWidth: 1, borderColor: c.line, padding: 18 }}>
            <Text style={{ fontSize: 13, color: c.ink3, fontFamily: font.regular }}>
              Transfer any amount to this account from any bank app — your Zitch wallet is credited
              automatically, usually within seconds.
            </Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 16 }}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={{ fontSize: 26, color: c.ink1, fontFamily: font.extrabold, letterSpacing: 1.5 }}>
                  {account.account_number}
                </Text>
                <Text style={{ fontSize: 13.5, color: c.ink2, fontFamily: font.medium, marginTop: 4 }}>
                  {account.bank_name}{account.account_name ? ` · ${account.account_name}` : ''}
                </Text>
              </View>
              <Pressable
                onPress={copyAccount}
                hitSlop={10}
                style={{ flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: 'rgba(15,162,149,.12)', borderRadius: 12, paddingVertical: 10, paddingHorizontal: 14 }}
              >
                <ZIcon name="copy" size={15} color={c.brand} />
                <Text style={{ fontSize: 13.5, color: c.brand, fontFamily: font.bold }}>Copy</Text>
              </Pressable>
            </View>
          </View>

          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 9, marginTop: 18, paddingHorizontal: 4 }}>
            <ZIcon name="check" size={16} color={c.lime} stroke={2.6} />
            <Text style={{ flex: 1, fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>
              Save this account — it's permanently yours. Transfers reflect automatically, no need to confirm anything here.
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
              Enter your BVN to get a dedicated account for funding by bank transfer. It's verified
              securely; we never store it.
            </Text>
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
