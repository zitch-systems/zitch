import React, { useState } from 'react';
import { View, Text, Pressable } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { router } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Sheet, Btn } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';

/**
 * Explicit paste-to-pay. The clipboard is read only after the user taps the
 * control, avoiding silent ingestion of values copied from another finance app.
 */
const SmartPaste = () => {
  const { c } = useTheme();
  const [num, setNum] = useState('');
  const [open, setOpen] = useState(false);

  const readClipboard = async () => {
    try {
      const text = (await Clipboard.getStringAsync()) || '';
      const digits = text.replace(/[\s-]/g, '');
      const match = digits.match(/^\+?(\d{10,11})$/);
      if (!match) {
        notify('Nothing to paste', 'Copy a 10-digit account number or an 11-digit phone number, then try again.');
        return;
      }
      setNum(match[1]);
      setOpen(true);
    } catch {
      notify('Clipboard unavailable', 'Zitch could not read the clipboard. You can enter the number manually.');
    }
  };

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
    <>
      <Pressable
        onPress={readClipboard}
        accessibilityRole="button"
        accessibilityLabel="Paste an account or phone number"
        accessibilityHint="Reads your clipboard only after you tap"
        style={({ pressed }) => ({
          flexDirection: 'row',
          alignItems: 'center',
          gap: 12,
          marginTop: 18,
          padding: 14,
          borderRadius: 16,
          backgroundColor: c.surface,
          borderWidth: 1,
          borderColor: c.line,
          opacity: pressed ? 0.82 : 1,
        })}
      >
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: c.surface3, alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name="copy" size={20} color={c.brand} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>Pay from clipboard</Text>
          <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: c.ink3, marginTop: 2 }}>
            Nothing is read until you tap
          </Text>
        </View>
        <ZIcon name="right" size={18} color={c.ink3} />
      </Pressable>

      <Sheet open={open} onClose={close}>
        <View style={{ alignItems: 'center', gap: 10, marginBottom: 4 }}>
          <View style={{ width: 52, height: 52, borderRadius: 16, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="copy" size={24} color={c.brand} />
          </View>
          <Text style={{ fontSize: 17, fontFamily: font.bold, color: c.ink1 }}>
            {isPhone ? 'Phone number detected' : 'Account number detected'}
          </Text>
          <Text style={{ fontSize: 26, fontFamily: font.extrabold, color: c.ink1, letterSpacing: 1, fontVariant: ['tabular-nums'] }}>
            {formatted}
          </Text>
          <Text style={{ fontSize: 13, color: c.ink3, textAlign: 'center', maxWidth: 280, fontFamily: font.regular }}>
            Confirm what you want to do with this number.
          </Text>
        </View>
        <View style={{ gap: 10, marginTop: 18 }}>
          <Btn label="Send money" icon="send" onPress={sendMoney} />
          {isPhone && <Btn label="Buy airtime" variant="ghost" onPress={buyAirtime} />}
          <Btn label="Not now" variant="outline" onPress={close} />
        </View>
      </Sheet>
    </>
  );
};

export default SmartPaste;

