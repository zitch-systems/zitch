/**
 * Authorization codes + PKCE (RFC 7636).
 *
 * These are the one thing here that is NOT a signed, stateless token, and the
 * reason is single-use: an authorization code must be redeemable exactly
 * once, and "has this already been redeemed?" is a fact you can only answer
 * from state. A signed self-contained code would stay valid for its whole
 * lifetime no matter how many times it was replayed.
 *
 * In-memory is acceptable precisely because the lifetime is ~60 seconds: the
 * window between the browser redirect and the client's token call. A restart
 * inside that window costs one retried sign-in, not a lost session (access
 * and refresh tokens are signed and survive restarts — see tokens.ts).
 *
 * PKCE is mandatory and S256-only. `plain` is refused outright: it offers no
 * protection at all against an attacker who can observe the authorization
 * request, which is the entire threat PKCE exists to address, and OAuth 2.1
 * drops it.
 */
import { createHash, randomBytes } from 'node:crypto';

import { b64uEncode, safeEqualString } from './sign.js';

export interface AuthorizationCode {
  clientId: string;
  redirectUri: string;
  codeChallenge: string;
  scope: string;
  /** RFC 8707 resource indicator — the MCP endpoint this code is bound to. */
  resource: string | undefined;
  expiresAt: number;
}

/** 60s: long enough for a browser redirect plus the client's token call,
 * short enough that a leaked code from a proxy log is almost always dead. */
const CODE_TTL_MS = 60_000;

export class AuthorizationCodeStore {
  private readonly codes = new Map<string, AuthorizationCode>();

  issue(entry: Omit<AuthorizationCode, 'expiresAt'>, now: number = Date.now()): string {
    const code = randomBytes(32).toString('base64url');
    this.codes.set(code, { ...entry, expiresAt: now + CODE_TTL_MS });
    return code;
  }

  /**
   * Redeems a code, removing it so a replay finds nothing. Returns undefined
   * for unknown, expired, or already-redeemed codes alike — the caller cannot
   * tell those apart, and neither can an attacker probing the endpoint.
   */
  redeem(code: string, now: number = Date.now()): AuthorizationCode | undefined {
    const entry = this.codes.get(code);
    if (!entry) return undefined;
    // Delete on ANY lookup, valid or expired: a code presented once is spent,
    // even if this particular attempt is about to be rejected.
    this.codes.delete(code);
    if (now >= entry.expiresAt) return undefined;
    return entry;
  }

  sweep(now: number = Date.now()): void {
    for (const [code, entry] of this.codes) {
      if (now >= entry.expiresAt) this.codes.delete(code);
    }
  }

  get size(): number {
    return this.codes.size;
  }
}

/** S256 transform: BASE64URL(SHA256(ASCII(verifier))). */
export function s256(verifier: string): string {
  return b64uEncode(createHash('sha256').update(verifier, 'ascii').digest());
}

/** Verifies a PKCE code_verifier against the stored S256 challenge. */
export function verifyPkce(verifier: string, challenge: string): boolean {
  // RFC 7636 §4.1 bounds the verifier at 43..128 chars; anything outside that
  // is malformed rather than merely wrong.
  if (verifier.length < 43 || verifier.length > 128) return false;
  return safeEqualString(s256(verifier), challenge);
}

export function startCodeSweep(store: AuthorizationCodeStore, intervalMs = 60_000): () => void {
  const timer = setInterval(() => store.sweep(), intervalMs);
  timer.unref();
  return () => clearInterval(timer);
}
