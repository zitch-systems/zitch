import React from 'react';
import { View, Text, Pressable, Linking } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import ZIcon from '@/components/design/ZIcon';
import { WhatsAppGlyph } from '@/components/design/WhatsAppGlyph';
import { useTheme, font } from '@/lib/theme';
import { notify } from '@/components/design/Notify';
import { BANK_WHATSAPP } from '@/components/configFiles/links';

// Tab order + presentation. Only these routes appear in the bar; any other route
// in the group (convert, loan, history, notifications, …) is reachable but hidden
// here. Convert lives in Home's quick actions. The four tabs split 2-and-2 around
// the raised WhatsApp button so it sits in the dead centre.
const LEFT: { name: string; icon: string; label: string }[] = [
  { name: 'home', icon: 'home', label: 'Home' },
  { name: 'wallet', icon: 'wallet', label: 'Wallet' },
];
const RIGHT: { name: string; icon: string; label: string }[] = [
  { name: 'cards', icon: 'card', label: 'Cards' },
  { name: 'me', icon: 'user', label: 'Me' },
];

// Open the Zitch banking bot on WhatsApp, prefilled so the bot greets the user.
const openWhatsApp = () => {
  const url = `https://wa.me/${BANK_WHATSAPP}?text=${encodeURIComponent('Hi Zitch 👋')}`;
  Linking.openURL(url).catch(() =>
    notify('WhatsApp', 'Could not open WhatsApp. Make sure it is installed, then try again.'),
  );
};

const BottomNav = ({ state, navigation }: BottomTabBarProps) => {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const activeName = state.routes[state.index]?.name;

  const Tab = ({ it }: { it: { name: string; icon: string; label: string } }) => {
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
          flex: 1,
          alignItems: 'center',
          gap: 5,
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
  };

  return (
    <View style={{ backgroundColor: c.surface, borderTopWidth: 1, borderTopColor: c.line }}>
      <View style={{ flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-around', paddingTop: 10, paddingBottom: 6, paddingHorizontal: 8 }}>
        {LEFT.map((it) => <Tab key={it.name} it={it} />)}

        {/* Raised WhatsApp button — the channel's hero action, dead centre. */}
        <Pressable
          onPress={openWhatsApp}
          accessibilityRole="button"
          accessibilityLabel="Bank on WhatsApp"
          style={({ pressed }) => ({
            flex: 1,
            alignItems: 'center',
            gap: 5,
            transform: [{ scale: pressed ? 0.9 : 1 }],
          })}
        >
          <View
            style={{
              width: 58,
              height: 58,
              borderRadius: 29,
              marginTop: -28,
              backgroundColor: '#25D366',
              alignItems: 'center',
              justifyContent: 'center',
              // 4px surface-coloured ring "notches" the button through the bar.
              borderWidth: 4,
              borderColor: c.surface,
              // Lift it off the bar.
              shadowColor: '#075E54',
              shadowOpacity: 0.4,
              shadowRadius: 10,
              shadowOffset: { width: 0, height: 6 },
              elevation: 8,
            }}
          >
            <WhatsAppGlyph size={30} color="#fff" />
          </View>
          <Text style={{ fontSize: 10.5, marginTop: -2, fontFamily: font.semibold, color: '#0FA295' }}>WhatsApp</Text>
        </Pressable>

        {RIGHT.map((it) => <Tab key={it.name} it={it} />)}
      </View>
      {/* iOS-style home indicator */}
      <View style={{ height: 22 + (insets.bottom ? insets.bottom - 6 : 0), alignItems: 'center', justifyContent: 'center' }}>
        <View style={{ width: 134, height: 5, borderRadius: 3, backgroundColor: c.ink1, opacity: 0.85 }} />
      </View>
    </View>
  );
};

export default BottomNav;
