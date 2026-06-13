import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  ScrollView,
  Modal,
  StyleSheet,
  ViewStyle,
  TextStyle,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { SafeAreaView } from 'react-native-safe-area-context';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font, radius, ThemeTokens } from '@/lib/theme';
import { money as fmtMoney, moneyk as fmtMoneyk } from '@/lib/format';

export const money = fmtMoney;
export const moneyk = fmtMoneyk;

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
}: {
  children: React.ReactNode;
  pad?: boolean;
  scroll?: boolean;
  tab?: boolean;
}) => {
  const { c } = useTheme();
  const bottomPad = tab ? 96 : 28;
  return (
    <LinearGradient colors={c.bgGradient} style={{ flex: 1 }}>
      <SafeAreaView style={{ flex: 1 }} edges={['top']}>
        {scroll ? (
          <ScrollView
            showsVerticalScrollIndicator={false}
            contentContainerStyle={{ paddingHorizontal: pad ? 20 : 0, paddingBottom: bottomPad }}
          >
            {children}
          </ScrollView>
        ) : (
          <View style={{ flex: 1, paddingHorizontal: pad ? 20 : 0 }}>{children}</View>
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
          <Text style={{ fontSize: 19, fontFamily: font.extrabold, color: c.ink1, letterSpacing: -0.2 }}>{title}</Text>
          {sub && <Text style={{ fontSize: 13, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{sub}</Text>}
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
  if (onPress) return <Pressable onPress={onPress} style={({ pressed }) => [base, pressed && { opacity: 0.9 }]}>{children}</Pressable>;
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
      <Text style={{ color: v.fg, fontFamily: font.bold, fontSize: btnFont[size] }}>{label}</Text>
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
    <Text
      style={{
        fontSize: size,
        fontFamily: font.extrabold,
        color: color || c.ink1,
        letterSpacing: -0.5,
        fontVariant: ['tabular-nums'],
      }}
    >
      {showk ? moneyk(amount) : money(amount)}
    </Text>
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
  const { c } = useTheme();
  const Wrap: any = onPress ? Pressable : View;
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
        <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: iconBg || c.surface3, alignItems: 'center', justifyContent: 'center' }}>
          <ZIcon name={icon} size={21} color={iconColor || c.brand} stroke={2} />
        </View>
      )}
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text numberOfLines={1} style={{ fontSize: 15, fontFamily: font.semibold, color: c.ink1 }}>{title}</Text>
        {sub && <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{sub}</Text>}
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
}) => {
  const { c } = useTheme();
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
        <TextInput
          editable={editable}
          value={value}
          onChangeText={onChangeText}
          placeholder={placeholder}
          placeholderTextColor={c.ink3}
          keyboardType={keyboardType}
          secureTextEntry={secureTextEntry}
          maxLength={maxLength}
          style={{ flex: 1, fontSize: 16, color: c.ink1, fontFamily: font.medium }}
        />
        {suffix}
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
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable onPress={onClose} style={{ flex: 1, backgroundColor: 'rgba(2,16,14,.5)' }} />
      <View
        style={{
          backgroundColor: c.surface,
          borderTopLeftRadius: 28,
          borderTopRightRadius: 28,
          padding: 20,
          paddingTop: 10,
          paddingBottom: 26,
          maxHeight: '88%',
        }}
      >
        <View style={{ width: 40, height: 5, borderRadius: 3, backgroundColor: c.line, alignSelf: 'center', marginBottom: 14 }} />
        {title && <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1, marginBottom: 14 }}>{title}</Text>}
        <ScrollView showsVerticalScrollIndicator={false}>{children}</ScrollView>
      </View>
    </Modal>
  );
};

// ---- PIN entry ----
export const PinPad = ({ onComplete, length = 4, busy = false, error }: { onComplete?: (pin: string) => void; length?: number; busy?: boolean; error?: string }) => {
  const { c } = useTheme();
  const [pin, setPin] = useState('');
  const press = (d: string) => {
    if (busy) return; // ignore input while a submission is in flight (prevents double-charge)
    if (pin.length < length) {
      const np = pin + d;
      setPin(np);
      if (np.length === length) setTimeout(() => { onComplete && onComplete(np); setPin(''); }, 120);
    }
  };
  const del = () => { if (!busy) setPin((p) => p.slice(0, -1)); };
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
            <View key={i} style={{ width: '33.33%', height: 64 }} />
          ) : (
            <View key={i} style={{ width: '33.33%', padding: 7 }}>
              <Pressable
                onPress={() => (k === 'del' ? del() : press(k))}
                style={{
                  height: 64,
                  borderRadius: 18,
                  backgroundColor: c.surface,
                  borderWidth: 1,
                  borderColor: c.line,
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                {k === 'del' ? (
                  <ZIcon name="left" size={24} color={c.ink1} />
                ) : (
                  <Text style={{ fontSize: 24, fontFamily: font.bold, color: c.ink1 }}>{k}</Text>
                )}
              </Pressable>
            </View>
          )
        )}
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
  <Pressable
    onPress={onPress}
    style={{ flexDirection: 'row', alignItems: 'center', gap: 7, paddingVertical: 9, paddingHorizontal: 14, backgroundColor: 'rgba(255,255,255,.16)', borderRadius: 999 }}
  >
    <ZIcon name={icon} size={16} color="#fff" stroke={2.2} />
    <Text style={{ color: '#fff', fontSize: 13, fontFamily: font.semibold }}>{label}</Text>
  </Pressable>
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

export const TxnRow = ({ txn, last, onPress }: { txn: Txn; last?: boolean; onPress?: () => void }) => {
  const { c } = useTheme();
  const inflow = txn.dir === 'in';
  const Wrap: any = onPress ? Pressable : View;
  return (
    <Wrap onPress={onPress} style={{ flexDirection: 'row', alignItems: 'center', gap: 13, paddingVertical: 13, borderBottomWidth: last ? 0 : 1, borderBottomColor: c.line }}>
      <View style={{ width: 44, height: 44, borderRadius: 13, backgroundColor: inflow ? 'rgba(0,181,29,.12)' : c.surface3, alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name={txn.icon} size={20} color={inflow ? c.lime : c.ink2} stroke={2} />
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={{ fontSize: 14.5, fontFamily: font.semibold, color: c.ink1 }}>{txn.type}</Text>
        <Text numberOfLines={1} style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{txn.detail}</Text>
      </View>
      <View style={{ alignItems: 'flex-end' }}>
        <Text style={{ fontSize: 14.5, fontFamily: font.bold, color: inflow ? c.lime : c.ink1, fontVariant: ['tabular-nums'] }}>
          {(inflow ? '+' : '-') + money(Math.abs(txn.amount))}
        </Text>
        <Text style={{ fontSize: 11.5, color: txn.status === 'Pending' ? c.amber : c.ink3, marginTop: 2, fontFamily: font.regular }}>{txn.status}</Text>
      </View>
    </Wrap>
  );
};
