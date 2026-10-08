import React, { useEffect, useState } from 'react';
import { View, AppState } from 'react-native';
import { Redirect } from 'expo-router';
import { getToken, getSessionGeneration } from '@/lib/secureStore';
import { enforceHardExpiry, enforceIdleTimeout, isExternalActivityActive, isSessionLocked, lockIfAwayTooLong } from '@/lib/session';
import { Loading } from '@/components/design/Loading';
import { useTheme } from '@/lib/theme';

type AuthState = 'loading' | 'authed' | 'unauthed';

/**
 * Gates a route group behind a valid access token. Screens inside the
 * authenticated groups must not be reachable without signing in — nor while the
 * session is locked by the idle timeout (the token survives a lock, so we must
 * check the lock flag too, otherwise a locked session would still pass).
 *
 * The check re-runs on a short timer and when the app returns to the foreground,
 * not just on mount — so a session that LOCKS while an authed screen is already
 * rendered is dropped to /signin rather than staying visible until remount.
 */
const AuthGuard = ({ children }: { children: React.ReactNode; fresh?: boolean }) => {
  const { c } = useTheme();
  // Always establish an unlocked session before mounting children. A previous
  // route's cached result can expose account data or run effects after logout.
  const [state, setState] = useState<AuthState>('loading');
  const [obscured, setObscured] = useState(false);

  useEffect(() => {
    let active = true;
    const check = async (): Promise<void> => {
      const generation = getSessionGeneration();
      try {
        if (isExternalActivityActive()) return;
        await enforceHardExpiry();
        await lockIfAwayTooLong();
        await enforceIdleTimeout();
        const token = await getToken();
        const locked = token ? await isSessionLocked() : false;
        if (generation !== getSessionGeneration()) { if (active) await check(); return; }
        const next: AuthState = token && !locked ? 'authed' : 'unauthed';
        if (active) { setState(next); setObscured(false); }
      } catch {
        if (active) setState('unauthed');
      }
    };
    check();
    const sub = AppState.addEventListener('change', (s) => {
      if (s === 'active') void check();
      else if (s === 'background' && !isExternalActivityActive()) setObscured(true);
    });
    // Catches a session that locks while a screen is already open. The root
    // layout also enforces the idle lock (every 30s + on foreground), so this is
    // a backstop and doesn't need to be aggressive — 5s churned the keychain.
    const timer = setInterval(check, 15000);
    return () => {
      active = false;
      sub.remove();
      clearInterval(timer);
    };
  }, []);

  if (state === 'loading') {
    return (
      <View style={{ flex: 1, backgroundColor: c.bg }}>
        <Loading />
      </View>
    );
  }

  if (state === 'unauthed') {
    return <Redirect href="/signin" />;
  }

  // Keep a short app switch from destroying an unfinished form or in-flight
  // payment. Hide it until the foreground check succeeds, then show the same
  // mounted screen; expired/locked sessions still unmount via the redirect.
  return (
    <View style={{ flex: 1, backgroundColor: c.bg }}>
      <View style={{ flex: 1, opacity: obscured ? 0 : 1 }} pointerEvents={obscured ? 'none' : 'auto'}
        accessibilityElementsHidden={obscured} importantForAccessibility={obscured ? 'no-hide-descendants' : 'auto'}>
        {children}
      </View>
      {obscured && <View style={{ position: 'absolute', top: 0, bottom: 0, left: 0, right: 0 }}><Loading /></View>}
    </View>
  );
};

export default AuthGuard;
