import React, { useState } from 'react';
import { Pressable, Text } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import ZIcon from '@/components/design/ZIcon';
import { notify } from '@/components/design/Notify';
import { extractCode } from '@/lib/code';
import { useTheme, font } from '@/lib/theme';

/**
 * "Paste" for a one-time code: reads the clipboard only when tapped and fills
 * the code if the copied text holds one (the whole SMS is fine). Sits beside
 * every code field, so a code copied from Messages or email goes in with one tap.
 */
export const PasteCode = ({ onCode, length = 6, disabled = false }: {
  onCode: (code: string) => void;
  length?: number;
  disabled?: boolean;
}) => {
  const { c } = useTheme();
  const [busy, setBusy] = useState(false);
  const paste = async () => {
    if (busy || disabled) return;
    setBusy(true);
    try {
      const code = extractCode(await Clipboard.getStringAsync(), length);
      if (code.length === length) onCode(code);
      else notify('No code to paste', `Copy the ${length}-digit code from your message, then tap Paste.`, 'info');
    } catch {
      notify('Could not paste', 'Type the code instead.', 'info');
    } finally {
      setBusy(false);
    }
  };
  return (
    <Pressable
      onPress={() => void paste()}
      disabled={disabled || busy}
      accessibilityRole="button"
      accessibilityLabel="Paste code"
      accessibilityState={{ disabled: disabled || busy }}
      hitSlop={8}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999,
        backgroundColor: 'rgba(6,110,102,0.10)', opacity: disabled ? 0.5 : 1 }}
    >
      <ZIcon name="copy" size={13} color={c.brand} />
      <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.brand }}>Paste</Text>
    </Pressable>
  );
};
