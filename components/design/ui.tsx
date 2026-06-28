import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  ScrollView,
  RefreshControl,
  Modal,
  Animated,
  StyleSheet,
  ViewStyle,
  TextStyle,
  useWindowDimensions,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import ZIcon from '@/components/design/ZIcon';
import AmbientBackground from '@/components/design/AmbientBackground';
import { Naira, NText } from '@/components/design/Naira';
import { useTheme, font, radius, ThemeTokens, ICON_COLORS, iconTint } from '@/lib/theme';
import { money as fmtMoney, moneyk as fmtMoneyk } from '@/lib/format';
import { isBiometricEnabled, isBiometricAvailable, authenticate, biometricLabel } from '@/lib/biometrics';
import { getTransactionPin } from '@/lib/secureStore';

export const money = fmtMoney;
export const moneyk = fmtMoneyk;
export { Naira, NText };

// ---- Tap: shared 3D spring press wrapper (design `Tap`) ----
// The design wraps EVERY interactive element in a 3D push-in: on press it scales
// to .96 with a perspective tilt and fades to .92, then springs back with an
// overshoot (cubic-bezier(.34,1.56,.64,1) analogue). Built on RN Animated
// (the project has no reanimated babel plugin) so it works in the APK as-is.
export const Tap = ({
  children,
  onPress,
  onLongPress,
  disabled,
  style,
  hitSlop,
  accessibilityLabel,
}: {
  children: React.ReactNode;
  onPress?: () => void;
  onLongPress?: () => void;
  disabled?: boolean;
  style?: ViewStyle;
  hitSlop?: number;
  accessibilityLabel?: string;
}) => {
  const v = useRef(new Animated.Value(0)).current;
  const animate = (to: number, bounce = false) =>
    Animated.spring(v, {
      toValue: to,
      useNativeDriver: true,
      speed: 50,
      bounciness: bounce ? 12 : 0,
    }).start();
  const scale = v.interpolate({ inputRange: [0, 1], outputRange: [1, 0.96] });
  const opacity = v.interpolate({ inputRange: [0, 1], outputRange: [1, 0.92] });
  return (
    <Pressable
      onPress={disabled ? undefined : onPress}
      onLongPress={disabled ? undefined : onLongPress}
      onPressIn={() => !disabled && animate(1)}
      onPressOut={() => animate(0, true)}
      hitSlop={hitSlop}
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="button"
    >
      <Animated.View style={[{ transform: [{ perspective: 150 }, { scale }], opacity }, style]}>
        {children}
      </Animated.View>
    </Pressable>
  );
};

const cardShadow = {
  shadowColor: '#063731',
  shadowOpacity: 0.12,
  shadowRadius: 16,
  shadowOffset: { width: 0, height: 8 },
  elevation: 3,
};

// ---- Layout shell ----
// `tab` adds extra bottom padding so content clears the custom bottom nav
// (the tab screens render their own nav bar over the scene).
export const Screen = ({
  children,
  pad = true,
  scroll = true,
  tab = false,
  onRefresh,
  refreshing = false,
}: {
  children: React.ReactNode;
  pad?: boolean;
  scroll?: boolean;
  tab?: boolean;
  // Pass onRefresh to enable pull-to-refresh on the scroll view.
  onRefresh?: () => void;
  refreshing?: boolean;
}) => {
  const { c } = useTheme();
  const { width } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const bottomPad = tab ? 96 : 28;
  // Fold/tablet: cap the content to a comfortable reading width and centre it so
  // screens never stretch edge-to-edge on wide displays. No-op on phones
  // (maxW undefined → the inner view is simply full width, as before).
  const maxW = width >= 600 ? 720 : undefined;
  const px = pad ? 20 : 0;
  return (
    <LinearGradient colors={c.bgGradient} style={{ flex: 1 }}>
      <AmbientBackground />
      <SafeAreaView style={{ flex: 1 }} edges={['top']}>
        {scroll ? (
          <ScrollView
            showsVerticalScrollIndicator={false}
            refreshControl={
              onRefresh
                ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={c.brand} colors={[c.brand]} />
                : undefined
            }
            // Add the device's bottom safe-area inset so the last content (buttons,
            // PIN pad, list rows) clears the home indicator / gesture bar instead of
            // being cut off — fixes the "cut at the bottom" on installed builds.
            contentContainerStyle={{ paddingBottom: bottomPad + insets.bottom, alignItems: 'center' }}
          >
            <View style={{ width: '100%', maxWidth: maxW, paddingHorizontal: px }}>{children}</View>
          </ScrollView>
        ) : (
          <View style={{ flex: 1, alignItems: 'center', paddingBottom: insets.bottom }}>
            <View style={{ flex: 1, width: '100%', maxWidth: maxW, paddingHorizontal: px }}>{children}</View>
          </View>
        )}
      </SafeAreaView>
    </LinearGradient>
  );
};

export const Header = ({
  title,
  sub,
  onBack,
  right,
}: {
  title?: string;
  sub?: string;
  onBack?: () => void;
  right?: React.ReactNode;
}) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingTop: 6, paddingBottom: 16 }}>
      {onBack && (
        <Pressable
          onPress={onBack}
          style={{
            width: 42,
            height: 42,
            borderRadius: 13,
            backgroundColor: c.surface,
            borderWidth: 1,
            borderColor: c.line,
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <ZIcon name="left" size={20} color={c.ink1} />
        </Pressable>
      )}
      {title && (
        <View style={{ flex: 1, minWidth: 0 }}>
          <NText style={{ fontSize: 19, fontFamily: font.extrabold, color: c.ink1, letterSpacing: -0.2 }}>{title}</NText>
          {sub && <NText style={{ fontSize: 13, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{sub}</NText>}
        </View>
      )}
      {right}
    </View>
  );
};

export const Card = ({
  children,
  style,
  onPress,
  pad = 18,
}: {
  children: React.ReactNode;
  style?: ViewStyle;
  onPress?: () => void;
  pad?: number;
}) => {
  const { c } = useTheme();
  const base: ViewStyle = {
    backgroundColor: c.surface,
    borderWidth: 1,
    borderColor: c.line,
    borderRadius: radius.lg,
    padding: pad,
    ...cardShadow,
    ...style,
  };
  if (onPress) return <Tap onPress={onPress} style={base}>{children}</Tap>;
  return <View style={base}>{children}</View>;
};

// ---- Buttons ----
type BtnVariant = 'primary' | 'deep' | 'dark' | 'ghost' | 'outline' | 'cyan';
type BtnSize = 'lg' | 'md' | 'sm';

const btnSizes: Record<BtnSize, ViewStyle> = {
  lg: { height: 56, paddingHorizontal: 24 },
  md: { height: 48, paddingHorizontal: 20 },
  sm: { height: 40, paddingHorizontal: 16 },
};
const btnFont: Record<BtnSize, number> = { lg: 16, md: 15, sm: 14 };
// Design button radii (ui.jsx PrimaryButton = 18; README buttons 16/14), not pill.
const btnRadius: Record<BtnSize, number> = { lg: 18, md: 16, sm: 14 };

export const Btn = ({
  label,
  onPress,
  variant = 'primary',
  icon,
  size = 'lg',
  disabled,
  full = true,
  style,
}: {
  label: string;
  onPress?: () => void;
  variant?: BtnVariant;
  icon?: string;
  size?: BtnSize;
  disabled?: boolean;
  full?: boolean;
  style?: ViewStyle;
}) => {
  const { c } = useTheme();
  const variants: Record<BtnVariant, { bg: string; fg: string; border?: string }> = {
    primary: { bg: c.brand, fg: c.inkOnBrand },
    deep: { bg: c.brandDeep, fg: '#fff' },
    dark: { bg: c.ink1, fg: c.bg },
    ghost: { bg: c.surface3, fg: c.ink1 },
    outline: { bg: 'transparent', fg: c.ink1, border: c.line },
    cyan: { bg: c.cyan, fg: '#04201C' },
  };
  const v = variants[variant];
  return (
    <Tap onPress={onPress} disabled={disabled} style={full ? { width: '100%' } : undefined}>
      <View
        style={[
          {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 9,
            borderRadius: btnRadius[size],
            width: full ? '100%' : undefined,
            backgroundColor: v.bg,
            borderWidth: v.border ? 1.5 : 0,
            borderColor: v.border,
            opacity: disabled ? 0.5 : 1,
            ...btnSizes[size],
          },
          style,
        ]}
      >
        {icon && <ZIcon name={icon} size={size === 'lg' ? 20 : 18} color={v.fg} stroke={2.2} />}
        <NText style={{ color: v.fg, fontFamily: font.bold, fontSize: btnFont[size] }}>{label}</NText>
      </View>
    </Tap>
  );
};

// ---- Money text ----
export const Money = ({
  amount,
  size = 34,
  color,
  showk,
}: {
  amount: number;
  size?: number;
  color?: string;
  showk?: boolean;
}) => {
  const { c } = useTheme();
  return (
    <NText
      style={{
        fontSize: size,
        fontFamily: font.extrabold,
        color: color || c.ink1,
        letterSpacing: -0.5,
        fontVariant: ['tabular-nums'],
      }}
    >
      {showk ? moneyk(amount) : money(amount)}
    </NText>
  );
};

// ---- Generic list item ----
export const ZItem = ({
  icon,
  iconColor,
  iconBg,
  title,
  sub,
  right,
  onPress,
  last,
}: {
  icon?: string;
  iconColor?: string;
  iconBg?: string;
  title: string;
  sub?: string;
  right?: React.ReactNode;
  onPress?: () => void;
  last?: boolean;
}) => {
  const { c, theme } = useTheme();
  const Wrap: any = onPress ? Tap : View;
  // Per-icon accent so list rows (profile, settings, savings, loan…) read
  // colourful — unless the caller overrides, or the icon isn't mapped.
  const mapped = icon ? ICON_COLORS[icon] : undefined;
  const accent = iconColor || mapped || c.brand;
  const accentBg = iconBg || (mapped ? iconTint(mapped, theme === 'dark') : c.surface3);
  return (
    <Wrap
      onPress={onPress}
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: 14,
        paddingVertical: 13,
        borderBottomWidth: last ? 0 : 1,
        borderBottomColor: c.line,
      }}
    >
      {icon && (
        <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: accentBg, alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name={icon} size={21} color={accent} stroke={2} />
        </View>
      )}
      <View style={{ flex: 1, minWidth: 0 }}>
        <NText numberOfLines={1} style={{ fontSize: 15, fontFamily: font.semibold, color: c.ink1 }}>{title}</NText>
        {sub && <NText style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{sub}</NText>}
      </View>
      {right}
    </Wrap>
  );
};

// ---- Field ----
export const Field = ({
  label,
  value,
  onChangeText,
  placeholder,
  keyboardType,
  secureTextEntry,
  maxLength,
  prefix,
  suffix,
  editable = true,
  pointerEvents,
  autoCapitalize,
}: {
  label?: string;
  value?: string;
  onChangeText?: (t: string) => void;
  placeholder?: string;
  keyboardType?: any;
  secureTextEntry?: boolean;
  maxLength?: number;
  prefix?: React.ReactNode;
  suffix?: React.ReactNode;
  editable?: boolean;
  pointerEvents?: 'none' | 'auto' | 'box-none';
  autoCapitalize?: 'none' | 'sentences' | 'words' | 'characters';
}) => {
  const { c } = useTheme();
  const [show, setShow] = useState(false);
  const secure = !!secureTextEntry && !show;
  return (
    <View>
      {label && <Text style={{ fontSize: 13, fontFamily: font.semibold, color: c.ink2, marginBottom: 8 }}>{label}</Text>}
      <View
        pointerEvents={pointerEvents}
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          gap: 10,
          backgroundColor: c.surface,
          borderWidth: 1,
          borderColor: c.line,
          borderRadius: 13,
          paddingHorizontal: 16,
          height: 56,
        }}
      >
        {prefix}
        <TextInput
          editable={editable}
          value={value}
          onChangeText={onChangeText}
          placeholder={placeholder}
          placeholderTextColor={c.ink3}
          keyboardType={keyboardType}
          secureTextEntry={secure}
          maxLength={maxLength}
          autoCapitalize={autoCapitalize}
          style={{ flex: 1, fontSize: 16, color: c.ink1, fontFamily: font.medium }}
        />
        {secureTextEntry ? (
          <Pressable onPress={() => setShow((s) => !s)} hitSlop={10} accessibilityLabel={show ? 'Hide password' : 'Show password'}>
            <ZIcon name={show ? 'eyeoff' : 'eye'} size={20} color={c.ink3} />
          </Pressable>
        ) : suffix}
      </View>
    </View>
  );
};

// ---- Bottom sheet ----
export const Sheet = ({
  open,
  onClose,
  children,
  title,
}: {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  title?: string;
}) => {
  const { c } = useTheme();
  const { width } = useWindowDimensions();
  // On fold/tablet, cap the sheet width and centre it so it reads as a card
  // rather than stretching across the whole display. Full-width on phones.
  const maxW = width >= 600 ? 560 : undefined;
  // Spring slide-up (design uses var(--ease-spring) on sheets). RN's built-in
  // Modal "slide" is linear, so we drive translateY ourselves with a spring.
  const ty = useRef(new Animated.Value(700)).current;
  useEffect(() => {
    if (open) {
      ty.setValue(700);
      Animated.spring(ty, { toValue: 0, useNativeDriver: true, bounciness: 7, speed: 13 }).start();
    }
  }, [open]);
  return (
    <Modal visible={open} transparent animationType="none" onRequestClose={onClose}>
      <Pressable onPress={onClose} style={{ flex: 1, backgroundColor: 'rgba(2,16,14,.5)' }} />
      <Animated.View
        style={{
          width: '100%',
          maxWidth: maxW,
          alignSelf: 'center',
          backgroundColor: c.surface,
          borderTopLeftRadius: 28,
          borderTopRightRadius: 28,
          padding: 20,
          paddingTop: 10,
          paddingBottom: 26,
          maxHeight: '88%',
          transform: [{ translateY: ty }],
        }}
      >
        <View style={{ width: 40, height: 5, borderRadius: 3, backgroundColor: c.line, alignSelf: 'center', marginBottom: 14 }} />
        {title && <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1, marginBottom: 14 }}>{title}</Text>}
        <ScrollView showsVerticalScrollIndicator={false}>{children}</ScrollView>
      </Animated.View>
    </Modal>
  );
};

// ---- PIN entry ----
export const PinPad = ({ onComplete, length = 4, busy = false, error }: { onComplete?: (pin: string) => void; length?: number; busy?: boolean; error?: string }) => {
  const { c } = useTheme();
  const [pin, setPin] = useState('');
  // Biometric "pay" shortcut: shown only when the user enabled biometrics, the
  // device has them, and a PIN is cached in the keychain to submit on success.
  const [bioKind, setBioKind] = useState<'face' | 'fingerprint' | 'biometrics' | null>(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      const [enabled, available, storedPin] = await Promise.all([
        isBiometricEnabled(), isBiometricAvailable(), getTransactionPin(),
      ]);
      const kind = enabled && available && storedPin ? await biometricLabel() : null;
      if (alive) setBioKind(kind);
    })();
    return () => { alive = false; };
  }, []);
  const press = (d: string) => {
    if (busy) return; // ignore input while a submission is in flight (prevents double-charge)
    if (pin.length < length) {
      const np = pin + d;
      setPin(np);
      if (np.length === length) setTimeout(() => { onComplete && onComplete(np); setPin(''); }, 120);
    }
  };
  const del = () => { if (!busy) setPin((p) => p.slice(0, -1)); };
  const useBiometric = async () => {
    if (busy) return;
    const ok = await authenticate('Approve payment');
    if (!ok) return;
    const storedPin = await getTransactionPin();
    if (storedPin) onComplete && onComplete(storedPin);
  };
  const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', 'del'];
  return (
    <View>
      <View style={{ flexDirection: 'row', justifyContent: 'center', gap: 16, marginTop: 8, marginBottom: error ? 10 : 26 }}>
        {Array.from({ length }).map((_, i) => (
          <View
            key={i}
            style={{
              width: 16,
              height: 16,
              borderRadius: 8,
              backgroundColor: i < pin.length ? c.brand : c.surface3,
              borderWidth: 2,
              borderColor: error ? c.red : i < pin.length ? c.brand : c.line,
            }}
          />
        ))}
      </View>
      {error ? (
        <Text style={{ textAlign: 'center', color: c.red, fontSize: 13, fontFamily: font.semibold, marginBottom: 16 }}>
          {error}
        </Text>
      ) : null}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', maxWidth: 280, alignSelf: 'center' }}>
        {keys.map((k, i) =>
          k === '' ? (
            bioKind ? (
              <View key={i} style={{ width: '33.33%', padding: 7 }}>
                <Tap
                  onPress={useBiometric}
                  disabled={busy}
                  style={{
                    height: 56,
                    borderRadius: 14,
                    backgroundColor: 'transparent',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <ZIcon name={bioKind === 'face' ? 'faceid' : 'fingerprint'} size={26} color={c.brand} />
                </Tap>
              </View>
            ) : (
              <View key={i} style={{ width: '33.33%', height: 56 }} />
            )
          ) : (
            <View key={i} style={{ width: '33.33%', padding: 7 }}>
              <Tap
                onPress={() => (k === 'del' ? del() : press(k))}
                style={{
                  height: 56,
                  borderRadius: 14,
                  backgroundColor: 'transparent',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                {k === 'del' ? (
                  <ZIcon name="left" size={24} color={c.ink1} />
                ) : (
                  <Text style={{ fontSize: 24, fontFamily: font.bold, color: c.ink1 }}>{k}</Text>
                )}
              </Tap>
            </View>
          )
        )}
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 14 }}>
        <ZIcon name="lock" size={13} color={c.ink3} />
        <Text style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.medium }}>Secured by Zitch</Text>
      </View>
    </View>
  );
};

export const PinSheet = ({
  open,
  onClose,
  onComplete,
  title = 'Enter your PIN',
  busy = false,
  error,
}: {
  open: boolean;
  onClose: () => void;
  onComplete?: (pin: string) => void;
  title?: string;
  busy?: boolean;
  error?: string;
}) => {
  const { c } = useTheme();
  return (
    <Sheet open={open} onClose={onClose} title={title}>
      <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
        Confirm this transaction with your 4-digit PIN
      </Text>
      <PinPad onComplete={onComplete} busy={busy} error={error} />
    </Sheet>
  );
};

// ---- Translucent pill (hero actions) ----
export const StatPill = ({ icon, label, onPress }: { icon: string; label: string; onPress?: () => void }) => (
  <Tap
    onPress={onPress}
    style={{ flexDirection: 'row', alignItems: 'center', gap: 7, paddingVertical: 9, paddingHorizontal: 14, backgroundColor: 'rgba(255,255,255,.16)', borderRadius: 999 }}
  >
    <ZIcon name={icon} size={16} color="#fff" stroke={2.2} />
    <Text style={{ color: '#fff', fontSize: 13, fontFamily: font.semibold }}>{label}</Text>
  </Tap>
);

// ---- Transaction row ----
export type Txn = {
  id: string;
  type: string;
  detail: string;
  amount: number;
  status: string;
  time?: string;
  icon: string;
  dir: 'in' | 'out';
  reference?: string;
};

// Two-letter monogram from a transaction's label (design renders txns/banks as
// monograms on a solid colour tile, never raster logos / service icons).
const txnMono = (s: string) => {
  const w = (s || '').trim().split(/\s+/).filter(Boolean);
  const code = ((w[0]?.[0] || '') + (w[1]?.[0] || w[0]?.[1] || '')).toUpperCase();
  return code || 'ZT';
};

export const TxnRow = ({ txn, last, onPress }: { txn: Txn; last?: boolean; onPress?: () => void }) => {
  const { c } = useTheme();
  const inflow = txn.dir === 'in';
  // Credits read green; debits take their service's accent colour (airtime
  // teal, data blue, …) so transaction lists stay colourful. The tile is the
  // SOLID accent with a 2-letter white monogram, per the design's TxnRow.
  const accent = inflow ? c.lime : (ICON_COLORS[txn.icon] ?? c.brand);
  const Wrap: any = onPress ? Tap : View;
  return (
    <Wrap onPress={onPress} style={{ flexDirection: 'row', alignItems: 'center', gap: 13, paddingVertical: 13, borderBottomWidth: last ? 0 : 1, borderBottomColor: c.line }}>
      <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: accent, alignItems: 'center', justifyContent: 'center' }}>
        <Text style={{ fontSize: 14.5, fontFamily: font.extrabold, color: '#fff' }}>{txnMono(txn.type)}</Text>
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={{ fontSize: 14.5, fontFamily: font.semibold, color: c.ink1 }}>{txn.type}</Text>
        <Text numberOfLines={1} style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{txn.detail}</Text>
      </View>
      <View style={{ alignItems: 'flex-end' }}>
        <NText style={{ fontSize: 14.5, fontFamily: font.bold, color: inflow ? c.lime : c.ink1, fontVariant: ['tabular-nums'] }}>
          {(inflow ? '+' : '-') + money(Math.abs(txn.amount))}
        </NText>
        <Text style={{ fontSize: 11.5, color: txn.status === 'Pending' ? c.amber : c.ink3, marginTop: 2, fontFamily: font.regular }}>{txn.status}</Text>
      </View>
    </Wrap>
  );
};
