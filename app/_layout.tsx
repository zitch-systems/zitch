// Side-effect import FIRST: installs the global fetch User-Agent guard before any
// screen can make a request, so even direct fetch() call sites aren't edge-blocked
// as anonymous bots.
import "@/lib/netPatch";
import { useEffect, useState } from "react";
import { AppState, Linking, Platform, Text as RNText, TextInput as RNTextInput } from "react-native";
import { useFonts } from "expo-font";
import { StatusBar } from "expo-status-bar";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { router, SplashScreen, Stack } from "expo-router";
import { ThemeProvider, appFonts, font, useTheme } from "@/lib/theme";
import { WalletProvider } from "@/lib/wallet";
import { MonoLauncherProvider } from "@/lib/mono";
import { NotifyHost } from "@/components/design/Notify";
import { enforceIdleTimeout, enforceHardExpiry, isSessionLocked, lockIfAwayTooLong, markBackgrounded, isExternalActivityActive } from "@/lib/session";
import { FONT_WAIT_MS, splashReady } from "@/lib/boot";
import { getToken } from "@/lib/secureStore";
import { reconcileCachedPin } from "@/lib/biometrics";
import { configureNotificationPresentation, registerForPushNotifications, subscribeToNotificationOpens } from "@/lib/notifications";
import { rememberWhatsAppApprovalUrl } from "@/lib/pendingApproval";

// Default every Text/TextInput to Inter so nothing can fall back to the
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
// The rejection is swallowed: this races the native splash's own lifecycle and
// rejects if it has already gone, and an unhandled rejection at module scope is
// not a reason for the app to fail to open.
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
  // The whole app uses Inter (see lib/theme `font`). Only these are loaded.
  const [fontsLoaded, error] = useFonts(appFonts);
  const [fontWaitOver, setFontWaitOver] = useState(false);
  // Fonts may delay the first paint, never prevent it — see lib/boot.
  const ready = splashReady(fontsLoaded, error, fontWaitOver);

  useEffect(() => {
    const timer = setTimeout(() => setFontWaitOver(true), FONT_WAIT_MS);
    return () => clearTimeout(timer);
  }, []);

  // Preserve a WhatsApp approval token even when the app is locked and the root
  // guard redirects to sign-in. After authentication, Signin resumes this exact
  // approval instead of dropping the customer on Home.
  useEffect(() => {
    Linking.getInitialURL().then(rememberWhatsAppApprovalUrl).catch(() => {});
    const sub = Linking.addEventListener('url', ({ url }) => {
      rememberWhatsAppApprovalUrl(url).catch(() => {});
    });
    return () => sub.remove();
  }, []);

  // Register silently on later launches when permission is already granted.
  // The actual first-time prompt is contextual, on completed signup.
  useEffect(() => {
    if (!ready) return;
    configureNotificationPresentation();
    registerForPushNotifications(false).catch(() => {});
    return subscribeToNotificationOpens(() => router.push('/notifications'));
  }, [ready]);

  useEffect(() => {
    // A font that will not load is a cosmetic problem. Throwing here made it a
    // fatal one — and fatal at the worst possible moment, before the splash was
    // ever hidden, so the app simply never opened.
    if (error) console.warn("[zitch] font load failed; using system fonts", error);
    // Unconditionally hide once ready, and never let hideAsync's own rejection
    // (already hidden / not yet prevented) escape as a boot crash.
    if (ready) SplashScreen.hideAsync().catch(() => {});
  }, [ready, error]);

  // PIN screens apply their own capture protection. Keeping this root effect
  // limited to keychain cleanup lets customers screenshot ordinary app pages
  // and lets the receipt renderer capture its own receipt card.
  useEffect(() => {
    // Drop any money PIN left cached by older builds when biometric pay is off.
    reconcileCachedPin().catch(() => {});
  }, []);

  // Inactivity timeout: lock a session idle past the limit and bounce to the
  // sign-in / unlock screen. Checked on launch, whenever the app returns to the
  // foreground, and on a short repeating timer so it also fires while the app
  // stays open and idle. Active use keeps the stamp fresh via authenticated API
  // calls, so the timer only trips after a real stretch of inactivity.
  // Gated on `ready`: until then this component renders null, so there is no
  // navigator mounted and router.replace() would throw "Attempted to navigate
  // before mounting the Root Layout component" — from an un-awaited async
  // function, i.e. as an unhandled rejection, with the lock bounce silently lost.
  // Effects run child-first, so by the time this one fires the Stack below is
  // mounted and the redirect lands.
  useEffect(() => {
    if (!ready) return;
    // App lock: re-opening the app (or returning from background) requires a
    // biometric/password unlock — not just after the idle timeout. The token
    // survives the lock so unlock is instant; a full sign-out clears it.
    const check = async () => {
      // A session idle past the absolute cap is cleared outright (token dropped),
      // forcing a password sign-in — checked before the softer idle lock.
      if (await enforceHardExpiry()) { router.replace("/signin"); return; }
      await lockIfAwayTooLong(); // re-lock only if backgrounded >= 1 min
      await enforceIdleTimeout();
      if (await isSessionLocked()) router.replace("/signin");
    };
    // Storage or the router failing is not a reason to take the app down with
    // it: the lock is re-checked on every foreground and on the timer below.
    const safeCheck = () => { check().catch(() => {}); };
    safeCheck();
    const sub = AppState.addEventListener("change", (s) => {
      if (s === "background") {
        // Stamp the time we left so we can re-lock on return ONLY if the user
        // was away at least a minute. Skip while an in-app picker/camera is up,
        // so uploading a photo never bounces to the unlock screen.
        if (isExternalActivityActive()) return;
        getToken().then((t) => { if (t) markBackgrounded(); }).catch(() => {});
      } else if (s === "active") {
        safeCheck();
      }
    });
    const timer = setInterval(safeCheck, 30 * 1000);
    return () => {
      sub.remove();
      clearInterval(timer);
    };
  }, [ready]);

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
          <WalletProvider>
            <MonoLauncherProvider>
              <RootStack />
              {/* Branded success/error popups, overlaid above all routes. */}
              <NotifyHost />
            </MonoLauncherProvider>
          </WalletProvider>
        </ThemeProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
};

export default RootLayout;
