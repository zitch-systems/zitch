import React from 'react';
import { View, Text, Pressable } from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import ZIcon from '@/components/design/ZIcon';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { useTheme, font } from '@/lib/theme';
import { BANKING_TABS, selectedBankingTab } from '@/components/navigation/bankingTabs';

type TabItem = typeof BANKING_TABS[number];

const BankingTab = ({ item, state, navigation }: BottomTabBarProps & { item: TabItem }) => {
  const { c } = useTheme();
  const current = state.routes[state.index]?.name;
  const selected = selectedBankingTab(current) === item.name;
  const target = state.routes.find((route) => route.name === item.name)?.key;
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityLabel={item.label}
      accessibilityState={{ selected }}
      aria-selected={selected}
      onPress={() => {
        const event = navigation.emit({ type: 'tabPress', target, canPreventDefault: true });
        // A detail page highlights its parent; tapping it returns to the main tab.
        if (current !== item.name && !event.defaultPrevented) navigation.navigate(item.name as never);
      }}
      onLongPress={() => navigation.emit({ type: 'tabLongPress', target })}
      style={({ pressed }) => ({ flex: 1, minWidth: 0, minHeight: 64, alignItems: 'center', gap: 4, paddingHorizontal: 2, opacity: pressed ? 0.75 : 1 })}
    >
      <View style={{ minWidth: 46, height: 38, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: selected ? c.surface3 : 'transparent' }}>
        <ZIcon name={item.icon} size={24} color={selected ? c.brand : c.ink3} stroke={selected ? 2.2 : 1.8} />
      </View>
      <Text style={{ fontSize: 11.5, fontFamily: selected ? font.bold : font.medium, color: selected ? c.brand : c.ink3, textAlign: 'center' }}>{item.label}</Text>
    </Pressable>
  );
};

const BottomNav = (props: BottomTabBarProps) => {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  return (
    <View style={{ backgroundColor: c.surface, borderTopWidth: 1, borderTopColor: c.line, paddingBottom: insets.bottom }}>
      <View style={{ flexDirection: 'row', alignItems: 'stretch', paddingTop: 8, paddingBottom: 4, paddingHorizontal: 8 }}>
        {BANKING_TABS.slice(0, 2).map((item) => <BankingTab key={item.name} {...props} item={item} />)}
        <Pressable onPress={() => router.push('/linkwhatsapp')} accessibilityRole="button" accessibilityLabel="Bank on WhatsApp"
          style={({ pressed }) => ({ flex: 1, minWidth: 0, minHeight: 64, alignItems: 'center', gap: 4, paddingHorizontal: 2, opacity: pressed ? 0.75 : 1 })}>
          <View style={{ width: 38, height: 38, borderRadius: 14, backgroundColor: '#128C7E', alignItems: 'center', justifyContent: 'center' }}>
            <WhatsAppGlyph size={25} color="#fff" />
          </View>
          <Text style={{ fontSize: 11.5, fontFamily: font.semibold, color: c.ink2, textAlign: 'center' }}>WhatsApp</Text>
        </Pressable>
        {BANKING_TABS.slice(2).map((item) => <BankingTab key={item.name} {...props} item={item} />)}
      </View>
    </View>
  );
};

export default BottomNav;
