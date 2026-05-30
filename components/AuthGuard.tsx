import React, { useEffect, useState } from 'react';
import { View, ActivityIndicator } from 'react-native';
import { Redirect } from 'expo-router';
import { getToken } from '@/lib/secureStore';

type AuthState = 'loading' | 'authed' | 'unauthed';

/**
 * Gates a route group behind a valid access token. Screens inside the
 * authenticated groups must not be reachable without signing in.
 */
const AuthGuard = ({ children }: { children: React.ReactNode }) => {
  const [state, setState] = useState<AuthState>('loading');

  useEffect(() => {
    let active = true;
    getToken()
      .then((token) => {
        if (active) setState(token ? 'authed' : 'unauthed');
      })
      .catch(() => {
        if (active) setState('unauthed');
      });
    return () => {
      active = false;
    };
  }, []);

  if (state === 'loading') {
    return (
      <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
        <ActivityIndicator size="large" color="#009b8f" />
      </View>
    );
  }

  if (state === 'unauthed') {
    return <Redirect href="/signin" />;
  }

  return <>{children}</>;
};

export default AuthGuard;
