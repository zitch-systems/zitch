import React, { useEffect, useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { router } from 'expo-router';
import { notify } from '@/components/design/Notify';
import { apiJson } from '@/lib/api';
import { Loading } from '@/components/design/Loading';
import { Screen, Header, Btn } from '@/components/design/ui';
import { Label } from '@/components/design/flowkit';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

type DediAccount = { account_number: string; account_name: string; bank_name: string };

// Funding is bank-transfer only: the user transfers to their dedicated Zitch
// (Monnify reserved) account and the wallet is credited automatically by the
// webhook — no card checkout. The account only exists once KYC (BVN/NIN) is
// done, so until then we point the user to verification.
const AddMoney = () => {
  const { c } = useTheme();
  const [loading, setLoading] = useState(true);
  const [account, setAccount] = useState<DediAccount | null>(null);

  useEffect(() => {
    apiJson('/api/wallet/account/')
      .then((r) => { if (r?.success && r.account_number) setAccount(r as DediAccount); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const copyAccount = async () => {
    if (!account) return;
    await Clipboard.setStringAsync(account.account_number);
    notify('Copied', 'Account number copied to clipboard');
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
        <View style={{ alignItems: 'center', paddingTop: 40, paddingHorizontal: 16 }}>
          <View style={{ width: 84, height: 84, borderRadius: 26, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="bank" size={40} color={c.brand} />
          </View>
          <Text style={{ fontSize: 19, color: c.ink1, fontFamily: font.extrabold, marginTop: 22, textAlign: 'center' }}>
            Get your Zitch account number
          </Text>
          <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular, marginTop: 10, textAlign: 'center', lineHeight: 21 }}>
            Verify your BVN or NIN to receive a dedicated account you can fund by bank transfer from
            any bank — instantly, with no card needed. If you've just verified, it may take a moment
            to appear.
          </Text>
          <View style={{ height: 28 }} />
          <Btn label="Verify my identity" icon="lock" onPress={() => router.push('/kyc')} />
        </View>
      )}
    </Screen>
  );
};

export default AddMoney;
