import React, { useEffect, useState } from 'react';
import { View, AppState } from 'react-native';
import { Redirect } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { isSessionLocked } from '@/lib/session';
import { Loading } from '@/components/design/Loading';

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
const AuthGuard = ({ children }: { children: React.ReactNode }) => {
  const [state, setState] = useState<AuthState>('loading');

  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const token = await getToken();
        const locked = token ? await isSessionLocked() : false;
        if (active) setState(token && !locked ? 'authed' : 'unauthed');
      } catch {
        if (active) setState('unauthed');
      }
    };
    check();
    const sub = AppState.addEventListener('change', (s) => {
      if (s === 'active') check();
    });
    const timer = setInterval(check, 5000);
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
