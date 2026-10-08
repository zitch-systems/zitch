import { useEffect, useState } from "react";
import { AppState, Linking, Platform, Text as RNText, TextInput as RNTextInput } from "react-native";
import { useFonts } from "expo-font";
import { StatusBar } from "expo-status-bar";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { router, SplashScreen, Stack, usePathname, useRootNavigationState } from "expo-router";
import { ThemeProvider, appFonts, font, useTheme } from "@/lib/theme";
import { WalletProvider } from "@/lib/wallet";
import { NotifyHost } from "@/components/design/Notify";
import { enforceHardExpiry, enforceIdleTimeout, isSessionLocked, lockIfAwayTooLong, markBackgrounded, isExternalActivityActive } from "@/lib/session";
import { getToken, getSessionGeneration } from "@/lib/secureStore";
import { FONT_WAIT_MS, splashReady } from "@/lib/boot";
import { reconcileCachedPin } from "@/lib/biometrics";
import { rememberWhatsAppApprovalUrl } from "@/lib/pendingApproval";

// Default every Text/TextInput to Manrope so nothing can fall back to the
// platform font. An explicit fontFamily on a component still wins, since the
// component's own style is merged after this default. On Android we also drop
// the extra font padding the OS adds above/below glyphs — gives noticeably
// crisper baseline alignment matching iOS.
const textBase: { fontFamily: string; includeFontPadding?: boolean } = { fontFamily: font.medium };
if (Platform.OS === 'android') textBase.includeFontPadding = false;
const TextAny = RNText as any;
const InputAny = RNTextInput as any;
TextAny.defaultProps = TextAny.defaultProps || {};
TextAny.defaultProps.style = [textBase, TextAny.defaultProps.style];
InputAny.defaultProps = InputAny.defaultProps || {};
InputAny.defaultProps.style = [textBase, InputAny.defaultProps.style];

// Prevent the splash screen from auto-hiding before asset loading is complete.
SplashScreen.preventAutoHideAsync().catch(() => {});

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

const RootLayout = () => {
  // The whole app uses Manrope (see lib/theme `font`). Only these are loaded.
  const [fontsLoaded, error] = useFonts(appFonts);

  const [fontWaitOver, setFontWaitOver] = useState(false);
  const ready = splashReady(fontsLoaded, error, fontWaitOver);
  const navigation = useRootNavigationState();
  const pathname = usePathname();

  useEffect(() => {
    const timer = setTimeout(() => setFontWaitOver(true), FONT_WAIT_MS);
    return () => clearTimeout(timer);
  }, []);
  useEffect(() => {
    if (ready) SplashScreen.hideAsync().catch(() => {});
  }, [ready]);
  useEffect(() => {
    reconcileCachedPin().catch(() => {});
    Linking.getInitialURL().then(rememberWhatsAppApprovalUrl).catch(() => {});
    const subscription = Linking.addEventListener('url', ({ url }) => {
      rememberWhatsAppApprovalUrl(url).catch(() => {});
    });
    return () => subscription.remove();
  }, []);

  // Inactivity timeout: lock a session idle past the limit and bounce to the
  // sign-in / unlock screen. Checked on launch, whenever the app returns to the
  // foreground, and on a short repeating timer so it also fires while the app
  // stays open and idle. Active use keeps the stamp fresh via authenticated API
  // calls, so the timer only trips after a real stretch of inactivity.
  useEffect(() => {
    if (!ready || !navigation?.key) return;
    let checking = false;
    // App lock: re-opening the app (or returning from background) requires a
    // biometric/password unlock — not just after the idle timeout. The token
    // survives the lock so unlock is instant; a full sign-out clears it.
    const check = async () => {
      if (checking || isExternalActivityActive()) return;
      checking = true;
      try {
        const expired = await enforceHardExpiry();
        await lockIfAwayTooLong();
        await enforceIdleTimeout();
        if ((expired || await isSessionLocked()) && pathname !== '/signin') router.replace('/signin');
      } catch {
        if (pathname !== '/signin') router.replace('/signin');
      } finally { checking = false; }
    };
    check();
    const sub = AppState.addEventListener("change", (s) => {
      if (s === "background") {
        // Stamp the time we left so we can re-lock on return ONLY if the user
        // was away at least a minute. Skip while an in-app picker/camera is up,
        // so uploading a photo never bounces to the unlock screen.
        if (isExternalActivityActive()) return;
        getToken().then(async (t) => { if (t) await markBackgrounded(); }).catch(() => {});
      } else if (s === "active") {
        check();
      }
    });
    const timer = setInterval(check, 30 * 1000);
    return () => {
      sub.remove();
      clearInterval(timer);
    };
  }, [ready, navigation?.key, pathname]);

  if (!ready) {
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
          <WalletProvider key={getSessionGeneration()}>
            <RootStack />
            {/* Branded success/error popups, overlaid above all routes. */}
            <NotifyHost />
          </WalletProvider>
        </ThemeProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
};

export default RootLayout;
