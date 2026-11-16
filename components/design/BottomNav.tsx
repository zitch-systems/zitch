import React from 'react';
import { View, Text, Pressable } from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import ZIcon from '@/components/design/ZIcon';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { WHATSAPP_GREEN } from '@/lib/whatsappGlyph';
import { useTheme, font } from '@/lib/theme';
import { BANKING_TABS, selectedBankingTab } from '@/components/navigation/bankingTabs';

type TabItem = typeof BANKING_TABS[number];

// Idle tabs read in actual black on the light theme (review, Nov 2026); on the dark
// theme the same role falls to the brightest ink so they stay legible.
export const useTabInk = () => {
  const { c, theme } = useTheme();
  return theme === 'dark' ? c.ink1 : '#000000';
};

// One menu on every phone tab (review, Nov 2026): the floating bar from Home
// design A. A rounded card that floats over the page background, four tabs, and
// a raised round WhatsApp button in the middle. Every slot has the same 24 px
// icon row, so the labels line up whether or not the button rises above it.
export const DOCK = {
  sidePad: 16,
  topPad: 22, // room for the raised button, which rises ~20 px above the bar
  bottomPad: 10,
  radius: 26,
  iconRow: 24,
  icon: 22,
  fab: 56,
  fabRise: 32,
  label: 11,
} as const;

const BankingTab = ({ item, state, navigation }: BottomTabBarProps & { item: TabItem }) => {
  const { c } = useTheme();
  const idle = useTabInk();
  const current = state.routes[state.index]?.name;
  const selected = selectedBankingTab(current) === item.name;
  const target = state.routes.find((route) => route.name === item.name)?.key;
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityLabel={item.label}
      accessibilityState={{ selected }}
      aria-selected={selected}
      hitSlop={6}
      onPress={() => {
        const event = navigation.emit({ type: 'tabPress', target, canPreventDefault: true });
        // A detail page highlights its parent; tapping it returns to the main tab.
        if (current !== item.name && !event.defaultPrevented) navigation.navigate(item.name as never);
      }}
      onLongPress={() => navigation.emit({ type: 'tabLongPress', target })}
      style={({ pressed }) => ({ flex: 1, minWidth: 0, alignItems: 'center', gap: 5, opacity: pressed ? 0.66 : 1 })}
    >
      <View style={{ height: DOCK.iconRow, alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name={item.icon} size={DOCK.icon} color={selected ? c.brand : idle} stroke={selected ? 2.2 : 1.9} />
      </View>
      <Text numberOfLines={1} style={{ fontSize: DOCK.label, fontFamily: selected ? font.bold : font.semibold, color: selected ? c.brand : idle, textAlign: 'center' }}>{item.label}</Text>
    </Pressable>
  );
};

const WhatsAppButton = () => {
  const { c } = useTheme();
  return (
    <Pressable
      onPress={() => router.push('/linkwhatsapp')}
      accessibilityRole="button"
      accessibilityLabel="Bank on WhatsApp"
      hitSlop={6}
      style={({ pressed }) => ({ flex: 1.15, minWidth: 0, alignItems: 'center', gap: 5, opacity: pressed ? 0.66 : 1 })}
    >
      <View style={{ height: DOCK.iconRow, width: DOCK.fab, alignItems: 'center' }}>
        <View style={{
          width: DOCK.fab, height: DOCK.fab, borderRadius: DOCK.fab / 2, marginTop: -DOCK.fabRise,
          backgroundColor: WHATSAPP_GREEN, borderWidth: 4, borderColor: c.surface,
          alignItems: 'center', justifyContent: 'center',
          shadowColor: '#128C7E', shadowOpacity: 0.45, shadowRadius: 9, shadowOffset: { width: 0, height: 8 }, elevation: 6,
        }}>
          <WhatsAppGlyph size={26} color="#fff" />
        </View>
      </View>
      <Text numberOfLines={2} style={{ fontSize: DOCK.label, lineHeight: DOCK.label + 3, fontFamily: font.semibold, color: c.brand, textAlign: 'center' }}>WhatsApp banking</Text>
    </Pressable>
  );
};

const BottomNav = (props: BottomTabBarProps) => {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  return (
    <View style={{ backgroundColor: c.bg, paddingTop: DOCK.topPad, paddingHorizontal: DOCK.sidePad, paddingBottom: Math.max(insets.bottom, DOCK.bottomPad) }}>
      <View style={{
        flexDirection: 'row', alignItems: 'flex-start',
        backgroundColor: c.surface, borderWidth: 1.5, borderColor: c.line, borderRadius: DOCK.radius,
        paddingTop: 11, paddingBottom: 10, paddingHorizontal: 8,
        shadowColor: '#00847B', shadowOpacity: 0.18, shadowRadius: 17, shadowOffset: { width: 0, height: 12 }, elevation: 8,
      }}>
        {BANKING_TABS.slice(0, 2).map((item) => <BankingTab key={item.name} {...props} item={item} />)}
        <WhatsAppButton />
        {BANKING_TABS.slice(2).map((item) => <BankingTab key={item.name} {...props} item={item} />)}
      </View>
    </View>
  );
};

export default BottomNav;
