import React, { useEffect, useState } from 'react';
import { View, Text, Modal, Pressable } from 'react-native';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

/**
 * Branded success/error/info popup — a nicer replacement for the OS `Alert.alert`
 * on simple (no-button) notifications. Imperative API so it's a near drop-in:
 *
 *   notify('Success', 'BVN verified');   // kind inferred from the title
 *   notifyError('Could not start payment');
 *
 * Mount <NotifyHost/> once at the app root. Confirmation dialogs that need
 * action buttons should keep using Alert.alert (this is for one-shot messages).
 */
type Kind = 'success' | 'error' | 'info';
type Item = { title: string; message?: string; kind: Kind };

let _emit: ((i: Item) => void) | null = null;

const inferKind = (title: string): Kind =>
  /error|fail|wrong|invalid|unable|could ?n.?t|denied/i.test(title) ? 'error'
    : /success|done|sent|verified|updated|complete|saved|added/i.test(title) ? 'success'
      : 'info';

export function notify(title: string, message?: string, kind?: Kind): void {
  _emit?.({ title, message, kind: kind ?? inferKind(title) });
}
export const notifySuccess = (title: string, message?: string) => notify(title, message, 'success');
export const notifyError = (title: string, message?: string) => notify(title, message, 'error');

const STYLE: Record<Kind, { icon: string; color: string; tint: string }> = {
  success: { icon: 'check', color: '#0B7A43', tint: 'rgba(11,122,67,.12)' },
  error: { icon: 'x', color: '#C42B2B', tint: 'rgba(196,43,43,.12)' },
  info: { icon: 'bell', color: '#0FA295', tint: 'rgba(15,162,149,.12)' },
};

export const NotifyHost = () => {
  const { c } = useTheme();
  const [item, setItem] = useState<Item | null>(null);

  useEffect(() => {
    _emit = setItem;
    return () => { _emit = null; };
  }, []);

  if (!item) return null;
  const s = STYLE[item.kind];
  const close = () => setItem(null);

  return (
    <Modal transparent animationType="fade" visible onRequestClose={close}>
      <Pressable
        onPress={close}
        style={{ flex: 1, backgroundColor: 'rgba(2,16,14,.5)', alignItems: 'center', justifyContent: 'center', paddingHorizontal: 28 }}
      >
        {/* absorbs taps so pressing the card doesn't dismiss; backdrop tap does */}
        <Pressable
          onPress={() => {}}
          style={{ width: '100%', maxWidth: 360, backgroundColor: c.surface, borderRadius: 22, padding: 24, alignItems: 'center' }}
        >
          <View style={{ width: 58, height: 58, borderRadius: 29, backgroundColor: s.tint, alignItems: 'center', justifyContent: 'center', marginBottom: 14 }}>
            <ZIcon name={s.icon} size={28} color={s.color} stroke={2.4} />
          </View>
          <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1, textAlign: 'center' }}>{item.title}</Text>
          {item.message ? (
            <Text style={{ fontSize: 14, color: c.ink3, textAlign: 'center', marginTop: 6, lineHeight: 20, fontFamily: font.regular }}>
              {item.message}
            </Text>
          ) : null}
          <Pressable onPress={close} style={{ marginTop: 22, alignSelf: 'stretch', height: 50, borderRadius: 14, backgroundColor: c.brand, alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ color: '#fff', fontSize: 15, fontFamily: font.bold }}>OK</Text>
          </Pressable>
        </Pressable>
      </Pressable>
    </Modal>
  );
};
