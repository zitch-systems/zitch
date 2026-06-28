import React, { useEffect, useRef, useState } from 'react';
import { Animated, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font, palette } from '@/lib/theme';

/**
 * Branded toast — the design's `notify()`: a pill at the TOP of the screen,
 * dark `--ink-1` background with white text, a cyan check for success / red x
 * for error, sliding down with a spring and auto-dismissing. Imperative API so
 * it stays a near drop-in for the 37 callers:
 *
 *   notify('Success', 'BVN verified');   // kind inferred from the title
 *   notifyError('Could not start payment');
 *
 * Mount <NotifyHost/> once at the app root. Confirmation dialogs that need
 * action buttons should keep using Alert.alert (this is for one-shot messages).
 */
type Kind = 'success' | 'error' | 'info';
type Item = { title: string; message?: string; kind: Kind; key: number };

let _emit: ((i: Item) => void) | null = null;
let _seq = 0;

const inferKind = (title: string): Kind =>
  /error|fail|wrong|invalid|unable|could ?n.?t|denied/i.test(title) ? 'error'
    : /success|done|sent|verified|updated|complete|saved|added|copied/i.test(title) ? 'success'
      : 'info';

export function notify(title: string, message?: string, kind?: Kind): void {
  _emit?.({ title, message, kind: kind ?? inferKind(title), key: ++_seq });
}
export const notifySuccess = (title: string, message?: string) => notify(title, message, 'success');
export const notifyError = (title: string, message?: string) => notify(title, message, 'error');

// Icon + accent per kind — check in cyan, x in red, bell in cyan (design spec).
const STYLE: Record<Kind, { icon: string; color: string }> = {
  success: { icon: 'check', color: palette.cyan },
  error: { icon: 'x', color: palette.red },
  info: { icon: 'bell', color: palette.cyan },
};

const VISIBLE_MS = 2800;

export const NotifyHost = () => {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const [item, setItem] = useState<Item | null>(null);
  const y = useRef(new Animated.Value(-140)).current;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const hide = () => {
    Animated.timing(y, { toValue: -140, duration: 220, useNativeDriver: true }).start(() => setItem(null));
  };

  useEffect(() => {
    _emit = (i: Item) => {
      if (timer.current) clearTimeout(timer.current);
      setItem(i);
    };
    return () => { _emit = null; };
  }, []);

  // Spring the pill in whenever a new item arrives, then auto-dismiss.
  useEffect(() => {
    if (!item) return;
    y.setValue(-140);
    Animated.spring(y, { toValue: 0, useNativeDriver: true, bounciness: 9, speed: 14 }).start();
    timer.current = setTimeout(hide, VISIBLE_MS);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [item?.key]);

  if (!item) return null;
  const s = STYLE[item.kind];

  return (
    <View
      pointerEvents="box-none"
      style={{ position: 'absolute', top: insets.top + 8, left: 0, right: 0, alignItems: 'center', zIndex: 9999, paddingHorizontal: 16 }}
    >
      <Animated.View style={{ transform: [{ translateY: y }], maxWidth: 440, width: '100%' }}>
        <View
          onTouchEnd={hide}
          style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
            alignSelf: 'center',
            maxWidth: '100%',
            backgroundColor: c.ink1,
            borderRadius: 999,
            paddingVertical: 11,
            paddingLeft: 12,
            paddingRight: 18,
            shadowColor: '#021410',
            shadowOpacity: 0.3,
            shadowRadius: 16,
            shadowOffset: { width: 0, height: 8 },
            elevation: 8,
          }}
        >
          <View style={{ width: 26, height: 26, borderRadius: 13, backgroundColor: 'rgba(255,255,255,.12)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name={s.icon} size={16} color={s.color} stroke={2.8} />
          </View>
          <View style={{ flexShrink: 1 }}>
            <Text numberOfLines={1} style={{ fontSize: 13.5, fontFamily: font.bold, color: '#fff' }}>{item.title}</Text>
            {item.message ? (
              <Text numberOfLines={2} style={{ fontSize: 12, color: 'rgba(255,255,255,.78)', marginTop: 1, fontFamily: font.regular }}>
                {item.message}
              </Text>
            ) : null}
          </View>
        </View>
      </Animated.View>
    </View>
  );
};
