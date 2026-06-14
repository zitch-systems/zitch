import React, { useState } from 'react';
import { View, Text, Share, Alert } from 'react-native';
import { router } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { Screen, Header, Card, Field, Btn, money, Naira } from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

// Build a shareable payment-request link. (Points at a Zitch deep link/landing;
// the recipient opens it to pay the requester from their own wallet.) Query is
// assembled manually — RN's URLSearchParams is incomplete/unreliable.
const buildLink = (name: string, amount: number, note: string) => {
  const parts: string[] = [];
  if (name) parts.push(`to=${encodeURIComponent(name)}`);
  if (amount > 0) parts.push(`amount=${amount}`);
  if (note) parts.push(`note=${encodeURIComponent(note)}`);
  return `https://zitch.ng/pay${parts.length ? `?${parts.join('&')}` : ''}`;
};

const BizPayment = () => {
  const { c } = useTheme();
  const { firstName } = useWallet();
  const [amt, setAmt] = useState('');
  const [note, setNote] = useState('');
  const [copied, setCopied] = useState(false);

  const amount = Number(amt || 0);
  const link = buildLink(firstName, amount, note);
  const message =
    `${firstName || 'A Zitch user'} is requesting${amount > 0 ? ` ${money(amount)}` : ' a payment'}` +
    `${note ? ` for "${note}"` : ''}. Pay securely on Zitch: ${link}`;

  const copy = async () => {
    await Clipboard.setStringAsync(link);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  const share = async () => {
    try {
      await Share.share({ message });
    } catch {
      Alert.alert('Error', 'Could not open the share sheet.');
    }
  };

  return (
    <Screen>
      <Header title="Receive Payment" sub="For your business" onBack={() => router.back()} />

      <Card style={{ alignItems: 'center', paddingVertical: 22 }}>
        <View style={{ width: 54, height: 54, borderRadius: 17, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="bank" size={28} color={c.brand} />
        </View>
        <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1, marginTop: 12 }}>Get paid by anyone</Text>
        <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 4, textAlign: 'center', maxWidth: 260, fontFamily: font.regular }}>
          Create a payment request and share it with your customer. They pay you in one tap.
        </Text>
      </Card>

      <View style={{ height: 16 }} />
      <Field
        label="Amount (optional)"
        value={amt}
        onChangeText={(v) => setAmt(v.replace(/\D/g, ''))}
        keyboardType="number-pad"
        placeholder="0.00"
        prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />}
      />
      <View style={{ height: 14 }} />
      <Field
        label="What's it for? (optional)"
        value={note}
        onChangeText={(v) => setNote(v.slice(0, 60))}
        placeholder="e.g. Hair appointment"
      />

      <View style={{ height: 18 }} />
      <Card style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }} onPress={copy}>
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.regular }}>Your payment link</Text>
          <Text numberOfLines={1} style={{ fontSize: 13.5, color: c.ink1, fontFamily: font.semibold, marginTop: 2 }}>{link}</Text>
        </View>
        <ZIcon name={copied ? 'check' : 'copy'} size={18} color={c.brand} />
      </Card>

      <View style={{ height: 18 }} />
      <Btn label="Share payment request" icon="share" onPress={share} />
    </Screen>
  );
};

export default BizPayment;
