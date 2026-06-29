import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { View, Text, Modal, Pressable, ActivityIndicator } from 'react-native';
import { useTheme, font } from '@/lib/theme';

// Mono Connect (open banking) launcher.
//
// Real bank linking runs in the native `@mono.co/connect-react-native` widget
// (v2: <MonoProvider> + useMonoConnect().init()). That native module only exists
// in a custom dev build / EAS build — NOT in Expo Go or Node (jest). We load it
// with an optional require() so the JS bundle never crashes when it's absent;
// there we fall back to a SIMULATED "Connecting…" sheet that returns an
// obviously-fake `MONO-SIM-…` code the backend rejects, so the whole flow is
// testable everywhere while only real builds can actually link a bank.
const PUBLIC_KEY = process.env.EXPO_PUBLIC_MONO_PUBLIC_KEY ?? '';

let SDK: any = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  SDK = require('@mono.co/connect-react-native');
} catch {
  SDK = null;
}
const MonoProvider: any = SDK ? (SDK.MonoProvider ?? SDK.default) : null;
const useMonoConnect: any = SDK ? SDK.useMonoConnect : null;
// Only treat the native widget as available when the SDK AND a public key are
// present; without a key the widget can't open, so we use the simulated flow.
const HAS_NATIVE = !!(MonoProvider && useMonoConnect && PUBLIC_KEY);

export type MonoLaunchOpts = {
  reference?: string;
  onSuccess: (code: string) => void | Promise<void>;
  onClose?: () => void;
};

type MonoContextValue = { launch: (opts: MonoLaunchOpts) => void; native: boolean };
const MonoContext = createContext<MonoContextValue>({ launch: () => {}, native: false });

export const useMono = () => useContext(MonoContext);

// Mounted only when the native SDK is present: pulls init() out of the hook so
// the provider can open the widget imperatively. HAS_NATIVE is a build-time
// constant, so this component renders consistently (no rules-of-hooks issue).
const NativeBridge = ({ bind }: { bind: (init: () => void) => void }) => {
  const { init } = useMonoConnect();
  useEffect(() => { bind(init); }, [init, bind]);
  return null;
};

export const MonoLauncherProvider = ({ children }: { children: React.ReactNode }) => {
  const { c } = useTheme();
  const opts = useRef<MonoLaunchOpts | null>(null);
  const initFn = useRef<(() => void) | null>(null);
  const [sim, setSim] = useState<MonoLaunchOpts | null>(null);

  const onSuccess = useCallback((data: any) => {
    const code = data?.code ?? data?.getAuthCode?.() ?? data;
    const o = opts.current; opts.current = null;
    if (code && o) o.onSuccess(String(code));
  }, []);
  const onClose = useCallback(() => { const o = opts.current; opts.current = null; o?.onClose?.(); }, []);

  const launch = useCallback((o: MonoLaunchOpts) => {
    if (HAS_NATIVE && initFn.current) { opts.current = o; initFn.current(); return; }
    setSim(o); // dev preview / Expo Go
  }, []);

  const value = useMemo(() => ({ launch, native: HAS_NATIVE }), [launch]);

  const tree = HAS_NATIVE ? (
    <MonoProvider publicKey={PUBLIC_KEY} onSuccess={onSuccess} onClose={onClose}>
      <NativeBridge bind={(init) => { initFn.current = init; }} />
      {children}
    </MonoProvider>
  ) : (
    children
  );

  return (
    <MonoContext.Provider value={value}>
      {tree}
      <Modal visible={!!sim} transparent animationType="fade" onRequestClose={() => { const o = sim; setSim(null); o?.onClose?.(); }}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,.45)', alignItems: 'center', justifyContent: 'center', padding: 28 }}>
          <View style={{ width: '100%', maxWidth: 360, borderRadius: 22, backgroundColor: c.surface, padding: 24, alignItems: 'center' }}>
            <ActivityIndicator color={c.brand} />
            <Text style={{ fontSize: 16, fontFamily: font.extrabold, color: c.ink1, marginTop: 16 }}>Connecting your bank…</Text>
            <Text style={{ fontSize: 13, color: c.ink3, fontFamily: font.regular, textAlign: 'center', marginTop: 8, lineHeight: 19 }}>
              Dev preview — the real Mono widget needs the native build. Simulate a result to test the flow.
            </Text>
            <View style={{ flexDirection: 'row', gap: 10, marginTop: 20, alignSelf: 'stretch' }}>
              <Pressable
                onPress={() => { const o = sim; setSim(null); o?.onClose?.(); }}
                style={{ flex: 1, height: 46, borderRadius: 13, borderWidth: 1.5, borderColor: c.line, alignItems: 'center', justifyContent: 'center' }}
              >
                <Text style={{ fontFamily: font.bold, color: c.ink2, fontSize: 14 }}>Cancel</Text>
              </Pressable>
              <Pressable
                onPress={() => { const o = sim; setSim(null); o?.onSuccess(`MONO-SIM-${Date.now()}`); }}
                style={{ flex: 1, height: 46, borderRadius: 13, backgroundColor: c.brand, alignItems: 'center', justifyContent: 'center' }}
              >
                <Text style={{ fontFamily: font.bold, color: '#fff', fontSize: 14 }}>Simulate</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </MonoContext.Provider>
  );
};
