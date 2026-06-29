import React, { useCallback } from 'react';
import { View, Text, Pressable, Image } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { LinearGradient } from 'expo-linear-gradient';
import { router, useFocusEffect } from 'expo-router';
import { notify } from '@/components/design/Notify';
import ZIcon from '@/components/design/ZIcon';
import { Screen, TxnRow, money, NText } from '@/components/design/ui';
import { SectionLabel } from '@/components/design/widgets';
import { ConnectedAccounts } from '@/components/design/banklink';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const Wallet = () => {
  const { c } = useTheme();
  const { balance, accountNumber, bankName, txns, showBal, setShowBal, reload, reloadLinked } = useWallet();

  // Keep balance, transactions & linked banks fresh each time the tab opens.
  useFocusEffect(useCallback(() => { reload(); reloadLinked(); }, [reload, reloadLinked]));

  const copyAccount = async () => {
    if (!accountNumber) return;
    await Clipboard.setStringAsync(accountNumber);
    notify('Copied', 'Account number copied to clipboard');
  };

  const hdrBtn = {
    width: 40, height: 40, borderRadius: 13, backgroundColor: c.surface,
    borderWidth: 1, borderColor: c.line, alignItems: 'center', justifyContent: 'center',
  } as const;

  return (
    <Screen pad={false} tab>
      {/* header */}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 18, paddingTop: 6 }}>
        <Text style={{ flex: 1, fontSize: 26, fontFamily: font.extrabold, color: c.ink1 }}>Wallet</Text>
        <Pressable onPress={() => { reload(); reloadLinked(); }} style={hdrBtn}>
          <ZIcon name="refresh" size={19} color={c.ink1} />
        </Pressable>
        <Pressable onPress={() => router.push('/settings')} style={hdrBtn}>
          <ZIcon name="settings" size={19} color={c.ink1} />
        </Pressable>
      </View>

      {/* primary Zitch wallet card — compact */}
      <LinearGradient
        colors={['#23B1A8', '#00847B', '#004D47']}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={{ margin: 16, borderRadius: 22, padding: 16, overflow: 'hidden', shadowColor: '#00847B', shadowOpacity: 0.45, shadowRadius: 20, shadowOffset: { width: 0, height: 14 }, elevation: 6 }}
      >
        <Image source={require('@/assets/images/zitch-mark.png')} style={{ position: 'absolute', right: -18, bottom: -22, width: 132, height: 132, opacity: 0.16 }} resizeMode="contain" />
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text style={{ fontSize: 11, fontFamily: font.bold, letterSpacing: 1.6, color: 'rgba(255,255,255,.82)' }}>ZITCH WALLET</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5, paddingVertical: 3, paddingHorizontal: 9, borderRadius: 999, backgroundColor: 'rgba(255,255,255,.16)' }}>
            <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: c.cyan }} />
            <Text style={{ fontSize: 10, fontFamily: font.bold, color: '#fff' }}>Primary</Text>
          </View>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 8 }}>
          <NText style={{ fontSize: 28, fontFamily: font.extrabold, color: '#fff', fontVariant: ['tabular-nums'] }}>
            {showBal ? money(balance) : '₦ ••••••'}
          </NText>
          <Pressable onPress={() => setShowBal(!showBal)}>
            <ZIcon name={showBal ? 'eye' : 'eyeoff'} size={17} color="rgba(255,255,255,.85)" />
          </Pressable>
        </View>
        {accountNumber ? (
          <Pressable onPress={copyAccount} style={{ flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-start', gap: 8, marginTop: 9, paddingVertical: 7, paddingHorizontal: 12, borderRadius: 999, backgroundColor: 'rgba(255,255,255,.12)' }}>
            <ZIcon name="bank" size={14} color="rgba(255,255,255,.9)" />
            <NText style={{ fontSize: 12.5, fontFamily: font.bold, color: 'rgba(255,255,255,.92)', fontVariant: ['tabular-nums'] }}>
              {accountNumber}{bankName ? ` · ${bankName}` : ''}
            </NText>
            <ZIcon name="copy" size={13} color="rgba(255,255,255,.72)" />
          </Pressable>
        ) : null}
        <View style={{ flexDirection: 'row', gap: 10, marginTop: 14 }}>
          <Pressable onPress={() => router.push('/addmoney')} style={{ flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingVertical: 11, borderRadius: 14, backgroundColor: '#fff' }}>
            <ZIcon name="plus" size={16} color={c.brandDeep} stroke={2.4} />
            <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 14 }}>Add money</Text>
          </Pressable>
          <Pressable onPress={() => router.push('/sendmoney')} style={{ flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingVertical: 11, borderRadius: 14, backgroundColor: 'rgba(255,255,255,.16)', borderWidth: 1, borderColor: 'rgba(255,255,255,.28)' }}>
            <ZIcon name="send" size={15} color="#fff" />
            <Text style={{ color: '#fff', fontFamily: font.bold, fontSize: 14 }}>Send</Text>
          </Pressable>
        </View>
      </LinearGradient>

      {/* connected bank accounts (Mono) */}
      <ConnectedAccounts />

      {/* recent activity */}
      <View style={{ paddingHorizontal: 18, paddingTop: 22 }}>
        <SectionLabel action="See all" onAction={() => router.push('/history')}>Recent activity</SectionLabel>
        {txns.length === 0 ? (
          <Text style={{ color: c.ink3, fontFamily: font.regular, paddingVertical: 8 }}>No transactions yet</Text>
        ) : (
          txns.slice(0, 5).map((x, i) => (
            <TxnRow
              key={x.id}
              txn={x}
              last={i === Math.min(4, txns.length - 1)}
              onPress={() => router.push({ pathname: '/txndetail', params: { type: x.type, amount: String(x.amount), status: x.status, dir: x.dir, detail: x.detail, reference: x.reference, icon: x.icon } })}
            />
          ))
        )}
      </View>
    </Screen>
  );
};

export default Wallet;
