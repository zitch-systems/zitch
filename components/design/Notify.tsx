import React, { useEffect, useState } from 'react';
import { View, Text, Modal, Pressable } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

/**
 * Branded success/error/info popup — a nicer replacement for the OS `Alert.alert`
 * on simple (no-button) notifications. Imperative API so it's a near drop-in:
 *
 *   notify('Success', 'BVN verified');   // kind inferred from the title
 *   notifyError('Could not start payment');
 *   flash('Saved', 'Image saved to your device');  // clears itself, no OK button
 *
 * Mount <NotifyHost/> once at the app root. Confirmation dialogs that need
 * action buttons should keep using Alert.alert (this is for one-shot messages).
 */
type Kind = 'success' | 'error' | 'info';
type Item = { title: string; message?: string; kind: Kind; dismissMs?: number };

/** How long a self-clearing popup stays up: long enough to read, short enough
 *  that it never feels like something waiting on you. */
export const FLASH_MS = 1600;

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

/**
 * A popup that clears itself. For confirmations that carry no decision — "saved",
 * "shared", "copied" — where an OK button is one tap of pure ceremony standing
 * between the user and the thing they already did.
 */
export function flash(title: string, message?: string, kind?: Kind): void {
  _emit?.({ title, message, kind: kind ?? inferKind(title), dismissMs: FLASH_MS });
}

export const NotifyHost = () => {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const [item, setItem] = useState<Item | null>(null);

  useEffect(() => {
    _emit = setItem;
    return () => { _emit = null; };
  }, []);

  // Self-clearing popups. Keyed on the item object, so a second flash arriving
  // while the first is still up restarts the clock instead of inheriting the
  // remainder of the old one and vanishing early.
  useEffect(() => {
    if (!item?.dismissMs) return;
    const t = setTimeout(() => setItem((cur) => (cur === item ? null : cur)), item.dismissMs);
    return () => clearTimeout(t);
  }, [item]);

  // Icon colours come from the theme tokens (bright in both light AND dark) so
  // the success/error mark never goes muddy on the near-black dark surface —
  // the previous hardcoded dark-green/red lost contrast in dark mode.
  const STYLE: Record<Kind, { icon: string; color: string; tint: string }> = {
    success: { icon: 'check', color: c.lime, tint: 'rgba(0,181,29,.14)' },
    error: { icon: 'x', color: c.red, tint: 'rgba(255,59,59,.14)' },
    info: { icon: 'bell', color: c.brand, tint: 'rgba(15,162,149,.14)' },
  };

  if (!item) return null;
  const s = STYLE[item.kind];
  const close = () => setItem(null);

  return (
    <Modal transparent animationType="slide" visible onRequestClose={close}>
      {/* Bottom sheet, not a centered card: rounded top corners only, a drag
          handle, and content anchored to the bottom edge — the same shape as
          every other sheet in the app (see `Sheet` in ui.tsx). A dead-center
          floating card is what used to make this one popup feel like a
          different, older-looking component from the rest of the app. */}
      <Pressable
        onPress={close}
        accessible={false}
        style={{ flex: 1, backgroundColor: 'rgba(2,16,14,.5)', justifyContent: 'flex-end' }}
      >
        {/* absorbs taps so pressing the sheet doesn't dismiss; backdrop tap does */}
        <Pressable
          onPress={() => {}}
          accessible={false}
          style={{
            backgroundColor: c.surface,
            borderTopLeftRadius: 28,
            borderTopRightRadius: 28,
            paddingHorizontal: 28,
            paddingTop: 10,
            paddingBottom: 28 + insets.bottom,
            alignItems: 'center',
          }}
        >
          <View style={{ width: 40, height: 5, borderRadius: 3, backgroundColor: c.line, alignSelf: 'center', marginBottom: 22 }} />
          {/* A two-tone "halo" — a soft outer ring behind a solid inner circle —
              instead of one flat tint. The extra layer is what reads as polished
              rather than a plain colored dot around the glyph. */}
          <View style={{ width: 76, height: 76, borderRadius: 38, backgroundColor: s.tint, alignItems: 'center', justifyContent: 'center', marginBottom: 18 }}>
            <View style={{ width: 54, height: 54, borderRadius: 27, backgroundColor: s.color, alignItems: 'center', justifyContent: 'center' }}>
              {/* Always white, not c.inkOnBrand: that token is calibrated against
                  the brand teal, and would go dark-on-bright-green in dark mode —
                  inconsistent with the white-on-green light mode reads. Every
                  s.color (lime/red/brand) is vivid enough for white to stay legible. */}
              <ZIcon name={s.icon} size={26} color="#fff" stroke={2.8} />
            </View>
          </View>
          <Text style={{ fontSize: 19, fontFamily: font.extrabold, color: c.ink1, textAlign: 'center' }}>{item.title}</Text>
          {item.message ? (
            <Text style={{ fontSize: 14, color: c.ink3, textAlign: 'center', marginTop: 7, lineHeight: 20, fontFamily: font.regular }}>
              {item.message}
            </Text>
          ) : null}
          {/* No button on a self-clearing popup — there is nothing to acknowledge,
              and an OK that outlives the message it confirms is just a dead tap. */}
          {item.dismissMs ? null : (
            <Pressable onPress={close} accessibilityRole="button" accessibilityLabel="Close notification" style={{ marginTop: 26, alignSelf: 'stretch', height: 54, borderRadius: 18, backgroundColor: c.brand, alignItems: 'center', justifyContent: 'center' }}>
              <Text style={{ color: c.inkOnBrand, fontSize: 15.5, fontFamily: font.bold }}>OK</Text>
            </Pressable>
          )}
        </Pressable>
      </Pressable>
    </Modal>
  );
};

