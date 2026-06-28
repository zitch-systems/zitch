import React, { useCallback, useState } from 'react';
import { View, Text, Pressable, Image } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { router, useFocusEffect } from 'expo-router';
import { LinearGradient } from 'expo-linear-gradient';
import ZIcon from '@/components/design/ZIcon';
import { Screen, TxnRow, money, NText } from '@/components/design/ui';
import { SectionLabel } from '@/components/design/widgets';
import { ConnectedAccounts } from '@/components/design/ConnectedAccounts';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const Wallet = () => {
  const { c } = useTheme();
  const { balance, fullName, firstName, accountNumber, bankName, txns, showBal, setShowBal, reload } = useWallet();
  const [copied, setCopied] = useState(false);

  // Keep balance & transactions fresh each time the tab is opened.
  useFocusEffect(useCallback(() => { reload(); }, [reload]));

  const moneyIn = txns.filter((t) => t.dir === 'in').reduce((s, t) => s + Math.abs(t.amount), 0);
  const moneyOut = txns.filter((t) => t.dir === 'out').reduce((s, t) => s + Math.abs(t.amount), 0);

  // Copy ONLY the bare account number (not the "· bank" suffix); pop a brief
  // local confirmation bubble above the chip, matching Home's pattern.
  const copyAccount = async () => {
    if (!accountNumber) return;
    await Clipboard.setStringAsync(accountNumber);
    setCopied(true);
    setTimeout(() => setCopied(false), 1300);
  };

  // NUBAN account numbers display grouped 4-3-3 ("9012 345 678").
  const groupedAccount = accountNumber.replace(/^(\d{4})(\d{3})(\d{3}).*$/, '$1 $2 $3');

  return (
    <Screen pad={false} tab>
      <Text style={{ paddingHorizontal: 20, paddingTop: 6, fontSize: 26, fontFamily: font.extrabold, color: c.ink1 }}>Wallet</Text>

      {/* Primary Zitch wallet card — uses its own distinct wallet gradient (NOT the
          shared Hero/heroGradient) per the design hand-off. */}
      <LinearGradient
        colors={['#23B1A8', '#00847B', '#004D47']}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={{ margin: 16, borderRadius: 22, padding: 18, overflow: 'hidden', shadowColor: '#004D47', shadowOpacity: 0.5, shadowRadius: 22, shadowOffset: { width: 0, height: 16 }, elevation: 6 }}
      >
        <Image
          source={require('@/assets/images/zitch-mark.png')}
          style={{ position: 'absolute', right: -18, bottom: -22, width: 140, height: 140, opacity: 0.18 }}
          resizeMode="contain"
        />
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text style={{ fontSize: 11, color: 'rgba(255,255,255,.82)', fontFamily: font.bold, letterSpacing: 1.5 }}>ZITCH WALLET</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5, paddingVertical: 3, paddingHorizontal: 9, borderRadius: 999, backgroundColor: 'rgba(255,255,255,.16)' }}>
            <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: c.cyan }} />
            <Text style={{ fontSize: 10, color: '#fff', fontFamily: font.bold }}>Primary</Text>
          </View>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 8 }}>
          <NText style={{ fontSize: 27, fontFamily: font.extrabold, color: '#fff', fontVariant: ['tabular-nums'] }}>
            {showBal ? money(balance) : '₦ ••••••'}
          </NText>
          <Pressable onPress={() => setShowBal(!showBal)} hitSlop={8}>
            <ZIcon name={showBal ? 'eye' : 'eyeoff'} size={17} color="rgba(255,255,255,.85)" />
          </Pressable>
        </View>

        {/* Two-line account chip — name over "{grouped number} · {bank}", tap to copy. */}
        {accountNumber ? (
          <View style={{ marginTop: 10, alignSelf: 'flex-start' }}>
            {copied && (
              <View style={{ position: 'absolute', bottom: '100%', left: 0, marginBottom: 7, flexDirection: 'row', alignItems: 'center', gap: 5, paddingVertical: 5, paddingHorizontal: 10, borderRadius: 999, backgroundColor: c.ink1 }}>
                <ZIcon name="check" size={12} color={c.cyan} stroke={2.6} />
                <Text style={{ color: '#fff', fontSize: 11.5, fontFamily: font.semibold }}>Account number copied</Text>
              </View>
            )}
            <Pressable
              onPress={copyAccount}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 7, paddingHorizontal: 12, borderRadius: 16, backgroundColor: 'rgba(255,255,255,.16)' }}
            >
              <View>
                <Text numberOfLines={1} style={{ color: '#fff', fontSize: 12.5, fontFamily: font.bold }}>
                  {fullName || firstName || 'Your account'}
                </Text>
                <NText numberOfLines={1} style={{ color: 'rgba(255,255,255,.82)', fontSize: 11.5, fontFamily: font.medium, marginTop: 1 }}>
                  {groupedAccount}{bankName ? ` · ${bankName}` : ''}
                </NText>
              </View>
              <ZIcon name="copy" size={15} color="rgba(255,255,255,.85)" />
            </Pressable>
          </View>
        ) : null}

        <View style={{ flexDirection: 'row', gap: 10, marginTop: 14 }}>
          <Pressable onPress={() => router.push('/addmoney')} style={{ flex: 1, paddingVertical: 12, borderRadius: 14, backgroundColor: '#fff', alignItems: 'center' }}>
            <Text style={{ color: c.brandDeep, fontFamily: font.bold, fontSize: 14 }}>+ Add money</Text>
          </Pressable>
          <Pressable onPress={() => router.push('/sendmoney')} style={{ flex: 1, paddingVertical: 12, borderRadius: 14, backgroundColor: 'rgba(255,255,255,.18)', borderWidth: 1, borderColor: 'rgba(255,255,255,.25)', alignItems: 'center' }}>
            <Text style={{ color: '#fff', fontFamily: font.bold, fontSize: 14 }}>Send</Text>
          </Pressable>
        </View>
      </LinearGradient>

      <View style={{ flexDirection: 'row', gap: 12, marginHorizontal: 16 }}>
        {[
          { k: 'Money in', v: moneyIn, color: c.lime, sign: '+' },
          { k: 'Money out', v: moneyOut, color: c.ink1, sign: '-' },
        ].map((s) => (
          <View key={s.k} style={{ flex: 1, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, padding: 16 }}>
            <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>{s.k}</Text>
            <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: s.color, marginTop: 4, fontVariant: ['tabular-nums'] }}>
              {s.sign}{money(s.v)}
            </Text>
          </View>
        ))}
      </View>

      {/* Connected external bank accounts (Mono open-banking) */}
      <ConnectedAccounts />

      <View style={{ paddingHorizontal: 18, paddingTop: 22 }}>
        <SectionLabel action="Filter">Recent activity</SectionLabel>
        {txns.length === 0 ? (
          <Text style={{ color: c.ink3, fontFamily: font.regular, paddingVertical: 8 }}>No transactions yet</Text>
        ) : (
          txns.map((x, i) => (
            <TxnRow
              key={x.id}
              txn={x}
              last={i === txns.length - 1}
              onPress={() => router.push({ pathname: '/txndetail', params: { type: x.type, amount: String(x.amount), status: x.status, dir: x.dir, detail: x.detail, reference: x.reference, icon: x.icon } })}
            />
          ))
        )}
      </View>
    </Screen>
  );
};

export default Wallet;
