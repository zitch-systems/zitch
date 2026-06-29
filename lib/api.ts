import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken, clearSession } from '@/lib/secureStore';
import { touchActivity } from '@/lib/session';

// On an authenticated 401 the session is dead (expired or revoked): clear it and
// bounce to sign-in. Guarded so several in-flight requests failing together only
// redirect once; a later login re-arms it.
let handlingExpiredSession = false;
async function onSessionExpired(): Promise<void> {
  if (handlingExpiredSession) return;
  handlingExpiredSession = true;
  await clearSession();
  router.replace('/signin');
  setTimeout(() => { handlingExpiredSession = false; }, 1500);
}

/**
 * Authenticated POST to the Zitch API.
 *
 * Sends the access token only as `Authorization: Bearer <token>`. The token is
 * deliberately NOT mirrored into the JSON body: request bodies are far more
 * likely than auth headers to be captured by crash/analytics reporters, gateway
 * and WAF logs, so keeping the live session token out of the body shrinks its
 * leak surface. The backend resolves the bearer from the header (see
 * `common.http.resolve_token`). Returns the raw Response so callers keep using
 * `res.ok` and `await res.json()`.
 */
export async function apiPost(path: string, body: Record<string, any> = {}, timeoutMs = 30000): Promise<Response> {
  const token = await getToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  // Bound every request so a slow/hanging backend (e.g. a slow upstream provider
  // call) can never leave a screen stuck forever — it aborts and the caller's
  // error path runs instead.
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${baseUrl}${path}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    // A 401 on a request we authenticated means the token expired or was revoked.
    // (Sign-in and other token-less calls also 401 on bad input, but `token` is
    // null there, so this won't fire for them.)
    if (token && res.status === 401) await onSessionExpired();
    else if (token) void touchActivity(); // record activity for the idle timeout
    return res;
  } finally {
    clearTimeout(timer);
  }
}

/** POST and parse the JSON response (for call sites that don't branch on status).
 *
 * Always resolves to an object: a non-JSON body (a gateway HTML error page or an
 * empty 502/504), a network failure, or a timeout/abort all degrade to a uniform
 * { success:false, message } shape so callers' `success`/`message` checks keep
 * working and no screen hangs waiting on a promise that never settles. */
export async function apiJson<T = any>(path: string, body: Record<string, any> = {}, timeoutMs = 30000): Promise<T> {
  const offline = { success: false, message: 'Service temporarily unavailable. Please try again.' } as T;
  try {
    const res = await apiPost(path, body, timeoutMs);
    const text = await res.text();
    try {
      return JSON.parse(text) as T;
    } catch {
      return offline;
    }
  } catch {
    return offline;
  }
}

/**
 * A stable key for a single spend attempt. Pass it as `idempotency_key` on a
 * money-moving request so a double-tap / retry / network race is deduped
 * server-side and never debits twice. Generate one per authorization and reuse
 * it across retries of that same attempt.
 */
export function newIdempotencyKey(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}-${Math.random().toString(36).slice(2, 10)}`;
}
