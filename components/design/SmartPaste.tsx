import React, { useEffect, useState } from 'react';
import { View, Text } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Sheet, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

const SEEN_KEY_PREFIX = 'zpaste:';

/**
 * Smart paste-to-pay. On mount (app/home entry) reads the clipboard once; if it
 * holds a Nigerian phone (11 digits) or account number (10 digits), offers to
 * send money or buy airtime, routing into the matching flow prefilled.
 * Per the design, a detected phone has its leading 0 stripped when transferring.
 * Each copied number is offered only once (tracked in AsyncStorage) so the sheet
 * doesn't re-pop on every home visit.
 */
const SmartPaste = () => {
  const { c } = useTheme();
  const [num, setNum] = useState('');
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const text = (await Clipboard.getStringAsync()) || '';
        const digits = text.replace(/[\s-]/g, '');
        const m = digits.match(/^\+?(\d{10,11})$/);
        if (!active || !m) return;
        // Offer each number once — skip if we've already prompted for it.
        const key = SEEN_KEY_PREFIX + m[1];
        if (await AsyncStorage.getItem(key)) return;
        await AsyncStorage.setItem(key, '1');
        if (!active) return;
        setNum(m[1]);
        setOpen(true);
      } catch {
        // clipboard unavailable — silently skip
      }
    })();
    return () => { active = false; };
  }, []);

  const isPhone = num.length >= 11;
  const formatted = isPhone
    ? num.replace(/(\d{4})(\d{3})(\d{4})/, '$1 $2 $3')
    : num.replace(/(\d{3})(\d{3})(\d{4})/, '$1 $2 $3');

  const close = () => setOpen(false);

  const sendMoney = () => {
    close();
    const identifier = isPhone ? num.replace(/^0/, '') : num;
    setTimeout(() => router.push({ pathname: '/sendmoney', params: { identifier } }), 240);
  };

  const buyAirtime = () => {
    close();
    setTimeout(() => router.push({ pathname: '/buyairtime', params: { phone: num } }), 240);
  };

  return (
    <Sheet open={open} onClose={close}>
      <View style={{ alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <View style={{ width: 52, height: 52, borderRadius: 16, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="copy" size={24} color={c.brand} />
        </View>
        <Text style={{ fontSize: 17, fontFamily: font.bold, color: c.ink1 }}>
          {isPhone ? 'Phone number detected' : 'Account number detected'}
        </Text>
        <Text style={{ fontSize: 26, fontFamily: font.extrabold, color: c.ink1, letterSpacing: 1, fontVariant: ['tabular-nums'] }}>{formatted}</Text>
        <Text style={{ fontSize: 13, color: c.ink3, textAlign: 'center', maxWidth: 280, fontFamily: font.regular }}>
          We noticed you copied this number. What would you like to do?
        </Text>
      </View>
      <View style={{ gap: 10, marginTop: 18 }}>
        <Btn label="Send money" icon="send" onPress={sendMoney} />
        {isPhone && <Btn label="Buy airtime" variant="ghost" onPress={buyAirtime} />}
        <Btn label="Not now" variant="outline" onPress={close} />
      </View>
    </Sheet>
  );
};

export default SmartPaste;
