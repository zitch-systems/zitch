import React, { useCallback, useRef, useState } from 'react';
import { View, Text, Pressable, AppState, Platform } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { router, useFocusEffect } from 'expo-router';
import ZIcon from '@/components/design/ZIcon';
import { Sheet, Btn } from '@/components/design/ui';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { copiedAccount, groupPhone, type CopiedAccount } from '@/lib/clipboardAccount';

/**
 * Copied number -> "Transfer to this account?" (review, Nov 2026).
 *
 * In the phone apps Home checks the clipboard by itself: when Home opens and
 * whenever the app comes back to the front while Home is showing. A copied
 * account number is offered as it is; a copied phone number is offered as the
 * account number that phone has at most banks and wallets (without its leading
 * 0). The popup only fills Send money: that page finds the bank and the name,
 * and nothing is paid until the customer confirms them and enters the PIN.
 *
 * Each number is offered once per app session, so dismissing it ends it, and
 * the customer's own Zitch account number (copied to share it) is never offered.
 * In a browser, reading the clipboard asks for permission every time, so there
 * it stays an explicit "Pay from clipboard" tap.
 */
const offered = new Set<string>();

/** Test hook: forget what was offered this session. */
export const resetOfferedClipboardNumbers = () => offered.clear();

export const TransferFromClipboardSheet = ({ found, onClose }: { found: CopiedAccount | null; onClose: () => void }) => {
  const { c } = useTheme();
  const transfer = () => {
    if (!found) return;
    const account = found.account;
    onClose();
    setTimeout(() => router.push({ pathname: '/sendmoney', params: { identifier: account } }), 240);
  };
  return (
    <Sheet open={!!found} onClose={onClose} title="Transfer to this account?">
      {found ? (
        <View>
          <View style={{ alignItems: 'center', paddingVertical: 18, paddingHorizontal: 14, borderRadius: 20, backgroundColor: c.surface2, borderWidth: 1, borderColor: c.line }}>
            <Text style={{ fontSize: 13, fontFamily: font.semibold, color: c.ink3 }}>Account number</Text>
            <Text accessibilityLabel={`Account number ${found.account.split('').join(' ')}`}
              style={{ marginTop: 6, fontSize: 30, fontFamily: font.extrabold, color: c.ink1, letterSpacing: 1, fontVariant: ['tabular-nums'] }}>
              {found.account}
            </Text>
            {found.phone ? (
              <Text style={{ marginTop: 6, fontSize: 12.5, fontFamily: font.medium, color: c.ink3, textAlign: 'center' }}>
                From the phone number {groupPhone(found.phone)}
              </Text>
            ) : null}
          </View>
          <Text style={{ marginTop: 14, fontSize: 13, lineHeight: 19, fontFamily: font.regular, color: c.ink3, textAlign: 'center' }}>
            Check the name and bank on the next page. A completed transfer can’t be reversed.
          </Text>
          <View style={{ gap: 10, marginTop: 18 }}>
            <Btn label="Transfer now" icon="send" onPress={transfer} />
            <Btn label="Ignore" variant="ghost" onPress={onClose} />
          </View>
        </View>
      ) : null}
    </Sheet>
  );
};

const SmartPaste = () => {
  const { c } = useTheme();
  const { accountNumber } = useWallet();
  const [found, setFound] = useState<CopiedAccount | null>(null);
  const own = useRef('');
  own.current = String(accountNumber || '').replace(/\D/g, '');
  const checking = useRef(false);

  const offer = useCallback((text: string, quiet: boolean) => {
    const hit = copiedAccount(text);
    if (!hit) {
      if (!quiet) notify('Nothing to paste', 'Copy a 10-digit account number or a phone number, then try again.');
      return;
    }
    if (hit.account === own.current) {
      if (!quiet) notify('That’s your own account', 'Copy the number you want to send money to.');
      return;
    }
    if (quiet && offered.has(hit.account)) return;
    offered.add(hit.account);
    setFound(hit);
  }, []);

  const check = useCallback(async () => {
    if (checking.current) return;
    checking.current = true;
    try {
      if (!(await Clipboard.hasStringAsync())) return;
      offer((await Clipboard.getStringAsync()) || '', true);
    } catch {
      // A clipboard the system will not share is the same as an empty one.
    } finally {
      checking.current = false;
    }
  }, [offer]);

  useFocusEffect(useCallback(() => {
    if (Platform.OS === 'web') return undefined;
    void check();
    const sub = AppState.addEventListener('change', (state) => { if (state === 'active') void check(); });
    return () => sub.remove();
  }, [check]));

  const readOnTap = async () => {
    try {
      offer((await Clipboard.getStringAsync()) || '', false);
    } catch {
      notify('Clipboard unavailable', 'Zitch could not read the clipboard. You can enter the number manually.');
    }
  };

  return (
    <>
      {Platform.OS === 'web' ? (
        <Pressable
          onPress={readOnTap}
          accessibilityRole="button"
          accessibilityLabel="Paste an account or phone number"
          accessibilityHint="Reads your clipboard only after you tap"
          style={({ pressed }) => ({
            flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 18, padding: 14, borderRadius: 16,
            backgroundColor: c.surface, borderWidth: 1, borderColor: c.line, opacity: pressed ? 0.82 : 1,
          })}
        >
          <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: c.surface3, alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="copy" size={20} color={c.brand} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>Pay from clipboard</Text>
            <Text style={{ fontSize: 12.5, fontFamily: font.regular, color: c.ink3, marginTop: 2 }}>A copied account or phone number</Text>
          </View>
          <ZIcon name="right" size={18} color={c.ink3} />
        </Pressable>
      ) : null}
      <TransferFromClipboardSheet found={found} onClose={() => setFound(null)} />
    </>
  );
};

export default SmartPaste;
