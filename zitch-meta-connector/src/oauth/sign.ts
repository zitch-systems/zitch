/**
 * HMAC-signed, self-describing tokens — the primitive every other OAuth
 * module here is built on (authorization codes are the one exception; they
 * are single-use and live in memory).
 *
 * Deliberately NOT a JWT. A JWT carries a client-supplied `alg` header, and
 * honouring that header is the root of the entire algorithm-confusion family
 * of bugs (`alg: none`, RS256→HS256 downgrade). These tokens have exactly one
 * algorithm, fixed in code, with no negotiable field — so that class of
 * vulnerability cannot be expressed. The format is:
 *
 *     base64url(JSON payload) "." base64url(HMAC-SHA256(payload))
 *
 * Everything issued this way is verifiable from the signing key alone, with
 * no server-side storage. That matters because this connector runs as a
 * single small instance that restarts on every deploy: a token store in
 * memory would silently sign every operator out on each redeploy, and a
 * database is far more machinery than a maintenance tool warrants.
 */
import { createHmac, timingSafeEqual } from 'node:crypto';

export function b64uEncode(input: Buffer | string): string {
  return Buffer.from(input).toString('base64url');
}

export function b64uDecode(input: string): Buffer {
  return Buffer.from(input, 'base64url');
}

function hmac(payload: string, key: string): Buffer {
  return createHmac('sha256', key).update(payload).digest();
}

/** Signs `payload` (any JSON-serialisable object) into a compact token. */
export function sign(payload: Record<string, unknown>, key: string): string {
  const body = b64uEncode(JSON.stringify(payload));
  return `${body}.${b64uEncode(hmac(body, key))}`;
}

/**
 * Verifies and decodes a token produced by `sign`.
 *
 * Returns undefined — never throws, and never distinguishes *why* — for a
 * malformed token, a bad signature, or an expired one. A caller that could
 * tell "signature wrong" from "expired" would hand an attacker an oracle for
 * probing the signing key.
 *
 * Enforces `exp` (seconds since epoch) when the payload carries one.
 */
export function verify<T extends Record<string, unknown>>(
  token: string,
  key: string,
  now: number = Date.now(),
): T | undefined {
  const parts = token.split('.');
  if (parts.length !== 2) return undefined;
  const [body, signature] = parts as [string, string];
  if (!body || !signature) return undefined;

  const expected = hmac(body, key);
  const presented = b64uDecode(signature);
  if (presented.length !== expected.length) return undefined;
  if (!timingSafeEqual(presented, expected)) return undefined;

  let payload: T;
  try {
    payload = JSON.parse(b64uDecode(body).toString('utf8')) as T;
  } catch {
    return undefined;
  }
  if (payload === null || typeof payload !== 'object') return undefined;

  const exp = (payload as { exp?: unknown }).exp;
  if (typeof exp === 'number' && now >= exp * 1000) return undefined;

  return payload;
}

/** Constant-time comparison of two strings (secrets, PKCE verifiers, …). */
export function safeEqualString(a: string, b: string): boolean {
  const bufA = Buffer.from(a);
  const bufB = Buffer.from(b);
  if (bufA.length !== bufB.length) {
    timingSafeEqual(bufA, bufA); // keep the timing profile flat on length mismatch
    return false;
  }
  return timingSafeEqual(bufA, bufB);
}
