import { Platform } from 'react-native';
import Constants from 'expo-constants';
import baseUrl, { FALLBACK_API_URL } from '@/components/configFiles/apiConfig';

// An identifiable User-Agent for the app. React Native's default is
// `okhttp/<ver>` on Android and `CFNetwork/...` on iOS — user agents that
// Cloudflare's Bot Fight Mode and many WAFs treat as anonymous bots and 403 at
// the edge ("You are not allowed to access this resource") before the request
// ever reaches the API. A named UA keeps the app's legitimate traffic out of
// those generic bot rules.
export const APP_VERSION = (Constants.expoConfig?.version as string) || '1.0';
export const USER_AGENT = `ZitchApp/${APP_VERSION} (${Platform.OS})`;

// --- Edge-403 fallback -------------------------------------------------------
// api.zitch.ng sits behind a CDN/WAF whose bot rules have been observed 403-ing
// legitimate app requests with a JSON body like "You are not authorized to
// access this resource" — a message our Django backend never emits. When that
// signature appears, the SAME request is retried once against the Render-
// assigned host (no WAF zone in front of it; already in DJANGO_ALLOWED_HOSTS
// as the documented fallback), and the fallback becomes sticky for the session
// so every later call skips the blocked edge. Scoped to the default production
// domain only — a local-dev EXPO_PUBLIC_API_URL override never switches hosts.
const FALLBACK_ELIGIBLE = baseUrl === 'https://api.zitch.ng';
let useFallbackHost = false;

// The WAF block page, not a backend response. Both known phrasings are matched
// ("allowed" and "authorized"); our own API never uses either sentence.
const EDGE_BLOCK_RE = /not (allowed|authorized) to access this resource/i;

export const isEdgeBlockMessage = (message: unknown): boolean =>
  typeof message === 'string' && EDGE_BLOCK_RE.test(message);

async function isEdgeBlock(res: Response): Promise<boolean> {
  if (res.status !== 403) return false;
  try {
    // clone() so the caller can still read the body if this isn't a block.
    return EDGE_BLOCK_RE.test(await res.clone().text());
  } catch {
    return false;
  }
}

// Install a global fetch guard so EVERY request to our API carries the User-Agent
// (and Accept: application/json) — including the screens that call `fetch()`
// directly (auth + some service lists) rather than going through `apiPost`.
// Scoped to our own API hosts, so third-party SDK traffic (Mono/Kora webviews)
// is untouched. Idempotent, and only fills a header that a caller left unset, so
// it never overrides an explicit value. Also applies the edge-403 fallback above.
// Import this once at app entry.
const g = globalThis as any;
if (!g.__zitchFetchPatched && typeof g.fetch === 'function') {
  g.__zitchFetchPatched = true;
  const orig: typeof fetch = g.fetch.bind(g);

  // When a fallback-leg request REJECTS (abort, timeout, connection reset), the
  // rejection is rethrown — never swapped for the edge's 403. A rejection can
  // mean "sent but the reply never came" (cold start, reset after send), while
  // the 403 body asserts the request was blocked before delivery; presenting it
  // would invite the caller to retry a money POST that may already have been
  // processed. The flag is also cleared so a failing fallback host is never a
  // one-way door: the next call probes the primary again.
  const fetchWithFallback = async (url: string, onFallback: string, init: any): Promise<Response> => {
    if (useFallbackHost) {
      try {
        return await orig(onFallback, init);
      } catch (e) {
        useFallbackHost = false;
        throw e;
      }
    }
    const res = await orig(url, init);
    if (await isEdgeBlock(res)) {
      useFallbackHost = true; // sticky: don't re-probe the blocked edge every call
      try {
        return await orig(onFallback, init);
      } catch (e) {
        useFallbackHost = false;
        throw e;
      }
    }
    return res;
  };

  g.fetch = (input: any, init?: any) => {
    // Only PLANNING is guarded — the network call itself runs outside the
    // try/catch, so a genuine fetch rejection propagates to the caller instead
    // of being swallowed and re-sent (a silent double-send on a money POST).
    let patched: any = null;
    let primary = '';
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      const ours =
        typeof url === 'string' &&
        ((baseUrl && url.startsWith(baseUrl)) || url.startsWith(FALLBACK_API_URL));
      if (ours) {
        const headers = new Headers(
          (init && init.headers) || (typeof input !== 'string' && input && input.headers) || {},
        );
        if (!headers.has('User-Agent')) headers.set('User-Agent', USER_AGENT);
        if (!headers.has('Accept')) headers.set('Accept', 'application/json');
        patched = { ...(init || {}), headers };
        // Only string-URL requests are rerouted/retried (all app code passes
        // strings; a Request object's body can't be safely re-sent).
        if (FALLBACK_ELIGIBLE && typeof input === 'string' && url.startsWith(baseUrl)) {
          primary = url;
        }
      }
    } catch {
      // Never let the guard break a request — fall back to the original call.
      patched = null;
      primary = '';
    }
    if (patched && primary) {
      return fetchWithFallback(primary, FALLBACK_API_URL + primary.slice(baseUrl.length), patched);
    }
    if (patched) return orig(input, patched);
    return orig(input, init);
  };
}

