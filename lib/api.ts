import { router } from 'expo-router';
import * as Crypto from 'expo-crypto';
import baseUrl from '@/components/configFiles/apiConfig';
import {
  getToken,
  clearSession,
  getRefreshToken,
  saveToken,
  saveRefreshToken,
} from '@/lib/secureStore';
import { touchActivity } from '@/lib/session';
// Importing this also installs the global fetch guard: it stamps the app
// User-Agent on every API request (covers the screens that call fetch()
// directly, not just apiPost). USER_AGENT is reused here so an explicit apiPost
// header and the guard agree. WAF refusals are never routed around the edge.
import { USER_AGENT, isEdgeBlockMessage } from '@/lib/netPatch';
import { deviceHeaders } from '@/lib/deviceIntegrity';

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

// A refresh in flight, shared by every caller that wants one.
//
// This has to be deduplicated, not merely guarded. The access token expires while
// the app is closed, so the first screen after opening fires several authenticated
// requests at once and they all 401 together. Refreshing per request would present
// the same refresh token N times — and the server burns it on first use and treats
// the second presentation as a stolen token, revoking the entire chain. So the
// naive version of this feature signs the customer out precisely when it is
// supposed to keep them signed in, and reports a break-in while doing it.
let refreshing: Promise<RefreshOutcome> | null = null;

// Three outcomes, not two. 'renewed' retries the request; 'rejected' means the
// server refused the refresh token, which is a genuinely dead session and the only
// case that signs the customer out. 'unavailable' is a dropped connection or a
// timeout — the session may be perfectly valid once there is signal again, so the
// original 401 is returned to the caller and nothing is wiped. Collapsing the last
// two into one boolean turns a subway tunnel into a re-login.
type RefreshOutcome = 'renewed' | 'rejected' | 'unavailable';

/** Exchange the stored refresh token for a new pair. */
async function refreshSession(): Promise<RefreshOutcome> {
  if (refreshing) return refreshing;
  refreshing = (async (): Promise<RefreshOutcome> => {
    try {
      const refresh = await getRefreshToken();
      // Nothing to renew with: an older install, or a session stored before
      // refresh tokens existed. That IS a dead session.
      if (!refresh) return 'rejected';
      // publicPost, not apiPost: this call carries no access token (the point is
      // that the old one is dead) and must never recurse into this handler.
      const res = await publicPost('/api/token/refresh/', { refresh_token: refresh }, 15000);
      const data = await res.json().catch(() => null);
      if (res.ok && data?.access_token && data?.refresh_token) {
        // Persist the rotated pair BEFORE any retried request goes out. If the app
        // died between using the new token and storing it, the next launch would
        // present the burnt one and be treated as a theft.
        await saveRefreshToken(data.refresh_token);
        await saveToken(data.access_token);
        return 'renewed';
      }
      // A 401 here is the server's verdict on the refresh token itself. A 5xx or
      // a gateway page is not a verdict about the session at all.
      return res.status === 401 ? 'rejected' : 'unavailable';
    } catch {
      return 'unavailable';
    } finally {
      // Cleared in `finally` so a failed refresh doesn't pin every later attempt
      // to the same rejected promise.
      refreshing = null;
    }
  })();
  return refreshing;
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
  const res = await sendAuthed(path, body, timeoutMs);
  const token = await getToken();
  // A 401 on a request we authenticated means the access token expired or was
  // revoked. Expiry is the ordinary case — the token lives TOKEN_TTL_HOURS and the
  // app is opened daily — so try the refresh token once before concluding the
  // session is over. Only if that fails is the customer actually signed out.
  //
  // Retried once, never in a loop: a server that 401s a freshly minted token is
  // telling us something a second attempt won't change.
  if (token && res.status === 401) {
    const outcome = await refreshSession();
    if (outcome === 'renewed') {
      const retried = await sendAuthed(path, body, timeoutMs);
      if (retried.status === 401) await onSessionExpired();
      else void touchActivity();
      return retried;
    }
    // 'unavailable' returns the 401 without clearing anything — see RefreshOutcome.
    if (outcome === 'rejected') await onSessionExpired();
  } else if (token) {
    void touchActivity(); // record activity for the idle timeout
  }
  return res;
}

/** One authenticated attempt: builds the headers from whatever token is stored
 *  now, so a retry after a refresh picks up the NEW one rather than resending the
 *  expired token it was called with. */
async function sendAuthed(path: string, body: Record<string, any>, timeoutMs: number): Promise<Response> {
  const token = await getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
    'User-Agent': USER_AGENT,
    // Device binding + integrity signals for backend risk scoring. Headers rather
    // than body fields, so no existing request shape changes and no endpoint has to
    // opt in. Advisory only — the backend never trusts them as a verdict, because a
    // compromised device controls every value in them.
    ...(await deviceHeaders()),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  // Bound every request so a slow/hanging backend (e.g. a slow upstream provider
  // call) can never leave a screen stuck forever — it aborts and the caller's
  // error path runs instead.
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    // Deliberately no 401 handling here: apiPost owns that, so one attempt cannot
    // sign the customer out before the refresh has been tried.
    return await fetch(`${baseUrl}${path}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
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
      // Replace the WAF's raw block-page message ("You are not authorized to
      // access this resource") with something a user can act on.
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
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        // Sign-in and OTP are exactly where device binding earns its keep: "this
        // account has never authenticated from this device" is the signal that
        // distinguishes a stolen password from the real user.
        ...(await deviceHeaders()),
      },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Timeout-bounded public POST with safe JSON parsing. Public catalogue screens
 * use this instead of bare fetch chains so a gateway HTML response, network
 * failure, or hung request always settles into a predictable error object.
 */
export async function publicJson<T = any>(
  path: string,
  body: Record<string, any> = {},
  timeoutMs = 30000,
): Promise<T> {
  const unavailable = {
    success: false,
    offline: true,
    message: 'Service temporarily unavailable. Please try again.',
  } as T;
  try {
    const res = await publicPost(path, body, timeoutMs);
    const text = await res.text();
    try {
      return JSON.parse(text) as T;
    } catch {
      return unavailable;
    }
  } catch {
    return unavailable;
  }
}

/**
 * A stable key for a single spend attempt. Pass it as `idempotency_key` on a
 * money-moving request so a double-tap / retry / network race is deduped
 * server-side and never debits twice. Generate one per authorization and reuse
 * it across retries of that same attempt.
 */
export function newIdempotencyKey(): string {
  // A money-operation key is an unguessable nonce, not just a likely-unique UI
  // correlation value.  CSPRNG UUIDs prevent another client from predicting a key
  // and pre-claiming it against an idempotency bucket.
  return Crypto.randomUUID();
}
