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

type DediAccount = { account_number: string; account_name: string; bank_name: string };

// Funding is bank-transfer only: the user transfers to their dedicated Zitch
// (Monnify reserved) account and the wallet is credited automatically by the
// webhook — no card checkout. The account is minted through Monnify's own
// onboarding: the user enters their BVN here and Monnify verifies it and issues
// the NUBAN (no separate in-app KYC step needed first).
const AddMoney = () => {
  const { c } = useTheme();
  const [loading, setLoading] = useState(true);
  const [account, setAccount] = useState<DediAccount | null>(null);
  const [bvn, setBvn] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    let alive = true;
    // Never let a slow/hanging backend (e.g. a slow Monnify call) leave the page
    // stuck on the spinner: show the screen within a few seconds no matter what.
    // If the account lookup resolves later, it still fills in (account state).
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

  const createAccount = async () => {
    if (bvn.length !== 11) return;
    setCreating(true);
    try {
      const r = await apiJson('/api/wallet/account/create/', { bvn });
      if (r?.success && r.account_number) {
        setAccount(r as DediAccount);
      } else {
        notify('Error', r?.message || "We couldn't create your account. Please try again.");
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setCreating(false);
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

      {account ? (
        <>
          <Label>Fund by bank transfer</Label>
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
              card needed. It's verified securely; we never store it.
            </Text>
          </View>

          <View style={{ height: 22 }} />
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

          <View style={{ height: 22 }} />
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
