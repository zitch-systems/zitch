import React from 'react';
import { View, Text, Pressable, Image } from 'react-native';
import ZIcon from '@/components/design/ZIcon';
import { Sheet, Btn, Money, money } from '@/components/design/ui';
import { Naira, NText } from '@/components/design/Naira';
import { useTheme, font, radius } from '@/lib/theme';

// Network/provider id → brand color, for monograms & accents.
export const NET_COLORS: Record<string, string> = {
  '1': '#FFCC00', // MTN
  '2': '#2BB24C', // GLO
  '3': '#E40000', // Airtel
  '4': '#0A8A3D', // 9mobile
};

export const QUICK_AMOUNTS = [200, 500, 1000, 2000, 5000, 10000];

export const Label = ({ children }: { children: string }) => {
  const { c } = useTheme();
  return <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.ink1, marginTop: 6, marginBottom: 12 }}>{children}</Text>;
};

// Two-option pill toggle.
export const Segmented = ({
  options,
  value,
  onChange,
}: {
  options: { v: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', gap: 4, padding: 4, backgroundColor: c.surface3, borderRadius: 14, marginBottom: 18 }}>
      {options.map((o) => {
        const on = value === o.v;
        return (
          <Pressable key={o.v} onPress={() => onChange(o.v)} style={{ flex: 1 }}>
            <View style={{ alignItems: 'center', paddingVertical: 10, borderRadius: 11, backgroundColor: on ? c.surface : 'transparent' }}>
              <Text style={{ fontSize: 14, fontFamily: font.bold, color: on ? c.brand : c.ink3 }}>{o.label}</Text>
            </View>
          </Pressable>
        );
      })}
    </View>
  );
};

// 3-per-row quick amount chips.
export const QuickAmounts = ({ amounts, value, onPick }: { amounts: number[]; value: string; onPick: (a: string) => void }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -5, marginBottom: 12 }}>
      {amounts.map((a) => {
        const on = String(value) === String(a);
        return (
          <View key={a} style={{ width: '33.33%', padding: 5 }}>
            <Pressable
              onPress={() => onPick(String(a))}
              style={{ alignItems: 'center', paddingVertical: 13, borderRadius: 13, backgroundColor: on ? c.brand : c.surface, borderWidth: 1.5, borderColor: on ? c.brand : c.line }}
            >
              <Text style={{ fontSize: 15, fontFamily: font.bold, color: on ? '#fff' : c.ink1, fontVariant: ['tabular-nums'] }}><Naira />{a.toLocaleString()}</Text>
            </Pressable>
          </View>
        );
      })}
    </View>
  );
};

// Monogram disc with brand tint.
export const Monogram = ({ text, color, size = 44 }: { text: string; color: string; size?: number }) => (
  <View style={{ width: size, height: size, borderRadius: size * 0.32, backgroundColor: color + '22', alignItems: 'center', justifyContent: 'center' }}>
    <Text style={{ color, fontFamily: font.extrabold, fontSize: size * 0.32 }}>{text}</Text>
  </View>
);

// Selectable provider grid (4 per row) with check badge.
export const ProviderGrid = ({
  items,
  value,
  onPick,
  cols = 4,
}: {
  items: { id: string; name: string; color: string; logo?: any }[];
  value: string;
  onPick: (id: string) => void;
  cols?: number;
}) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -5, marginBottom: 18 }}>
      {items.map((it) => {
        const on = value === it.id;
        const initials = it.name.replace(/[^A-Za-z0-9 ]/g, '').split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
        return (
          <View key={it.id} style={{ width: `${100 / cols}%`, padding: 5 }}>
            <Pressable
              onPress={() => onPick(it.id)}
              style={{ alignItems: 'center', gap: 7, paddingVertical: 12, borderRadius: 16, backgroundColor: c.surface, borderWidth: 2, borderColor: on ? c.brand : c.line }}
            >
              {it.logo ? (
                <View style={{ width: '100%', height: 46, borderRadius: 12, backgroundColor: '#fff', borderWidth: 1, borderColor: c.line, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 6 }}>
                  <Image source={it.logo} resizeMode="contain" style={{ width: '100%', height: 32 }} />
                </View>
              ) : (
                <View style={{ width: 42, height: 42, borderRadius: 12, backgroundColor: it.color, alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ color: '#fff', fontFamily: font.extrabold, fontSize: 14 }}>{initials}</Text>
                </View>
              )}
              <Text numberOfLines={1} style={{ fontSize: 11, fontFamily: font.semibold, color: c.ink2, textAlign: 'center' }}>{it.name}</Text>
              {on && (
                <View style={{ position: 'absolute', top: 6, right: 6, width: 16, height: 16, borderRadius: 9, backgroundColor: c.brand, alignItems: 'center', justifyContent: 'center' }}>
                  <ZIcon name="check" size={10} color="#fff" stroke={3} />
                </View>
              )}
            </Pressable>
          </View>
        );
      })}
    </View>
  );
};

// Selectable plan list (label / sub / price).
export const PlanList = ({
  plans,
  value,
  onPick,
}: {
  plans: { id: string; label: string; sub?: string; price: number }[];
  value: string;
  onPick: (id: string) => void;
}) => {
  const { c } = useTheme();
  return (
    <View style={{ gap: 10 }}>
      {plans.map((p) => {
        const on = value === p.id;
        return (
          <Pressable
            key={p.id}
            onPress={() => onPick(p.id)}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14, paddingHorizontal: 16, borderRadius: 15, backgroundColor: c.surface, borderWidth: 2, borderColor: on ? c.brand : c.line }}
          >
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 15, fontFamily: font.bold, color: c.ink1 }}>{p.label}</Text>
              {p.sub ? <Text style={{ fontSize: 12.5, color: c.ink3, marginTop: 2, fontFamily: font.regular }}>{p.sub}</Text> : null}
            </View>
            <Text style={{ fontSize: 15, fontFamily: font.bold, color: on ? c.brand : c.ink1, fontVariant: ['tabular-nums'] }}><Naira />{p.price.toLocaleString()}</Text>
          </Pressable>
        );
      })}
    </View>
  );
};

// Balance hint / insufficient-funds warning under an amount field.
export const BalanceHint = ({ amount, balance }: { amount: number; balance: number }) => {
  const { c } = useTheme();
  const short = amount > 0 && amount > balance;
  if (short) {
    return (
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 2, marginBottom: 14 }}>
        <Text style={{ fontSize: 12, fontFamily: font.semibold, color: c.red }}>Insufficient balance</Text>
        <Text style={{ fontSize: 12, fontFamily: font.bold, color: c.brand }}>+ Add money</Text>
      </View>
    );
  }
  return (
    <Text style={{ textAlign: 'right', fontSize: 12, color: c.ink3, marginTop: 2, marginBottom: 14, fontFamily: font.regular }}>
      Balance: <NText style={{ fontFamily: font.bold, color: c.ink2 }}>{money(balance)}</NText>
    </Text>
  );
};

const Row2 = ({ k, v, strong }: { k: string; v: string; strong?: boolean }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 11, borderTopWidth: 1, borderTopColor: c.line }}>
      <Text style={{ fontSize: 14, color: c.ink3, fontFamily: font.regular }}>{k}</Text>
      <NText style={{ fontSize: strong ? 16 : 14, fontFamily: strong ? font.extrabold : font.semibold, color: c.ink1, fontVariant: ['tabular-nums'] }}>{v}</NText>
    </View>
  );
};

// Confirm sheet — review rows, wallet method, "Pay ₦…" CTA.
export const ConfirmSheet = ({
  open,
  onClose,
  title,
  total,
  rows,
  balance,
  onPay,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  total: number;
  rows: [string, string][];
  balance: number;
  onPay: () => void;
}) => {
  const { c } = useTheme();
  return (
    <Sheet open={open} onClose={onClose}>
      <View style={{ alignItems: 'center', marginBottom: 18 }}>
        <Text style={{ fontSize: 13, fontFamily: font.semibold, color: c.ink3 }}>{title}</Text>
        <Money amount={total} size={34} />
      </View>
      <View style={{ marginBottom: 18 }}>
        {rows.map((r, i) => <Row2 key={i} k={r[0]} v={r[1]} />)}
        <Row2 k="Fee" v="₦0" />
      </View>
      <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1, marginBottom: 10 }}>Pay with</Text>
      <View style={{ borderRadius: 14, backgroundColor: c.surface2, borderWidth: 1.5, borderColor: c.line, padding: 14, marginBottom: 18 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <View style={{ width: 38, height: 38, borderRadius: 11, backgroundColor: 'rgba(15,162,149,.14)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="wallet" size={20} color={c.brand} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.ink1 }}>Zitch Wallet</Text>
            <NText style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>Available {money(balance)}</NText>
          </View>
          <ZIcon name="check" size={18} color={c.brand} />
        </View>
      </View>
      <Btn label={`Pay ${money(total)}`} icon="lock" onPress={onPay} />
    </Sheet>
  );
};
