import { Platform } from 'react-native';
import Constants from 'expo-constants';
import baseUrl from '@/components/configFiles/apiConfig';

// An identifiable User-Agent for the app. React Native's default is
// `okhttp/<ver>` on Android and `CFNetwork/...` on iOS — user agents that
// Cloudflare's Bot Fight Mode and many WAFs treat as anonymous bots and 403 at
// the edge ("You are not allowed to access this resource") before the request
// ever reaches the API. A named UA keeps the app's legitimate traffic out of
// those generic bot rules.
export const APP_VERSION = (Constants.expoConfig?.version as string) || '1.0';
export const USER_AGENT = `ZitchApp/${APP_VERSION} (${Platform.OS})`;

// Install a global fetch guard so EVERY request to our API carries the User-Agent
// (and Accept: application/json) — including the screens that call `fetch()`
// directly (auth + some service lists) rather than going through `apiPost`.
// Scoped to our own API host, so third-party SDK traffic (Mono/Monnify webviews)
// is untouched. Idempotent, and only fills a header that a caller left unset, so
// it never overrides an explicit value. Import this once at app entry.
const g = globalThis as any;
if (!g.__zitchFetchPatched && typeof g.fetch === 'function') {
  g.__zitchFetchPatched = true;
  const orig: typeof fetch = g.fetch.bind(g);
  g.fetch = (input: any, init?: any) => {
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      if (typeof url === 'string' && baseUrl && url.startsWith(baseUrl)) {
        const headers = new Headers(
          (init && init.headers) || (typeof input !== 'string' && input && input.headers) || {},
        );
        if (!headers.has('User-Agent')) headers.set('User-Agent', USER_AGENT);
        if (!headers.has('Accept')) headers.set('Accept', 'application/json');
        return orig(input, { ...(init || {}), headers });
      }
    } catch {
      // Never let the guard break a request — fall through to the original fetch.
    }
    return orig(input, init);
  };
}
