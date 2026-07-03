import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken, clearSession } from '@/lib/secureStore';
import { touchActivity } from '@/lib/session';
// Importing this also installs the global fetch guard: it stamps the app
// User-Agent on every API request (covers the screens that call fetch()
// directly, not just apiPost) and retries edge/WAF-blocked calls against the
// Render fallback host. USER_AGENT is reused here so an explicit apiPost
// header and the guard agree.
import { USER_AGENT, isEdgeBlockMessage } from '@/lib/netPatch';

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
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
    'User-Agent': USER_AGENT,
  };
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
  // `offline: true` distinguishes "the request/reply never completed — delivery
  // unknown" from a definitive backend rejection. Money screens use it to KEEP
  // their idempotency key, so a user retry replays server-side instead of
  // double-debiting.
  const offline = { success: false, offline: true, message: 'Service temporarily unavailable. Please try again.' } as T;
  try {
    const res = await apiPost(path, body, timeoutMs);
    const text = await res.text();
    try {
      const parsed = JSON.parse(text) as any;
      // Both the edge and the fallback host blocked us (the fetch guard already
      // retried): replace the WAF's raw block-page message ("You are not
      // authorized to access this resource") with something a user can act on.
      if (res.status === 403 && isEdgeBlockMessage(parsed?.message)) {
        return {
          ...parsed,
          success: false,
          message: 'A network security check blocked this request. Please try again in a moment.',
        } as T;
      }
      return parsed as T;
    } catch {
      return offline;
    }
  } catch {
    return offline;
  }
}

/**
 * Timeout-bounded POST for UNAUTHENTICATED endpoints (sign-in, register, OTP,
 * password reset). Same AbortController deadline as `apiPost` so a hung
 * connection can never leave an auth screen's full-screen loader spinning
 * forever — but no `Authorization` header and no 401→sign-out redirect (these
 * calls legitimately 401 on bad credentials, and there's no session to clear).
 * The global fetch guard still stamps the User-Agent.
 */
export async function publicPost(path: string, body: Record<string, any> = {}, timeoutMs = 30000): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(`${baseUrl}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(timer);
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
