import React from 'react';
import { View, Text, Pressable, ScrollView } from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import ZIcon from '@/components/design/ZIcon';
import { ZMark, ZWordmark, Avatar } from '@/components/design/Brand';
import { useTheme, font } from '@/lib/theme';
import { useRailWidth } from '@/lib/device';
import { BANKING_TABS, selectedBankingTab } from '@/components/navigation/bankingTabs';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';

const ITEMS = BANKING_TABS;

/**
 * Left navigation rail for fold/tablet — replaces the bottom nav on wide
 * screens (logo, nav items with active highlight, profile footer).
 */
const Sidebar = ({ state, navigation, width }: BottomTabBarProps & { width?: number }) => {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const fallbackW = useRailWidth();
  const activeName = state.routes[state.index]?.name;
  const railW = width || fallbackW || 200;

  return (
    // Pinned to the left edge as a full-height rail. bottom-tabs renders the
    // tabBar at the bottom of a column, so absolute positioning is what lifts it
    // into a side rail; the scene is padded by railW in the Tabs layout.
    <View style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: railW, backgroundColor: c.surface, borderRightWidth: 1, borderRightColor: c.line, paddingTop: insets.top + 18, paddingHorizontal: 16, paddingBottom: 18, zIndex: 20 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 8, paddingBottom: 22 }}>
        <ZMark size={26} />
        <ZWordmark size={17} color={c.ink1} />
      </View>

      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ gap: 4, paddingBottom: 16 }} showsVerticalScrollIndicator={false}>
        {ITEMS.map((it) => {
          const on = selectedBankingTab(activeName) === it.name;
          return (
            <Pressable
              key={it.name}
              onPress={() => {
                const event = navigation.emit({ type: 'tabPress', target: state.routes.find((route) => route.name === it.name)?.key ?? it.name, canPreventDefault: true });
                if (activeName !== it.name && !event.defaultPrevented) navigation.navigate(it.name as never);
              }}
              onLongPress={() => navigation.emit({ type: 'tabLongPress', target: state.routes.find((route) => route.name === it.name)?.key })}
              accessibilityRole="tab"
              accessibilityLabel={it.label}
              accessibilityState={{ selected: on }}
              aria-selected={on}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 13, paddingVertical: 13, paddingHorizontal: 14, borderRadius: 14, backgroundColor: on ? 'rgba(15,162,149,.12)' : 'transparent' }}
            >
              <ZIcon name={it.icon} size={22} color={on ? c.brand : c.ink2} stroke={on ? 2.1 : 1.7} />
              <Text style={{ flex: 1, fontSize: 15, fontFamily: on ? font.bold : font.semibold, color: on ? c.brand : c.ink2 }}>{it.label}</Text>
            </Pressable>
          );
        })}
        <Pressable onPress={() => router.push('/linkwhatsapp')} accessibilityRole="button" accessibilityLabel="Bank on WhatsApp" style={{ flexDirection: 'row', alignItems: 'center', gap: 13, minHeight: 48, paddingVertical: 13, paddingHorizontal: 14, borderRadius: 14 }}>
          <WhatsAppGlyph size={22} color={c.brand} />
          <Text style={{ flex: 1, fontSize: 15, fontFamily: font.semibold, color: c.ink2 }}>WhatsApp</Text>
        </Pressable>
      </ScrollView>

      <Pressable
        onPress={() => navigation.navigate('me' as never)}
        accessibilityRole="button"
        accessibilityLabel="Open my account"
        style={{ flexDirection: 'row', alignItems: 'center', gap: 11, paddingTop: 14, paddingHorizontal: 8, borderTopWidth: 1, borderTopColor: c.line }}
      >
        <Avatar size={38} ring={c.brand} surface={c.surface} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text numberOfLines={1} style={{ fontSize: 13.5, fontFamily: font.bold, color: c.ink1 }}>My account</Text>
          <Text style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.regular }}>Profile &amp; security</Text>
        </View>
      </Pressable>
    </View>
  );
};

export default Sidebar;

