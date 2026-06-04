import { router } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken, clearSession } from '@/lib/secureStore';

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
 * Sends the access token as `Authorization: Bearer <token>` (the preferred
 * scheme) and, for backwards compatibility while screens migrate, still mirrors
 * it into the JSON body as `access_token` — the backend accepts either. Returns
 * the raw Response so callers keep using `res.ok` and `await res.json()`.
 */
export async function apiPost(path: string, body: Record<string, any> = {}): Promise<Response> {
  const token = await getToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${baseUrl}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(token ? { access_token: token, ...body } : body),
  });
  // A 401 on a request we authenticated means the token expired or was revoked.
  // (Sign-in and other token-less calls also 401 on bad input, but `token` is
  // null there, so this won't fire for them.)
  if (token && res.status === 401) await onSessionExpired();
  return res;
}

/** POST and parse the JSON response (for call sites that don't branch on status). */
export async function apiJson<T = any>(path: string, body: Record<string, any> = {}): Promise<T> {
  const res = await apiPost(path, body);
  return res.json();
}
