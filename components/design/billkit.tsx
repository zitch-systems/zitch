// Pieces shared by the bill screens (Airtime, Data, Cable TV, Electricity,
// Betting, Exams), as reviewed on the Live UI page (Nov 2026): network circles,
// fields on a soft tint with no border, the "Last used" / "My number" field with
// its list of recent values, and the amount and plan grids.
import React, { useRef, useState } from 'react';
import { View, Text, Pressable, Image, TextInput, ScrollView } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import ZIcon from '@/components/design/ZIcon';
import { Naira } from '@/components/design/Naira';
import { money } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

export const NETWORK_LOGOS: Record<string, any> = {
  '1': require('@/assets/images/providers/mtn.png'),
  '2': require('@/assets/images/providers/glo.png'),
  '3': require('@/assets/images/providers/airtel.png'),
  '4': require('@/assets/images/providers/9mobile.png'),
};
export const NETWORK_LABELS: Record<string, string> = { '1': 'MTN', '2': 'Glo', '3': 'Airtel', '4': '9mobile' };

/** The soft brand tint the bill fields sit on, in place of a border. */
export const useFieldTint = () => {
  const { theme } = useTheme();
  return theme === 'dark' ? 'rgba(234,251,247,0.07)' : 'rgba(6,110,102,0.07)';
};

export const SectionTitle = ({ children, top = 0 }: { children: string; top?: number }) => {
  const { c } = useTheme();
  return <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.ink1, marginTop: top, marginBottom: 10 }}>{children}</Text>;
};

export const Tick = ({ size = 18, bg }: { size?: number; bg?: string }) => {
  const { c } = useTheme();
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: bg || c.brand, alignItems: 'center', justifyContent: 'center' }}>
      <ZIcon name="check" size={Math.round(size * 0.62)} color="#fff" stroke={3.2} />
    </View>
  );
};

/** A network's logo with no white ring: MTN and Glo fill their own round badge
 *  (Glo's white square is cropped away); Airtel and 9mobile sit as they are. */
export const NetDot = ({ net, size = 30 }: { net: string; size?: number }) => {
  const logo = NETWORK_LOGOS[net];
  if (!logo) return <View style={{ width: size, height: size }} />;
  const fill = net === '1' ? 1 : net === '2' ? 220 / 186 : 0;
  if (fill) {
    const s = Math.round(size * fill);
    return (
      <View style={{ width: size, height: size, borderRadius: size / 2, overflow: 'hidden', alignItems: 'center', justifyContent: 'center' }}>
        <Image source={logo} resizeMode="cover" style={{ width: s, height: s }} />
      </View>
    );
  }
  return (
    <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
      <Image source={logo} resizeMode="contain" style={{ width: Math.round(size * 0.86), height: Math.round(size * 0.86) }} />
    </View>
  );
};

/** Four networks as round logos; the chosen one gets a teal ring and a tick. */
export const NetworkCircles = ({ value, onPick }: { value: string; onPick: (id: string) => void }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: 6, marginBottom: 22 }}>
      {['1', '2', '3', '4'].map((id) => {
        const on = id === value;
        return (
          <Pressable
            key={id}
            onPress={() => onPick(id)}
            accessibilityRole="radio"
            accessibilityLabel={NETWORK_LABELS[id]}
            accessibilityState={{ selected: on }}
            style={{ alignItems: 'center', gap: 8, width: 68 }}
          >
            <View style={{
              width: 60, height: 60, borderRadius: 30, backgroundColor: c.surface, alignItems: 'center', justifyContent: 'center',
              borderWidth: on ? 2.5 : 0, borderColor: c.brand,
              shadowColor: '#063731', shadowOpacity: 0.1, shadowRadius: 7, shadowOffset: { width: 0, height: 6 }, elevation: 3,
            }}>
              <Image source={NETWORK_LOGOS[id]} resizeMode="contain" style={{ width: 40, height: 40 }} />
            </View>
            {on ? <View style={{ position: 'absolute', top: -2, right: 2 }}><Tick size={19} /></View> : null}
            <Text style={{ fontSize: 12, fontFamily: on ? font.extrabold : font.semibold, color: on ? c.brand : c.ink2 }}>{NETWORK_LABELS[id]}</Text>
          </Pressable>
        );
      })}
    </View>
  );
};

export type RecentItem = {
  /** What goes into the field when picked. */
  value: string;
  /** How it reads in the list (defaults to value). */
  title?: string;
  sub?: string;
  leading?: React.ReactNode;
};

/**
 * A field that opens with the last value used and lists the others under it:
 * "0801 234 5678 · My number" on Airtime, "Last used" on meters, smartcards and
 * betting IDs. The chevron opens the list; "Use another …" clears the field for
 * typing. With no recent values it is a plain field on the soft tint.
 */
export const RecentField = ({
  label,
  value,
  onChangeText,
  onPick,
  items,
  menuTitle,
  anotherLabel,
  tag,
  leading,
  placeholder,
  keyboardType = 'number-pad',
  maxLength,
  format,
  accessibilityLabel,
  below,
}: {
  label: string;
  value: string;
  onChangeText: (v: string) => void;
  onPick: (item: RecentItem) => void;
  items: RecentItem[];
  menuTitle: string;
  anotherLabel: string;
  tag?: string;
  leading?: React.ReactNode;
  placeholder?: string;
  keyboardType?: any;
  maxLength?: number;
  /** Display form while the field is not being edited (e.g. grouped digits). */
  format?: (v: string) => string;
  accessibilityLabel?: string;
  /** Shown under the field (a resolved name, an error). */
  below?: React.ReactNode;
}) => {
  const { c } = useTheme();
  const tint = useFieldTint();
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const input = useRef<TextInput>(null);
  const active = open || focused;
  const shown = focused || !format ? value : format(value);
  return (
    <View style={{ marginBottom: 4 }}>
      <SectionTitle>{label}</SectionTitle>
      <View style={{
        flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 58, paddingLeft: leading ? 12 : 16, paddingRight: items.length ? 6 : 16,
        borderRadius: 18, backgroundColor: active ? c.surface : tint, borderWidth: 2, borderColor: active ? c.brand : 'transparent',
      }}>
        {leading}
        <View style={{ flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <TextInput
            ref={input}
            value={shown}
            onChangeText={(v) => onChangeText(v)}
            onFocus={() => { setFocused(true); setOpen(false); }}
            onBlur={() => setFocused(false)}
            placeholder={placeholder}
            placeholderTextColor={c.ink3}
            keyboardType={keyboardType}
            maxLength={maxLength}
            accessibilityLabel={accessibilityLabel || label}
            style={{ flexShrink: 1, minWidth: 60, paddingVertical: 0, fontSize: 15.5, color: c.ink1, fontFamily: font.bold, fontVariant: ['tabular-nums'] }}
          />
          {tag && value && !focused ? (
            <View style={{ paddingVertical: 3, paddingHorizontal: 8, borderRadius: 999, backgroundColor: 'rgba(6,110,102,0.12)' }}>
              <Text style={{ fontSize: 11, fontFamily: font.bold, color: c.brand }}>{tag}</Text>
            </View>
          ) : null}
        </View>
        {items.length ? (
          <Pressable
            onPress={() => { input.current?.blur(); setOpen((o) => !o); }}
            accessibilityRole="button"
            accessibilityLabel={menuTitle}
            accessibilityState={{ expanded: open }}
            hitSlop={4}
            style={{ width: 38, height: 38, borderRadius: 12, alignItems: 'center', justifyContent: 'center' }}
          >
            <ZIcon name={open ? 'up' : 'down'} size={20} color={c.ink1} />
          </Pressable>
        ) : null}
      </View>
      {below}
      {open ? (
        <View style={{
          marginTop: 8, borderRadius: 20, backgroundColor: c.surface, paddingTop: 8, paddingHorizontal: 6, paddingBottom: 6,
          shadowColor: '#063731', shadowOpacity: 0.18, shadowRadius: 20, shadowOffset: { width: 0, height: 18 }, elevation: 8,
        }}>
          <Text style={{ fontSize: 11.5, fontFamily: font.bold, color: c.ink3, letterSpacing: 0.3, paddingTop: 8, paddingHorizontal: 12, paddingBottom: 6 }}>{menuTitle.toUpperCase()}</Text>
          {items.map((it) => {
            const on = it.value === value;
            return (
              <Pressable
                key={it.value}
                onPress={() => { onPick(it); setOpen(false); }}
                accessibilityRole="button"
                accessibilityLabel={[it.title || it.value, it.sub].filter(Boolean).join(', ')}
                accessibilityState={{ selected: on }}
                style={({ pressed }) => ({ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 10, paddingHorizontal: 12, borderRadius: 14, backgroundColor: on ? tint : pressed ? c.surface2 : 'transparent' })}
              >
                {it.leading}
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text numberOfLines={1} style={{ fontSize: 14.5, fontFamily: font.bold, color: c.ink1, fontVariant: ['tabular-nums'] }}>{it.title || it.value}</Text>
                  {it.sub ? <Text numberOfLines={1} style={{ fontSize: 12, marginTop: 2, fontFamily: font.regular, color: c.ink3 }}>{it.sub}</Text> : null}
                </View>
                {on ? <Tick size={20} /> : null}
              </Pressable>
            );
          })}
          <View style={{ height: 1, backgroundColor: c.line, marginVertical: 6, marginHorizontal: 12 }} />
          <Pressable
            onPress={() => { onChangeText(''); setOpen(false); setTimeout(() => input.current?.focus(), 50); }}
            accessibilityRole="button"
            accessibilityLabel={anotherLabel}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10, paddingHorizontal: 12 }}
          >
            <ZIcon name="plus" size={18} color={c.brand} />
            <Text style={{ fontSize: 14, fontFamily: font.bold, color: c.brand }}>{anotherLabel}</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
};

const rowsOf = <T,>(list: T[], n: number): T[][] => {
  const out: T[][] = [];
  for (let i = 0; i < list.length; i += n) out.push(list.slice(i, i + n));
  return out;
};

/** Preset amounts as a 3-across grid of tiles; the chosen one is filled teal. */
export const AmountGrid = ({ amounts, value, onPick }: { amounts: number[]; value: string; onPick: (a: string) => void }) => {
  const { c } = useTheme();
  const tint = useFieldTint();
  return (
    <View style={{ gap: 10 }}>
      {rowsOf(amounts, 3).map((row, r) => (
        <View key={r} style={{ flexDirection: 'row', gap: 10 }}>
          {row.map((a) => {
            const on = String(value) === String(a);
            return (
              <Pressable
                key={a}
                onPress={() => onPick(String(a))}
                accessibilityRole="radio"
                accessibilityLabel={`Select ${money(a)}`}
                accessibilityState={{ selected: on }}
                style={{ flex: 1, height: 56, borderRadius: 16, alignItems: 'center', justifyContent: 'center', backgroundColor: on ? c.brand : tint }}
              >
                <Text style={{ fontSize: 16, fontFamily: font.extrabold, color: on ? c.inkOnBrand : c.ink1, fontVariant: ['tabular-nums'] }}>
                  <Naira />{a.toLocaleString()}
                </Text>
              </Pressable>
            );
          })}
          {row.length < 3 ? Array.from({ length: 3 - row.length }, (_, i) => <View key={`f${i}`} style={{ flex: 1 }} />) : null}
        </View>
      ))}
    </View>
  );
};

/** Plan types (SME, Gifting…) as pills. */
export const PillTabs = ({ options, value, onChange }: { options: { v: string; label: string }[]; value: string; onChange: (v: string) => void }) => {
  const { c } = useTheme();
  const tint = useFieldTint();
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {options.map((o) => {
        const on = o.v === value;
        return (
          <Pressable
            key={o.v}
            onPress={() => onChange(o.v)}
            accessibilityRole="radio"
            accessibilityLabel={o.label}
            accessibilityState={{ selected: on }}
            style={{ paddingVertical: 9, paddingHorizontal: 16, borderRadius: 999, backgroundColor: on ? c.brand : tint }}
          >
            <Text style={{ fontSize: 13.5, fontFamily: font.bold, color: on ? c.inkOnBrand : c.ink2 }}>{o.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
};

/** Data plans as a 2-across grid: size, validity, price. */
export const PlanGrid = ({ plans, value, onPick }: {
  plans: { id: string; label: string; sub?: string; price: number }[];
  value: string;
  onPick: (id: string) => void;
}) => {
  const { c } = useTheme();
  const tint = useFieldTint();
  return (
    <View style={{ gap: 10 }}>
      {rowsOf(plans, 2).map((row, r) => (
        <View key={r} style={{ flexDirection: 'row', gap: 10 }}>
          {row.map((p) => {
            const on = p.id === value;
            return (
              <Pressable
                key={p.id}
                onPress={() => onPick(p.id)}
                accessibilityRole="radio"
                accessibilityLabel={`${p.label}${p.sub ? ` for ${p.sub}` : ''}, ${money(p.price)}`}
                accessibilityState={{ selected: on }}
                style={{ flex: 1, paddingVertical: 14, paddingHorizontal: 16, borderRadius: 18, backgroundColor: on ? c.brand : tint }}
              >
                <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
                  <Text numberOfLines={1} style={{ flexShrink: 1, fontSize: 19, fontFamily: font.extrabold, color: on ? c.inkOnBrand : c.ink1 }}>{p.label}</Text>
                  {on ? <Tick size={18} bg="rgba(255,255,255,0.25)" /> : null}
                </View>
                {p.sub ? <Text numberOfLines={1} style={{ fontSize: 12, marginTop: 2, fontFamily: font.medium, color: on ? 'rgba(255,255,255,0.8)' : c.ink3 }}>{p.sub}</Text> : null}
                <Text style={{ fontSize: 14.5, marginTop: 8, fontFamily: font.extrabold, color: on ? c.inkOnBrand : c.brand, fontVariant: ['tabular-nums'] }}>
                  <Naira />{p.price.toLocaleString()}
                </Text>
              </Pressable>
            );
          })}
          {row.length < 2 ? <View style={{ flex: 1 }} /> : null}
        </View>
      ))}
    </View>
  );
};

/** Discos in one row that scrolls sideways, with a round button at the right
 *  edge that moves it along (review, Nov 2026: nine tiles no longer take three
 *  rows of the screen). */
export const DiscoRow = ({ items, value, onPick }: {
  items: { id: string; name: string; color: string }[];
  value: string;
  onPick: (id: string) => void;
}) => {
  const { c } = useTheme();
  const scroller = useRef<any>(null);
  const [x, setX] = useState(0);
  const [room, setRoom] = useState({ content: 0, view: 0 });
  const atEnd = room.content > 0 && x + room.view >= room.content - 4;
  const TILE = 108;
  return (
    <View style={{ marginBottom: 18, marginRight: -20 }}>
      <ScrollView
        ref={scroller}
        horizontal
        showsHorizontalScrollIndicator={false}
        onScroll={(e) => setX(e.nativeEvent.contentOffset.x)}
        scrollEventThrottle={32}
        onContentSizeChange={(w) => setRoom((r) => ({ ...r, content: w }))}
        onLayout={(e) => { const w = e.nativeEvent.layout.width; setRoom((r) => ({ ...r, view: w })); }}
        contentContainerStyle={{ gap: 10, paddingRight: 56 }}
      >
        {items.map((it) => {
          const on = it.id === value;
          const initials = it.name.replace(/[^A-Za-z ]/g, '').split(' ').map((w) => w[0] || '').join('').slice(0, 2).toUpperCase();
          return (
            <Pressable
              key={it.id}
              onPress={() => onPick(it.id)}
              accessibilityRole="radio"
              accessibilityLabel={it.name}
              accessibilityState={{ selected: on }}
              style={{ width: TILE, alignItems: 'center', gap: 7, paddingVertical: 12, borderRadius: 16, backgroundColor: on ? 'rgba(6,110,102,0.10)' : c.surface, borderWidth: on ? 2 : 0, borderColor: c.brand }}
            >
              <View style={{ width: 42, height: 42, borderRadius: 12, backgroundColor: it.color, alignItems: 'center', justifyContent: 'center' }}>
                <Text style={{ color: '#fff', fontFamily: font.extrabold, fontSize: 14 }}>{initials}</Text>
              </View>
              <Text numberOfLines={1} style={{ fontSize: 11, fontFamily: font.semibold, color: c.ink2, textAlign: 'center', paddingHorizontal: 4 }}>{it.name}</Text>
              {on ? <View style={{ position: 'absolute', top: 6, right: 6 }}><Tick size={16} /></View> : null}
            </Pressable>
          );
        })}
      </ScrollView>
      {atEnd ? null : (
        <View pointerEvents="box-none" style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: 76, justifyContent: 'center', alignItems: 'flex-end', paddingRight: 14 }}>
          <LinearGradient pointerEvents="none" colors={[`${c.bg}00`, `${c.bg}EB`, c.bg]} locations={[0, 0.45, 1]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }} />
          <Pressable
            onPress={() => scroller.current?.scrollTo({ x: x + TILE * 2 + 20, animated: true })}
            accessibilityRole="button"
            accessibilityLabel="Scroll to see more discos"
            style={{ width: 38, height: 38, borderRadius: 19, backgroundColor: c.surface, alignItems: 'center', justifyContent: 'center', shadowColor: '#063731', shadowOpacity: 0.16, shadowRadius: 7, shadowOffset: { width: 0, height: 6 }, elevation: 4 }}
          >
            <ZIcon name="right" size={18} color={c.brand} stroke={2.4} />
          </Pressable>
        </View>
      )}
    </View>
  );
};

/** The account a bill will go to, once the number checks out: name in bold,
 *  address under it, on a soft green. */
export const ResolvedAccount = ({ name, detail }: { name: string; detail?: string }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginTop: 10, marginBottom: 6, paddingVertical: 10, paddingHorizontal: 12, borderRadius: 14, backgroundColor: 'rgba(0,181,29,0.08)' }}>
      <View style={{ marginTop: 1 }}><Tick size={18} bg="#00A01A" /></View>
      <View style={{ flex: 1 }}>
        <Text style={{ fontSize: 13, fontFamily: font.extrabold, color: c.ink1, letterSpacing: 0.2 }}>{name}</Text>
        {detail ? <Text style={{ fontSize: 12, marginTop: 2, lineHeight: 17, fontFamily: font.regular, color: c.ink3 }}>{detail}</Text> : null}
      </View>
    </View>
  );
};
