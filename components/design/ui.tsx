import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  ScrollView,
  RefreshControl,
  Modal,
  ViewStyle,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  useWindowDimensions,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import ZIcon from '@/components/design/ZIcon';
import AmbientBackground from '@/components/design/AmbientBackground';
import { Loading, LoadingMark } from '@/components/design/Loading';
import { Naira, NText } from '@/components/design/Naira';
import { useTheme, font, radius, ICON_COLORS, iconTint } from '@/lib/theme';
import { money as fmtMoney, moneyk as fmtMoneyk } from '@/lib/format';
import { isBiometricTxnEnabled, isBiometricAvailable, biometricLabel } from '@/lib/biometrics';
import { getTransactionPin, hasTransactionPin } from '@/lib/secureStore';
import { usePinScreenProtection } from '@/lib/screenCapture';

export const money = fmtMoney;
export const moneyk = fmtMoneyk;
export { Naira, NText };

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
  header,
  pad = true,
  scroll = true,
  tab = false,
  onRefresh,
  refreshing = false,
}: {
  children: React.ReactNode;
  // Rendered ABOVE the scroll view, as its sibling rather than its first child —
  // so it never scrolls. Not `stickyHeaderIndices`: that pins a header only once
  // the ScrollView has scrolled PAST it, section-list style, which still lets it
  // travel with the content up to that point. This is simpler and matches what
  // was actually asked for — the header never moves at all.
  header?: React.ReactNode;
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
  // `header`, if given, needs the same width cap and horizontal padding as the
  // scrolling content below it, or the two drift out of alignment on tablet/fold
  // widths where maxW actually applies.
  const headerBlock = header ? (
    <View style={{ width: '100%', maxWidth: maxW, paddingHorizontal: px, alignSelf: 'center' }}>
      {header}
    </View>
  ) : null;
  return (
    <LinearGradient colors={c.bgGradient} style={{ flex: 1 }}>
      <AmbientBackground />
      <SafeAreaView style={{ flex: 1 }} edges={['top']}>
        {/* Lift content above the keyboard on iOS so a focused field / its submit
            button is never hidden behind it (Android handles this via the OS
            softInputMode). */}
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        {headerBlock}
        {scroll ? (
          <ScrollView
            showsVerticalScrollIndicator={false}
            keyboardDismissMode={Platform.OS === 'ios' ? 'interactive' : 'on-drag'}
            automaticallyAdjustKeyboardInsets={Platform.OS === 'ios'}
            // "handled" so the FIRST tap on a button while the keyboard is open
            // activates it, instead of being swallowed to just dismiss the keyboard
            // (which forced a double-tap on every form's submit button).
            keyboardShouldPersistTaps="handled"
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
        </KeyboardAvoidingView>
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
          accessibilityRole="button"
          accessibilityLabel="Go back"
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
  pad = 15,
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
  if (onPress) return <Pressable onPress={onPress} accessibilityRole="button" style={({ pressed }) => [base, pressed && { opacity: 0.9 }]}>{children}</Pressable>;
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
    <Pressable
      onPress={disabled ? undefined : onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: !!disabled }}
      disabled={!!disabled}
      style={({ pressed }) => [
        {
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 9,
          borderRadius: radius.pill,
          width: full ? '100%' : undefined,
          backgroundColor: v.bg,
          borderWidth: v.border ? 1.5 : 0,
          borderColor: v.border,
          opacity: disabled ? 0.5 : pressed ? 0.92 : 1,
          ...btnSizes[size],
        },
        style,
      ]}
    >
      {icon && <ZIcon name={icon} size={size === 'lg' ? 20 : 18} color={v.fg} stroke={2.2} />}
      <NText style={{ color: v.fg, fontFamily: font.bold, fontSize: btnFont[size] }}>{label}</NText>
    </Pressable>
  );
};

// ---- Toggle switch ----
// Shared on/off switch used across settings and the security screens.
export const Toggle = ({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) => {
  const { c } = useTheme();
  return (
    <Pressable
      onPress={disabled ? undefined : () => onChange(!on)}
      accessibilityRole="switch"
      accessibilityState={{ checked: on, disabled: !!disabled }}
      style={{ width: 46, height: 28, borderRadius: 999, padding: 3, backgroundColor: on ? c.brand : c.surface3, justifyContent: 'center', opacity: disabled ? 0.5 : 1 }}
    >
      <View style={{ width: 22, height: 22, borderRadius: 11, backgroundColor: '#fff', transform: [{ translateX: on ? 18 : 0 }] }} />
    </Pressable>
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
  leading,
  title,
  sub,
  right,
  onPress,
  last,
}: {
  icon?: string;
  iconColor?: string;
  iconBg?: string;
  /** Custom glyph for the icon box (e.g. a brand mark ZIcon doesn't carry).
   *  Rendered in the same 44px box as `icon`, so a row using it stays on the
   *  same alignment grid as every other row in the group. */
  leading?: React.ReactNode;
  title: string;
  sub?: string;
  right?: React.ReactNode;
  onPress?: () => void;
  last?: boolean;
}) => {
  const { c, theme } = useTheme();
  const Wrap: any = onPress ? Pressable : View;
  // Per-icon accent so list rows (profile, settings, savings, loan…) read
  // colourful — unless the caller overrides, or the icon isn't mapped.
  const mapped = icon ? ICON_COLORS[icon] : undefined;
  const accent = iconColor || mapped || c.brand;
  const accentBg = iconBg || (mapped ? iconTint(mapped, theme === 'dark') : c.surface3);
  return (
    <Wrap
      onPress={onPress}
      accessibilityRole={onPress ? 'button' : undefined}
      accessibilityLabel={onPress ? [title, sub].filter(Boolean).join(', ') : undefined}
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: 12,
        paddingVertical: 11,
        borderBottomWidth: last ? 0 : 1,
        borderBottomColor: c.line,
      }}
    >
      {(icon || leading) && (
        <View style={{ width: 38, height: 38, borderRadius: 11, backgroundColor: accentBg, alignItems: 'center', justifyContent: 'center' }}>
          {leading ?? <ZIcon name={icon as string} size={18} color={accent} stroke={2.2} />}
        </View>
      )}
      <View style={{ flex: 1, minWidth: 0 }}>
        <NText numberOfLines={1} style={{ fontSize: 14, fontFamily: font.semibold, color: c.ink1 }}>{title}</NText>
        {sub && <NText style={{ fontSize: 12, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{sub}</NText>}
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
  autoComplete,
  textContentType,
  returnKeyType,
  onSubmitEditing,
  autoCorrect,
  inputRef,
  loading = false,
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
  // Autofill / SMS-autoread / keyboard-chaining pass-throughs (opt-in per field).
  autoComplete?: any;
  textContentType?: any;
  returnKeyType?: 'done' | 'go' | 'next' | 'search' | 'send';
  onSubmitEditing?: () => void;
  autoCorrect?: boolean;
  inputRef?: React.Ref<TextInput>;
  // While true, the input area shows the small branded Zitch loader in place of
  // the value/placeholder (e.g. the Send-money bank field while auto-detecting).
  loading?: boolean;
}) => {
  const { c } = useTheme();
  const [show, setShow] = useState(false);
  const secure = !!secureTextEntry && !show;
  // Passwords/PINs and emails must never be auto-capitalized or auto-corrected —
  // a silently capitalized first character is a classic "wrong password" trap.
  // Default those off unless the caller explicitly overrides.
  const isEmail = keyboardType === 'email-address';
  const capitalize = autoCapitalize ?? (secureTextEntry || isEmail ? 'none' : undefined);
  const correct = autoCorrect ?? (secureTextEntry || isEmail ? false : undefined);
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
          borderRadius: radius.md,
          paddingHorizontal: 16,
          height: 56,
        }}
      >
        {prefix}
        {loading ? (
          <View
            style={{ flex: 1, flexDirection: 'row', alignItems: 'center' }}
            accessible
            accessibilityRole="progressbar"
            accessibilityLabel="Loading"
          >
            <LoadingMark size={22} />
          </View>
        ) : (
        <TextInput
          ref={inputRef}
          editable={editable}
          value={value}
          onChangeText={onChangeText}
          placeholder={placeholder}
          accessibilityLabel={label || placeholder || 'Input'}
          placeholderTextColor={c.ink3}
          keyboardType={keyboardType}
          secureTextEntry={secure}
          maxLength={maxLength}
          autoCapitalize={capitalize}
          autoCorrect={correct}
          autoComplete={autoComplete}
          textContentType={textContentType}
          returnKeyType={returnKeyType}
          onSubmitEditing={onSubmitEditing}
          style={{ flex: 1, fontSize: 16, color: c.ink1, fontFamily: font.medium }}
        />
        )}
        {secureTextEntry ? (
          <Pressable
            onPress={() => setShow((s) => !s)}
            hitSlop={10}
            accessibilityRole="button"
            accessibilityLabel={show ? 'Hide password' : 'Show password'}
          >
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
  protectScreen = false,
}: {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  title?: string;
  /** Block screenshots only while a PIN-entry sheet is actually visible. */
  protectScreen?: boolean;
}) => {
  const { c } = useTheme();
  const { width } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  usePinScreenProtection(open && protectScreen);
  // A hidden React Native Modal can keep its children mounted. PIN pads then
  // initialise biometrics while the sheet is invisible and initialise again
  // when the customer opens it, which presents the native fingerprint prompt
  // twice. Mount sheet content only for the visible lifetime of the sheet.
  if (!open) return null;
  // On fold/tablet, cap the sheet width and centre it so it reads as a card
  // rather than stretching across the whole display. Full-width on phones.
  const maxW = width >= 600 ? 560 : undefined;
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      {/* Ride the panel above the keyboard so a field inside the sheet (bank
          search, fund amount) and its button aren't covered while typing. */}
      <KeyboardAvoidingView
        style={{ flex: 1, justifyContent: 'flex-end' }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <Pressable onPress={onClose} style={{ flex: 1, backgroundColor: 'rgba(2,16,14,.5)' }} />
        <View
          style={{
            width: '100%',
            maxWidth: maxW,
            alignSelf: 'center',
            backgroundColor: c.surface,
            borderTopLeftRadius: 28,
            borderTopRightRadius: 28,
            padding: 20,
            paddingTop: 10,
            // Clear the home indicator / gesture bar so the sheet's bottom content
            // (the PIN keypad's last row) isn't flush against the screen edge.
            paddingBottom: 26 + insets.bottom,
            maxHeight: '90%',
          }}
        >
          <View style={{ width: 40, height: 5, borderRadius: 3, backgroundColor: c.line, alignSelf: 'center', marginBottom: 14 }} />
          {title && <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1, marginBottom: 14 }}>{title}</Text>}
          {/* "handled": first tap on a row/button inside the sheet activates it
              instead of only dismissing the keyboard. */}
          <ScrollView showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">{children}</ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
};

// ---- PIN entry ----
export const PinPad = ({ onComplete, length = 6, busy = false, error, autoBiometric = true }: { onComplete?: (pin: string, viaBiometric?: boolean) => void; length?: number; busy?: boolean; error?: string; autoBiometric?: boolean }) => {
  const { c } = useTheme();
  const [pin, setPin] = useState('');
  // Biometric "pay" shortcut: shown only when the user enabled biometrics, the
  // device has them, and a PIN is cached in the keychain to submit on success.
  const [bioKind, setBioKind] = useState<'face' | 'fingerprint' | 'biometrics' | null>(null);
  // Until we've checked whether biometric pay is set up we don't know which screen
  // to show; `resolved` avoids flashing the keypad before switching to biometrics.
  const [resolved, setResolved] = useState(false);
  // When biometric pay is set up we open straight onto a biometric screen (not the
  // keypad); the user can tap ✕ to fall back to the PIN.
  const [showBio, setShowBio] = useState(false);
  // Fire the biometric prompt at most once per mount (each time the sheet opens),
  // so the OS sheet doesn't reappear after a manual cancel or a wrong-PIN retry.
  const autoTried = React.useRef(false);
  // Both the auto prompt and the visible biometric button call the same async
  // function. A fast tap while the automatic OS sheet is opening used to start a
  // second native prompt; keep one scan and one completion in flight per pad.
  const bioInFlight = React.useRef(false);
  const bioCompleted = React.useRef(false);
  // Keep the latest onComplete/busy in refs so the biometric helpers stay STABLE
  // and the setup effect runs exactly once per mount — not on every render (an
  // inline onComplete in the parent would otherwise re-run the effect each render,
  // hammering native biometric calls and destabilising the sheet).
  const onCompleteRef = React.useRef(onComplete);
  const busyRef = React.useRef(busy);
  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);
  useEffect(() => { busyRef.current = busy; }, [busy]);
  useEffect(() => {
    if (error) bioCompleted.current = false;
  }, [error]);
  const handleBiometric = React.useCallback(async () => {
    if (busyRef.current || bioInFlight.current || bioCompleted.current) return;
    bioInFlight.current = true;
    try {
      // The transaction PIN is itself protected by SecureStore's authenticated
      // keychain ACL. Reading it opens the single OS biometric prompt; doing a
      // separate LocalAuthentication scan first caused the double prompt users
      // reported and added no protection to the already-gated secret.
      const storedPin = await getTransactionPin();
      if (storedPin) {
        bioCompleted.current = true;
        onCompleteRef.current && onCompleteRef.current(storedPin, true);
      }
    } catch {
      /* biometrics must never crash the payment sheet — fall back to the keypad */
    } finally {
      bioInFlight.current = false;
    }
  }, []);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        // Show the biometric screen only when the user has turned ON transaction-
        // biometrics AND has a cached PIN (a NON-secret flag; we never read the PIN
        // here just to pick a screen).
        const [txnOn, available, hasPin] = await Promise.all([
          isBiometricTxnEnabled(), isBiometricAvailable(), hasTransactionPin(),
        ]);
        const kind = txnOn && available && hasPin ? await biometricLabel() : null;
        if (!alive) return;
        setBioKind(kind);
        // Biometric approval set up → open on the biometric screen and prompt Face
        // ID / fingerprint straight away. Runs once per mount (this component mounts
        // only when the sheet actually opens).
        const useBio = !!(kind && autoBiometric);
        setShowBio(useBio);
        setResolved(true);
        if (useBio && !autoTried.current && !busyRef.current) {
          autoTried.current = true;
          handleBiometric();
        }
      } catch {
        if (alive) setResolved(true); // any failure → just show the keypad
      }
    })();
    return () => { alive = false; };
  }, [autoBiometric, handleBiometric]);
  const press = (d: string) => {
    if (busy) return; // ignore input while a submission is in flight (prevents double-charge)
    if (pin.length < length) {
      const np = pin + d;
      setPin(np);
      if (np.length === length) setTimeout(() => { onComplete && onComplete(np, false); setPin(''); }, 120);
    }
  };
  const del = () => { if (!busy) setPin((p) => p.slice(0, -1)); };
  // Row-based key layout: each row of three stretches edge-to-edge (with even
  // gaps) so the keypad lines up with the sheet's own padding instead of
  // floating on a fixed-width island with mismatched margins.
  const keyRows: string[][] = [['1', '2', '3'], ['4', '5', '6'], ['7', '8', '9'], ['bio', '0', 'del']];
  // While a submission is in flight, replace the keypad with the branded
  // loading animation — the moment the correct PIN is entered (or the biometric
  // clears) the sheet cuts straight to the same loader used across the app,
  // never an "authenticating" keypad state.
  if (busy) {
    return <Loading full={false} label="Processing…" />;
  }
  // Brief check-in-progress hold (only when biometrics may take over) so the
  // keypad doesn't flash before the biometric screen appears.
  if (!resolved && autoBiometric) {
    return (
      <View style={{ alignItems: 'center', paddingVertical: 48 }}>
        <ActivityIndicator color={c.brand} />
      </View>
    );
  }
  // Biometric-first screen: a big tap-to-scan icon (the OS prompt also auto-fires
  // on open) with a small ✕ underneath to cancel and use the PIN instead.
  if (showBio) {
    return (
      <View style={{ alignItems: 'center', paddingVertical: 16 }}>
        <Pressable
          onPress={handleBiometric}
          accessibilityRole="button"
          accessibilityLabel={bioKind === 'face' ? 'Approve with Face ID' : 'Approve with fingerprint'}
          style={{ width: 104, height: 104, borderRadius: 52, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}
        >
          <ZIcon name={bioKind === 'face' ? 'faceid' : 'fingerprint'} size={52} color={c.brand} />
        </Pressable>
        <Text style={{ marginTop: 18, fontSize: 15.5, fontFamily: font.bold, color: c.ink1 }}>
          {bioKind === 'face' ? 'Approve with Face ID' : 'Approve with fingerprint'}
        </Text>
        <Text style={{ marginTop: 6, fontSize: 12.5, fontFamily: font.regular, color: c.ink3 }}>
          Tap the icon to scan again
        </Text>
        {error ? (
          <Text style={{ textAlign: 'center', color: c.red, fontSize: 13, fontFamily: font.semibold, marginTop: 10 }}>{error}</Text>
        ) : null}
        <Pressable
          onPress={() => setShowBio(false)}
          accessibilityRole="button"
          accessibilityLabel="Use PIN instead"
          hitSlop={12}
          style={{ marginTop: 24, width: 46, height: 46, borderRadius: 23, borderWidth: 1.5, borderColor: c.line, alignItems: 'center', justifyContent: 'center' }}
        >
          <ZIcon name="x" size={20} color={c.ink2} />
        </Pressable>
        <Text style={{ marginTop: 9, fontSize: 12.5, color: c.ink3, fontFamily: font.medium }}>Use PIN instead</Text>
      </View>
    );
  }
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
      <View style={{ width: '100%', maxWidth: 420, alignSelf: 'center', gap: 10 }}>
        {keyRows.map((row, ri) => (
          <View key={ri} style={{ flexDirection: 'row', gap: 10 }}>
            {row.map((k) =>
              k === 'bio' && !bioKind ? (
                <View key={k} style={{ flex: 1, height: 62 }} />
              ) : (
                <Pressable
                  key={k}
                  disabled={busy}
                  accessibilityRole="button"
                  accessibilityLabel={k === 'del' ? 'Delete digit' : k === 'bio' ? 'Use biometric approval' : `Digit ${k}`}
                  accessibilityState={{ disabled: busy }}
                  onPress={() => (k === 'del' ? del() : k === 'bio' ? handleBiometric() : press(k))}
                  style={({ pressed }) => ({
                    flex: 1,
                    height: 62,
                    borderRadius: 16,
                    backgroundColor: pressed ? c.surface3 : c.surface,
                    borderWidth: 1,
                    borderColor: c.line,
                    alignItems: 'center',
                    justifyContent: 'center',
                  })}
                >
                  {k === 'del' ? (
                    <ZIcon name="left" size={24} color={c.ink1} />
                  ) : k === 'bio' ? (
                    <ZIcon name={bioKind === 'face' ? 'faceid' : 'fingerprint'} size={26} color={c.brand} />
                  ) : (
                    <Text style={{ fontSize: 24, fontFamily: font.bold, color: c.ink1 }}>{k}</Text>
                  )}
                </Pressable>
              )
            )}
          </View>
        ))}
      </View>
    </View>
  );
};

export const PinSheet = ({
  open,
  onClose,
  onComplete,
  title = 'Enter your PIN',
  subtitle = 'Confirm this transaction with your 6-digit PIN',
  busy = false,
  error,
  autoBiometric = false,
}: {
  open: boolean;
  onClose: () => void;
  onComplete?: (pin: string, viaBiometric?: boolean) => void;
  title?: string;
  subtitle?: string;
  busy?: boolean;
  error?: string;
  // Default OFF: PinSheet backs setup flows (capturing a PIN to enable biometric
  // pay) where auto-prompting biometrics would be wrong. Money-approval callers
  // pass autoBiometric so Face ID / fingerprint is offered on open.
  autoBiometric?: boolean;
}) => {
  const { c } = useTheme();
  return (
    <Sheet open={open} onClose={onClose} title={title} protectScreen>
      {/* No negative top margin: the subtitle renders inside the sheet's
          ScrollView, so pulling it up clips its top edge against the title. */}
      {!busy && (
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, fontFamily: font.regular }}>
          {subtitle}
        </Text>
      )}
      <PinPad onComplete={onComplete} busy={busy} error={error} autoBiometric={autoBiometric} />
    </Sheet>
  );
};

// ---- Translucent pill (hero actions) ----
export const StatPill = ({ icon, label, onPress }: { icon: string; label: string; onPress?: () => void }) => (
  <Pressable
    onPress={onPress}
    style={{ flexDirection: 'row', alignItems: 'center', gap: 7, paddingVertical: 9, paddingHorizontal: 14, backgroundColor: 'rgba(255,255,255,.16)', borderRadius: 999 }}
  >
    <ZIcon name={icon} size={16} color="#fff" stroke={2.2} />
    <Text style={{ color: '#fff', fontSize: 13, fontFamily: font.semibold }}>{label}</Text>
  </Pressable>
);

// ---- Status pill ----
// One place decides what a transaction status LOOKS like. The list row, the
// detail screen and the statement all read the same three states, and before
// this they each styled them differently — a failed transfer was red on one
// screen and plain grey on another, which is the one status you cannot afford
// to under-state.
export type TxnState = 'success' | 'pending' | 'failed';

export const txnState = (status: string): TxnState => {
  const s = (status || '').toLowerCase();
  if (/fail|declin|revers|cancel/.test(s)) return 'failed';
  if (/pend|process|await|queue/.test(s)) return 'pending';
  return 'success';
};

export const StatusPill = ({ status, small }: { status: string; small?: boolean }) => {
  const { c, theme } = useTheme();
  const state = txnState(status);
  const tone = state === 'failed' ? c.red : state === 'pending' ? c.amber : c.lime;
  return (
    <View
      style={{
        alignSelf: 'flex-end',
        paddingHorizontal: small ? 7 : 9,
        paddingVertical: small ? 2 : 3,
        borderRadius: 999,
        backgroundColor: iconTint(tone, theme === 'dark'),
      }}
    >
      <Text style={{ fontSize: small ? 10 : 11, fontFamily: font.semibold, color: tone }}>{status}</Text>
    </View>
  );
};

// ---- Header text link ----
// The "History" / "Download" affordance that sits in a Header's `right` slot.
// A plain coloured word rather than a bordered pill: it is a shortcut to a
// sibling screen, not an action on the screen you are looking at, and a button
// chrome around it competes with the screen's real primary button.
export const HeaderLink = ({ label, onPress, icon }: { label: string; onPress: () => void; icon?: string }) => {
  const { c } = useTheme();
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      hitSlop={10}
      style={({ pressed }) => ({ flexDirection: 'row', alignItems: 'center', gap: 5, opacity: pressed ? 0.6 : 1 })}
    >
      {icon ? <ZIcon name={icon} size={16} color={c.brand} stroke={2.2} /> : null}
      <Text style={{ fontSize: 14.5, fontFamily: font.bold, color: c.brand }}>{label}</Text>
    </Pressable>
  );
};

// ---- Pill tabs ----
// N-option selector. `Segmented` is a 2-slot toggle on a filled track; this is
// a row of free-standing pills that scrolls when the options outgrow the width
// (data-plan categories) and wraps a checkmark onto the chosen one when it is
// a commitment rather than a view filter (statement time frame).
export const PillTabs = ({
  options,
  value,
  onChange,
  scroll = true,
  check = false,
}: {
  options: { v: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  scroll?: boolean;
  check?: boolean;
}) => {
  const { c, theme } = useTheme();
  const pills = options.map((o) => {
    const on = value === o.v;
    return (
      <Pressable
        key={o.v}
        onPress={() => onChange(o.v)}
        accessibilityRole="radio"
        accessibilityLabel={o.label}
        accessibilityState={{ selected: on }}
        style={{
          flex: scroll ? undefined : 1,
          alignItems: 'center',
          justifyContent: 'center',
          paddingHorizontal: check ? 10 : 16,
          paddingVertical: check ? 13 : 9,
          borderRadius: check ? 14 : 999,
          backgroundColor: on ? (check ? iconTint(c.brand, theme === 'dark') : c.brand) : c.surface3,
          borderWidth: check ? 1.5 : 0,
          borderColor: on ? c.brand : 'transparent',
          overflow: 'hidden',
        }}
      >
        <Text
          numberOfLines={1}
          style={{ fontSize: 13.5, fontFamily: font.bold, color: on ? (check ? c.brand : c.inkOnBrand) : c.ink3 }}
        >
          {o.label}
        </Text>
        {check && on && (
          // Notched into the corner the way a selected card is ticked elsewhere
          // in the app (ProviderGrid), so "chosen" reads the same everywhere.
          <View style={{ position: 'absolute', right: 0, bottom: 0, width: 22, height: 22, borderTopLeftRadius: 10, backgroundColor: c.brand, alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="check" size={11} color={c.inkOnBrand} stroke={3} />
          </View>
        )}
      </Pressable>
    );
  });
  if (!scroll) return <View style={{ flexDirection: 'row', gap: 10 }}>{pills}</View>;
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0 }} contentContainerStyle={{ gap: 8, paddingVertical: 2 }}>
      {pills}
    </ScrollView>
  );
};

// ---- Select row ----
// A field-shaped row that opens a picker rather than a keyboard. The app's
// convention (bank picker in sendmoney) is a closed row + a Sheet list, never
// the native Picker, so this is that pattern extracted once.
export const SelectRow = ({
  label,
  value,
  placeholder,
  onPress,
  icon,
  compact,
}: {
  label?: string;
  value?: string;
  placeholder?: string;
  onPress: () => void;
  icon?: string;
  compact?: boolean;
}) => {
  const { c } = useTheme();
  const filled = !!value;
  return (
    <View style={{ flex: compact ? 1 : undefined }}>
      {label ? <Text style={{ fontSize: 13, fontFamily: font.semibold, color: c.ink2, marginBottom: 8 }}>{label}</Text> : null}
      <Pressable
        onPress={onPress}
        accessibilityRole="button"
        accessibilityLabel={`${label ?? placeholder ?? 'Select'}${filled ? `, ${value}` : ''}`}
        style={({ pressed }) => ({
          flexDirection: 'row',
          alignItems: 'center',
          gap: 10,
          height: compact ? 48 : 56,
          paddingHorizontal: 16,
          borderRadius: compact ? 14 : radius.sm + 4,
          backgroundColor: compact ? c.surface3 : c.surface,
          borderWidth: compact ? 0 : 1.5,
          borderColor: c.line,
          opacity: pressed ? 0.85 : 1,
        })}
      >
        {icon ? <ZIcon name={icon} size={18} color={c.ink3} /> : null}
        <Text
          numberOfLines={1}
          style={{ flex: 1, fontSize: compact ? 14 : 15, fontFamily: filled ? font.semibold : font.regular, color: filled ? c.ink1 : c.ink3, textAlign: compact ? 'center' : 'left' }}
        >
          {value || placeholder}
        </Text>
        <ZIcon name="down" size={16} color={c.ink3} stroke={2.4} />
      </Pressable>
    </View>
  );
};

// ---- Picker sheet ----
// The list a SelectRow opens. Options carry an optional icon + sub-label so the
// same component serves a flat filter list and the file-type chooser.
export type PickerOption = { v: string; label: string; sub?: string; icon?: string };

type PickerSheetProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  options: PickerOption[];
  value: string;
  onPick: (v: string) => void;
  /** Show a filter box above the list. Opt-in, so a short list (two networks,
   *  three plans) does not grow a search field it does not need — but a long one
   *  (37 states) stops being a list you scroll and becomes one you type at. */
  searchable?: boolean;
  searchPlaceholder?: string;
  emptyLabel?: string;
};

/**
 * Deliberately a hook-free shell around the body, so a closed picker holds NO
 * state. The filter box then resets on every open for free, rather than needing
 * an effect to clear it — and reopening onto someone's last filter looks like the
 * list has lost most of its entries. This mirrors what `Sheet` does one level
 * down, and for the same reason.
 */
export const PickerSheet = (props: PickerSheetProps) => (props.open ? <PickerSheetBody {...props} /> : null);

const PickerSheetBody = ({
  open,
  onClose,
  title,
  options,
  value,
  onPick,
  searchable,
  searchPlaceholder,
  emptyLabel,
}: PickerSheetProps) => {
  const { c } = useTheme();
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  // Substring, not prefix: people look for "ibom" and "river" as readily as they
  // type the first letters, and a 37-item list is far too small for the
  // difference to cost anything.
  const shown = q
    ? options.filter((o) => o.label.toLowerCase().includes(q) || o.v.toLowerCase().includes(q))
    : options;
  return (
    <Sheet open={open} onClose={onClose} title={title}>
      {searchable ? (
        <View style={{ marginBottom: 6 }}>
          <Field
            value={query}
            onChangeText={setQuery}
            placeholder={searchPlaceholder ?? 'Type to search'}
            autoCapitalize="none"
            autoCorrect={false}
          />
        </View>
      ) : null}
      {searchable && shown.length === 0 ? (
        <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular, paddingVertical: 18, textAlign: 'center' }}>
          {emptyLabel ?? 'Nothing matches that.'}
        </Text>
      ) : null}
      {shown.map((o) => {
        const on = o.v === value;
        return (
          <Pressable
            key={o.v}
            onPress={() => { onPick(o.v); onClose(); }}
            accessibilityRole="radio"
            accessibilityState={{ selected: on }}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: c.line }}
          >
            {o.icon ? (
              <View style={{ width: 38, height: 38, borderRadius: 11, backgroundColor: c.surface3, alignItems: 'center', justifyContent: 'center' }}>
                <ZIcon name={o.icon} size={19} color={on ? c.brand : c.ink2} />
              </View>
            ) : null}
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={{ fontSize: 15, fontFamily: on ? font.bold : font.semibold, color: on ? c.brand : c.ink1 }}>{o.label}</Text>
              {o.sub ? <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{o.sub}</Text> : null}
            </View>
            {on ? <ZIcon name="check" size={18} color={c.brand} stroke={2.6} /> : null}
          </Pressable>
        );
      })}
    </Sheet>
  );
};

// ---- Progress bar ----
export const Progress = ({ value, max, tone }: { value: number; max: number; tone?: string }) => {
  const { c } = useTheme();
  // Clamped, not just divided: a limit raised mid-session (or a stale cached
  // max of 0) would otherwise render a bar wider than its own track.
  const pct = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  return (
    <View style={{ height: 9, borderRadius: 999, backgroundColor: c.surface3, overflow: 'hidden' }}>
      <View style={{ width: `${pct * 100}%`, height: '100%', borderRadius: 999, backgroundColor: tone ?? c.brand }} />
    </View>
  );
};

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
  /** The customer's own note for this payment, when they gave one. */
  narration?: string;
  /** Epoch ms parsed from the backend's date string, or undefined when it
   *  couldn't be read. Grouping by month needs a real instant; `detail` is a
   *  pre-formatted display string and cannot be sorted or bucketed. */
  ts?: number;
};

export const TxnRow = ({ txn, last, onPress }: { txn: Txn; last?: boolean; onPress?: () => void }) => {
  const { c, theme } = useTheme();
  const inflow = txn.dir === 'in';
  // Credits stay green; debits take their service's accent colour (airtime
  // teal, data blue, …) so transaction lists read colourful instead of flat
  // grey. Unmapped icons fall back to the neutral ink tone.
  const accent = inflow ? c.lime : (ICON_COLORS[txn.icon] ?? c.ink2);
  const tint = inflow ? 'rgba(0,181,29,.12)' : (ICON_COLORS[txn.icon] ? iconTint(ICON_COLORS[txn.icon], theme === 'dark') : c.surface3);
  const Wrap: any = onPress ? Pressable : View;
  return (
    <Wrap onPress={onPress} style={{ flexDirection: 'row', alignItems: 'center', gap: 13, paddingVertical: 13, borderBottomWidth: last ? 0 : 1, borderBottomColor: c.line }}>
      {/* A disc, not the rounded square used for service TILES: a tile is a
          thing you tap to start something, a transaction is a thing that already
          happened, and the two should not read as the same affordance. */}
      <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: tint, alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name={txn.icon} size={20} color={accent} stroke={2} />
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text numberOfLines={1} style={{ fontSize: 14.5, fontFamily: font.semibold, color: c.ink1 }}>{txn.type}</Text>
        <Text numberOfLines={1} style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{txn.detail}</Text>
      </View>
      <View style={{ alignItems: 'flex-end', gap: 4 }}>
        <NText style={{ fontSize: 14.5, fontFamily: font.bold, color: inflow ? c.lime : c.ink1, fontVariant: ['tabular-nums'] }}>
          {(inflow ? '+' : '-') + money(Math.abs(txn.amount))}
        </NText>
        <StatusPill status={txn.status} small />
      </View>
    </Wrap>
  );
};
