/**
 * Per-request authentication.
 *
 * Every request — not just the first one on a connection — must present a
 * credential. There is no session, no cookie, no "authenticate once and reuse
 * a token": the MCP Streamable HTTP transport can span many independent HTTP
 * requests, and each one is checked here before it reaches the transport or
 * any tool handler.
 *
 * TWO credential types are accepted, and which one you use depends on how you
 * reached the server:
 *
 *   1. **An OAuth 2.1 access token** (`Authorization: Bearer <token>`), issued
 *      by this server's own authorization endpoints — see src/oauth/. This is
 *      what Claude's web custom-connector UI uses, because that UI can only
 *      sign in through a browser OAuth flow.
 *   2. **The raw CONNECTOR_API_KEY**, as `Authorization: Bearer <key>` or
 *      `X-Connector-Api-Key: <key>`. Kept because it is what a curl check, a
 *      health probe, or a future GPT Action can actually use — none of them
 *      can run a browser redirect flow. Dropping it would have broken every
 *      non-browser caller for no gain, since it remains exactly as strong a
 *      secret as it was before OAuth existed.
 *
 * Both paths land on the same read-only tools with the same rate limits and
 * the same audit logging. Neither ever exposes META_ACCESS_TOKEN: an OAuth
 * token authenticates the CALLER to this connector and is never itself a Meta
 * credential.
 */
import type { NextFunction, Request, Response } from 'express';

import type { Config } from './config.js';
import { safeEqualString } from './oauth/sign.js';
import { verifyAccessToken } from './oauth/tokens.js';
import { resourceUri } from './oauth/router.js';

function bearerToken(req: Request): string | undefined {
  const auth = req.header('authorization');
  if (auth?.toLowerCase().startsWith('bearer ')) return auth.slice(7).trim() || undefined;
  return undefined;
}

function extractKey(req: Request): string | undefined {
  const bearer = bearerToken(req);
  if (bearer) return bearer;
  const header = req.header('x-connector-api-key');
  return header?.trim() || undefined;
}

export interface AuthResult {
  ok: boolean;
  /** First 6 chars of the presented credential, for audit logs — never the
   * full value. */
  keyFingerprint: string;
  /** How the caller authenticated, for the audit trail. */
  method: 'api_key' | 'oauth' | 'none';
}

export function checkAuth(req: Request, config: Config): AuthResult {
  const presented = extractKey(req);
  if (!presented) {
    return { ok: false, keyFingerprint: '(none)', method: 'none' };
  }
  const keyFingerprint = presented.slice(0, 6);

  // API key first: it is a single constant-time compare, whereas the OAuth
  // path parses and verifies a signature. Order is not security-relevant —
  // both run to completion on failure — but the common non-browser case is
  // cheaper this way.
  if (safeEqualString(presented, config.connectorApiKey)) {
    return { ok: true, keyFingerprint, method: 'api_key' };
  }

  const bearer = bearerToken(req);
  if (bearer) {
    const claims = verifyAccessToken(bearer, config.oauthSigningKey, resourceUri(req, config));
    if (claims) {
      // NOT the token's own leading characters: every signed token begins with
      // the same encoded `{"typ":…` bytes, so those identify nothing and would
      // make every OAuth caller look identical in the audit log. The per-token
      // random id does distinguish them, and reveals nothing usable.
      return { ok: true, keyFingerprint: claims.tokenId?.slice(0, 8) ?? 'oauth', method: 'oauth' };
    }
  }

  return { ok: false, keyFingerprint, method: 'none' };
}

/**
 * Express middleware; stores the result on `res.locals` for the audit logger
 * and short-circuits with 401 on failure.
 *
 * The 401 carries a `WWW-Authenticate` header pointing at this server's
 * protected-resource metadata (RFC 9728). That header is how an MCP client
 * that has never seen this server discovers where to authenticate — without
 * it, Claude's connector UI has no way to find the authorization endpoints
 * and simply reports that the server rejected it.
 */
export function requireApiKey(config: Config) {
  return (req: Request, res: Response, next: NextFunction): void => {
    const result = checkAuth(req, config);
    res.locals.keyFingerprint = result.keyFingerprint;
    res.locals.authMethod = result.method;
    if (!result.ok) {
      const metadataUrl = `${resourceUri(req, config).replace(/\/mcp$/, '')}/.well-known/oauth-protected-resource`;
      res
        .status(401)
        .set(
          'WWW-Authenticate',
          `Bearer realm="zitch-meta-connector", resource_metadata="${metadataUrl}"`,
        )
        .json({
          error: 'unauthorized',
          message:
            'Authentication required: present an OAuth 2.1 access token or the CONNECTOR_API_KEY.',
        });
      return;
    }
    next();
  };
}
