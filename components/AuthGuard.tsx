import React, { useEffect, useState } from 'react';
import { View, AppState } from 'react-native';
import { Redirect } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { isSessionLocked } from '@/lib/session';
import { Loading } from '@/components/design/Loading';

type AuthState = 'loading' | 'authed' | 'unauthed';

// Remember the last resolved auth result across mounts. Each route group wraps
// its content in its own AuthGuard, so navigating (homepage) -> (servicesscreen)
// mounts a fresh guard; without this it would start in 'loading' and flash the
// full-screen loader on every navigation. Seeding from the cache lets an in-app
// navigation render the target screen immediately while the check re-confirms in
// the background (it still redirects if the session has since locked/expired).
let lastKnownAuth: AuthState | null = null;

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
const AuthGuard = ({ children }: { children: React.ReactNode }) => {
  const [state, setState] = useState<AuthState>(lastKnownAuth ?? 'loading');

  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const token = await getToken();
        const locked = token ? await isSessionLocked() : false;
        const next: AuthState = token && !locked ? 'authed' : 'unauthed';
        lastKnownAuth = next;
        if (active) setState(next);
      } catch {
        if (active) setState('unauthed');
      }
    };
    check();
    const sub = AppState.addEventListener('change', (s) => {
      if (s === 'active') check();
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
      <View style={{ flex: 1, backgroundColor: '#EFF7F5' }}>
        <Loading />
      </View>
    );
  }

  if (state === 'unauthed') {
    return <Redirect href="/signin" />;
  }

  return <>{children}</>;
};

export default AuthGuard;
