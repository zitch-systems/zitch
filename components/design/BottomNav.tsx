import React from 'react';
import { View, Text, Pressable, Linking } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { BottomTabBarProps } from 'expo-router/js-tabs';
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
    notify('WhatsApp', 'Could not open WhatsApp. Make sure it is installed, then try again.', 'error'),
  );
};

/**
 * One tab. Declared at MODULE level, not inside BottomNav.
 *
 * It used to live in BottomNav's body, which made it a brand-new component type
 * on every render. React identifies components by reference, so a new type is
 * not a re-render — it is a full unmount and remount. Every tab press changes
 * `state`, so all four tabs were being torn down and rebuilt on every single
 * navigation, throwing away each Pressable's internal press state and doing far
 * more work than diffing four sets of props. This bar is mounted on every screen
 * in the (homepage) group, so it was on the critical path of most taps in the
 * app.
 *
 * Hoisted and memoized, a press now re-renders only the two tabs whose `on`
 * actually changed.
 */
const Tab = React.memo(({ it, on, onSelect, brand, ink3 }: {
  it: { name: string; icon: string; label: string };
  on: boolean;
  /** Takes the route name, so ONE stable function serves every tab. A
   *  zero-arg `onPress` would have to be built per tab in the parent's render,
   *  which is a new prop identity each time and silently defeats the memo
   *  above — the closure is built in here instead, where it is not a prop. */
  onSelect: (name: string) => void;
  brand: string;
  ink3: string;
}) => {
  // Only the two tokens this component actually paints with, passed as plain
  // strings rather than the theme object: memoization compares props by
  // identity, and the context object is a new reference on every theme render.
  const c = { brand, ink3 };
  return (
    <Pressable
      onPress={() => onSelect(it.name)}
      accessibilityRole="tab"
      accessibilityLabel={it.label}
      accessibilityState={{ selected: on }}
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
});
Tab.displayName = 'BottomNavTab';

const BottomNav = ({ state, navigation }: BottomTabBarProps) => {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const activeName = state.routes[state.index]?.name;

  // ONE stable handler for all four tabs, taking the route name. It is passed
  // through as-is: wrapping it per tab (`onPress={() => press(it.name)}`) would
  // mint a new function on every render and make the React.memo above a no-op.
  //
  // The navigation state it needs is read through a ref rather than closed over,
  // so its identity does not change when the active tab does. Listing
  // `activeName` as a dependency would rebuild this on every navigation — the
  // exact moment the memo is supposed to help — and re-render all four tabs
  // instead of only the two whose `on` actually flipped.
  const nav = React.useRef({ routes: state.routes, activeName });
  nav.current = { routes: state.routes, activeName };

  const press = React.useCallback((name: string) => {
    const { routes, activeName: current } = nav.current;
    const route = routes.find((candidate) => candidate.name === name);
    const event = navigation.emit({ type: 'tabPress', target: route?.key, canPreventDefault: true });
    if (current !== name && !event.defaultPrevented) navigation.navigate(name as never);
  }, [navigation]);

  const renderTab = (it: { name: string; icon: string; label: string }) => (
    <Tab
      key={it.name}
      it={it}
      on={activeName === it.name}
      onSelect={press}
      brand={c.brand}
      ink3={c.ink3}
    />
  );

  return (
    <View style={{ backgroundColor: c.surface, borderTopWidth: 1, borderTopColor: c.line }}>
      <View style={{ flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-around', paddingTop: 10, paddingBottom: Math.max(8, insets.bottom), paddingHorizontal: 8 }}>
        {LEFT.map(renderTab)}

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
          {/* "WhatsApp banking", not "WhatsApp": the label sits under a WhatsApp glyph,
              so naming the app again says nothing the icon has not. What a customer
              cannot tell from the icon is that this opens THEIR BANK there rather
              than the messenger. Two lines, because the bar has no width for one. */}
          <Text style={{ fontSize: 10, marginTop: -2, fontFamily: font.semibold, color: '#0FA295', textAlign: 'center', lineHeight: 11 }}>WhatsApp{'\n'}banking</Text>
        </Pressable>

        {RIGHT.map(renderTab)}
      </View>
    </View>
  );
};

export default BottomNav;
