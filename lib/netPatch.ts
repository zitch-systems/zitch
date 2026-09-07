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

// The WAF block page, not a backend response. Both known phrasings are matched
// ("allowed" and "authorized"); our own API never uses either sentence.
const EDGE_BLOCK_RE = /not (allowed|authorized) to access this resource/i;

export const isEdgeBlockMessage = (message: unknown): boolean =>
  typeof message === 'string' && EDGE_BLOCK_RE.test(message);

// Install a global fetch guard so EVERY request to our API carries the User-Agent
// (and Accept: application/json) — including the screens that call `fetch()`
// directly (auth + some service lists) rather than going through `apiPost`.
// Scoped to our own API hosts, so third-party SDK traffic (Mono/Kora webviews)
// is untouched. Idempotent, and only fills a header that a caller left unset, so
// it never overrides an explicit value. A WAF refusal stays a refusal; the app
// never routes around the security boundary to a provider-assigned origin.
// Import this once at app entry.
const g = globalThis as any;
if (!g.__zitchFetchPatched && typeof g.fetch === 'function') {
  g.__zitchFetchPatched = true;
  const orig: typeof fetch = g.fetch.bind(g);

  g.fetch = (input: any, init?: any) => {
    // Only PLANNING is guarded — the network call itself runs outside the
    // try/catch, so a genuine fetch rejection propagates to the caller instead
    // of being swallowed and re-sent (a silent double-send on a money POST).
    let patched: any = null;
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      const ours =
        typeof url === 'string' &&
        Boolean(baseUrl && url.startsWith(baseUrl));
      if (ours) {
        const headers = new Headers(
          (init && init.headers) || (typeof input !== 'string' && input && input.headers) || {},
        );
        if (!headers.has('User-Agent')) headers.set('User-Agent', USER_AGENT);
        if (!headers.has('Accept')) headers.set('Accept', 'application/json');
        patched = { ...(init || {}), headers };
      }
    } catch {
      // Never let the guard break a request — fall back to the original call.
      patched = null;
    }
    if (patched) return orig(input, patched);
    return orig(input, init);
  };
}

