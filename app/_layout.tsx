import { useEffect } from "react";
import { AppState, Platform, Text as RNText, TextInput as RNTextInput } from "react-native";
import { useFonts } from "expo-font";
import { StatusBar } from "expo-status-bar";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { router, SplashScreen, Stack } from "expo-router";
import { ThemeProvider, manropeFonts, font, useTheme } from "@/lib/theme";
import { WalletProvider } from "@/lib/wallet";
import { NotifyHost } from "@/components/design/Notify";
import { enforceIdleTimeout, isSessionLocked, lockSession } from "@/lib/session";
import { getToken } from "@/lib/secureStore";

// Default every Text/TextInput to Manrope so nothing can fall back to the
// platform font. An explicit fontFamily on a component still wins, since the
// component's own style is merged after this default. On Android we also drop
// the extra font padding the OS adds above/below glyphs — gives noticeably
// crisper baseline alignment matching iOS.
const textBase: { fontFamily: string; includeFontPadding?: boolean } = { fontFamily: font.regular };
if (Platform.OS === 'android') textBase.includeFontPadding = false;
const TextAny = RNText as any;
const InputAny = RNTextInput as any;
TextAny.defaultProps = TextAny.defaultProps || {};
TextAny.defaultProps.style = [textBase, TextAny.defaultProps.style];
InputAny.defaultProps = InputAny.defaultProps || {};
InputAny.defaultProps.style = [textBase, InputAny.defaultProps.style];

// Prevent the splash screen from auto-hiding before asset loading is complete.
SplashScreen.preventAutoHideAsync();

const RootStack = () => {
  const { theme, c } = useTheme();
  return (
    <>
      <StatusBar style={theme === "dark" ? "light" : "dark"} />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: c.bg } }}>
        <Stack.Screen name="index" />
        <Stack.Screen name="(auth)" />
        <Stack.Screen name="(homepage)" />
        <Stack.Screen name="(servicesscreen)" />
      </Stack>
    </>
  );
};

const _layout = () => {
  // The whole app uses Manrope (see lib/theme `font`). Only these are loaded.
  const [fontsLoaded, error] = useFonts(manropeFonts);

  useEffect(() => {
    if (error) throw error;
    if (fontsLoaded) {
      SplashScreen.hideAsync();
    }
  }, [fontsLoaded, error]);

  // Inactivity timeout: lock a session idle past the limit and bounce to the
  // sign-in / unlock screen. Checked on launch, whenever the app returns to the
  // foreground, and on a short repeating timer so it also fires while the app
  // stays open and idle. Active use keeps the stamp fresh via authenticated API
  // calls, so the timer only trips after a real stretch of inactivity.
  useEffect(() => {
    // App lock: re-opening the app (or returning from background) requires a
    // biometric/password unlock — not just after the idle timeout. The token
    // survives the lock so unlock is instant; a full sign-out clears it.
    const check = async () => {
      await enforceIdleTimeout();
      if (await isSessionLocked()) router.replace("/signin");
    };
    check();
    const sub = AppState.addEventListener("change", (s) => {
      if (s === "background") {
        // Leaving the app locks the session so the next open re-authenticates.
        getToken().then((t) => { if (t) lockSession(); });
      } else if (s === "active") {
        check();
      }
    });
    const timer = setInterval(check, 30 * 1000);
    return () => {
      sub.remove();
      clearInterval(timer);
    };
  }, []);

  if (!fontsLoaded && !error) {
    return null;
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <ThemeProvider>
          {/* Wallet state lives at the root so it is shared across BOTH the
              (homepage) tabs and the (servicesscreen) flows. A purchase/transfer
              screen calling reload() updates the same balance Home/Wallet render —
              previously the provider only wrapped the tabs, so service screens got
              a no-op default context and the balance never refreshed. */}
          <WalletProvider>
            <RootStack />
            {/* Branded success/error popups, overlaid above all routes. */}
            <NotifyHost />
          </WalletProvider>
        </ThemeProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
};

export default _layout;
