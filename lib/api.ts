import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';

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
  return fetch(`${baseUrl}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(token ? { access_token: token, ...body } : body),
  });
}

/** POST and parse the JSON response (for call sites that don't branch on status). */
export async function apiJson<T = any>(path: string, body: Record<string, any> = {}): Promise<T> {
  const res = await apiPost(path, body);
  return res.json();
}
