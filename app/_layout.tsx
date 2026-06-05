import { useEffect } from "react";
import { AppState, Text as RNText, TextInput as RNTextInput } from "react-native";
import { useFonts } from "expo-font";
import { StatusBar } from "expo-status-bar";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { router, SplashScreen, Stack } from "expo-router";
import { ThemeProvider, manropeFonts, font, useTheme } from "@/lib/theme";
import { enforceIdleTimeout } from "@/lib/session";

// Default every Text/TextInput to Manrope so nothing can fall back to the
// platform font. An explicit fontFamily on a component still wins, since the
// component's own style is merged after this default.
const TextAny = RNText as any;
const InputAny = RNTextInput as any;
TextAny.defaultProps = TextAny.defaultProps || {};
TextAny.defaultProps.style = [{ fontFamily: font.regular }, TextAny.defaultProps.style];
InputAny.defaultProps = InputAny.defaultProps || {};
InputAny.defaultProps.style = [{ fontFamily: font.regular }, InputAny.defaultProps.style];

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

  // Inactivity timeout: clear a session idle past the limit and bounce to
  // sign-in. Checked once on launch and whenever the app returns to the
  // foreground; active use keeps the stamp fresh via authenticated API calls.
  useEffect(() => {
    const check = () =>
      enforceIdleTimeout().then((loggedOut) => {
        if (loggedOut) router.replace("/signin");
      });
    check();
    const sub = AppState.addEventListener("change", (s) => {
      if (s === "active") check();
    });
    return () => sub.remove();
  }, []);

  if (!fontsLoaded && !error) {
    return null;
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <ThemeProvider>
          <RootStack />
        </ThemeProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
};

export default _layout;
