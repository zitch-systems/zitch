import React from 'react';
import { View, Text, Pressable } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import ZIcon from '@/components/design/ZIcon';
import { ZMark, ZWordmark, Avatar } from '@/components/design/Brand';
import { useTheme, font } from '@/lib/theme';
import { useDeviceClass } from '@/lib/device';

const ITEMS: { name: string; icon: string; label: string }[] = [
  { name: 'home', icon: 'home', label: 'Home' },
  { name: 'wallet', icon: 'wallet', label: 'Wallet' },
  { name: 'loan', icon: 'loan', label: 'Loans' },
  { name: 'cards', icon: 'card', label: 'Cards' },
  { name: 'me', icon: 'user', label: 'Me' },
];

/**
 * Left navigation rail for fold/tablet — replaces the bottom nav on wide
 * screens (logo, nav items with active highlight, profile footer).
 */
const Sidebar = ({ state, navigation }: BottomTabBarProps) => {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const device = useDeviceClass();
  const activeName = state.routes[state.index]?.name;
  const railW = device === 'tablet' ? 240 : 200;

  return (
    <View style={{ width: railW, height: '100%', backgroundColor: c.surface, borderRightWidth: 1, borderRightColor: c.line, paddingTop: insets.top + 18, paddingHorizontal: 16, paddingBottom: 18 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 8, paddingBottom: 22 }}>
        <ZMark size={26} />
        <ZWordmark size={17} color={c.ink1} />
      </View>

      <View style={{ flex: 1, gap: 4 }}>
        {ITEMS.map((it) => {
          const on = activeName === it.name;
          return (
            <Pressable
              key={it.name}
              onPress={() => {
                const event = navigation.emit({ type: 'tabPress', target: it.name, canPreventDefault: true });
                if (!on && !event.defaultPrevented) navigation.navigate(it.name as never);
              }}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 13, paddingVertical: 13, paddingHorizontal: 14, borderRadius: 14, backgroundColor: on ? 'rgba(15,162,149,.12)' : 'transparent' }}
            >
              <ZIcon name={it.icon} size={22} color={on ? c.brand : c.ink2} stroke={on ? 2.1 : 1.7} />
              <Text style={{ fontSize: 15, fontFamily: on ? font.bold : font.semibold, color: on ? c.brand : c.ink2 }}>{it.label}</Text>
            </Pressable>
          );
        })}
      </View>

      <Pressable
        onPress={() => navigation.navigate('me' as never)}
        style={{ flexDirection: 'row', alignItems: 'center', gap: 11, paddingTop: 14, paddingHorizontal: 8, borderTopWidth: 1, borderTopColor: c.line }}
      >
        <Avatar size={38} ring={c.brand} surface={c.surface} />
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text numberOfLines={1} style={{ fontSize: 13.5, fontFamily: font.bold, color: c.ink1 }}>My account</Text>
          <Text style={{ fontSize: 11.5, color: c.ink3, fontFamily: font.regular }}>Tier 3</Text>
        </View>
      </Pressable>
    </View>
  );
};

export default Sidebar;
