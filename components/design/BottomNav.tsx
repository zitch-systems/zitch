import React from 'react';
import { View, Text, Pressable, Linking } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import ZIcon from '@/components/design/ZIcon';
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
            <Svg width={30} height={30} viewBox="0 0 24 24">
              <Path
                fill="#fff"
                d="M17.5 14.4c-.3-.15-1.76-.87-2.03-.97-.27-.1-.47-.15-.67.15-.2.3-.77.97-.94 1.17-.17.2-.35.22-.65.07-.3-.15-1.26-.46-2.4-1.48-.88-.79-1.48-1.76-1.65-2.06-.17-.3-.02-.46.13-.61.13-.13.3-.35.45-.52.15-.17.2-.3.3-.5.1-.2.05-.37-.02-.52-.08-.15-.67-1.62-.92-2.22-.24-.58-.49-.5-.67-.51h-.57c-.2 0-.52.07-.8.37-.27.3-1.04 1.02-1.04 2.5 0 1.47 1.07 2.9 1.22 3.1.15.2 2.1 3.2 5.1 4.49.71.31 1.27.49 1.7.63.72.23 1.37.2 1.88.12.58-.09 1.76-.72 2.01-1.42.25-.7.25-1.3.17-1.42-.07-.12-.27-.2-.57-.35M12.05 21.79h-.01a9.8 9.8 0 0 1-5-1.37l-.36-.21-3.72.98.99-3.63-.23-.37a9.8 9.8 0 0 1-1.5-5.22c0-5.42 4.41-9.83 9.84-9.83a9.77 9.77 0 0 1 9.82 9.84c0 5.42-4.4 9.83-9.83 9.83m8.36-18.19A11.8 11.8 0 0 0 12.04 0C5.5 0 .19 5.32.19 11.86c0 2.09.55 4.13 1.58 5.93L.1 24l6.36-1.67a11.8 11.8 0 0 0 5.58 1.42h.01c6.54 0 11.86-5.32 11.86-11.86 0-3.17-1.23-6.15-3.49-8.39"
              />
            </Svg>
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
