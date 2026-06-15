import React from 'react';
import { View, Text, Pressable } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import ZIcon from '@/components/design/ZIcon';
import { useTheme, font } from '@/lib/theme';

// Tab order + presentation. Only these routes appear in the bar; any other
// route in the group (history, notifications, …) is reachable but hidden here.
const ITEMS: { name: string; icon: string; label: string }[] = [
  { name: 'home', icon: 'home', label: 'Home' },
  { name: 'wallet', icon: 'wallet', label: 'Wallet' },
  { name: 'convert', icon: 'convert', label: 'Convert' },
  { name: 'cards', icon: 'card', label: 'Cards' },
  { name: 'me', icon: 'user', label: 'Me' },
];

const BottomNav = ({ state, navigation }: BottomTabBarProps) => {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const activeName = state.routes[state.index]?.name;

  return (
    <View style={{ backgroundColor: c.surface, borderTopWidth: 1, borderTopColor: c.line }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-around', alignItems: 'center', paddingTop: 10, paddingBottom: 6, paddingHorizontal: 8 }}>
        {ITEMS.map((it) => {
          const on = activeName === it.name;
          return (
            <Pressable
              key={it.name}
              onPress={() => {
                const event = navigation.emit({ type: 'tabPress', target: it.name, canPreventDefault: true });
                if (!on && !event.defaultPrevented) navigation.navigate(it.name as never);
              }}
              // Tactile 3D press: the tab scales down + dims on touch.
              style={({ pressed }) => ({
                alignItems: 'center',
                gap: 5,
                paddingHorizontal: 8,
                paddingVertical: 2,
                transform: [{ scale: pressed ? 0.88 : 1 }],
                opacity: pressed ? 0.85 : 1,
              })}
            >
              {/* Active tab "lights up" with a highlighted pill behind the icon. */}
              <View
                style={{
                  paddingHorizontal: 15,
                  paddingVertical: 6,
                  borderRadius: 15,
                  backgroundColor: on ? 'rgba(15,162,149,.14)' : 'transparent',
                }}
              >
                <ZIcon name={it.icon} size={26} color={on ? c.brand : c.ink3} stroke={on ? 2.2 : 1.8} />
              </View>
              <Text style={{ fontSize: 11.5, fontFamily: on ? font.semibold : font.medium, color: on ? c.brand : c.ink3 }}>
                {it.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
      {/* iOS-style home indicator */}
      <View style={{ height: 22 + (insets.bottom ? insets.bottom - 6 : 0), alignItems: 'center', justifyContent: 'center' }}>
        <View style={{ width: 134, height: 5, borderRadius: 3, backgroundColor: c.ink1, opacity: 0.85 }} />
      </View>
    </View>
  );
};

export default BottomNav;
