/**
 * The OAuth 2.1 endpoints, as an Express router.
 *
 *   GET  /.well-known/oauth-authorization-server    RFC 8414 AS metadata
 *   GET  /.well-known/oauth-protected-resource[/mcp] RFC 9728 PR metadata
 *   POST /oauth/register                            RFC 7591 dynamic registration
 *   GET  /oauth/authorize                           sign-in + consent page
 *   POST /oauth/authorize                           approve -> redirect with code
 *   POST /oauth/token                               code / refresh_token grants
 *
 * Deliberately absent: any endpoint that could reveal META_ACCESS_TOKEN.
 * OAuth here authenticates the CALLER to this connector; the Meta credential
 * stays server-side in metaClient.ts and is never part of any grant, token,
 * or response. An OAuth token proves "you may ask this connector to read
 * Meta config for you" — it is never itself a Meta credential.
 */
import express, { Router, type Request, type Response } from 'express';

import type { Config } from '../config.js';
import { renderConsentPage, renderErrorPage } from './consent.js';
import {
  AuthorizationCodeStore,
  startCodeSweep,
  verifyPkce,
} from './codes.js';
import {
  ClientError,
  checkClientSecret,
  lookupClient,
  registerClient,
  validateRedirectUri,
  type ClientMetadata,
} from './clients.js';
import { sign, verify, safeEqualString } from './sign.js';
import {
  ACCESS_TOKEN_TTL_SECONDS,
  issueAccessToken,
  issueRefreshToken,
  verifyRefreshToken,
} from './tokens.js';

export const OAUTH_SCOPE = 'mcp:read';

/** The connector's public origin. Behind Render's proxy the socket is plain
 * HTTP, so the scheme has to come from X-Forwarded-Proto or every URL we
 * advertise (and every redirect we build) would be http:// — which OAuth 2.1
 * forbids and Claude would refuse. */
export function baseUrl(req: Request, config: Config): string {
  if (config.publicBaseUrl) return config.publicBaseUrl.replace(/\/+$/, '');
  const proto = (req.header('x-forwarded-proto') || req.protocol || 'https').split(',')[0]!.trim();
  const host = (req.header('x-forwarded-host') || req.header('host') || 'localhost').split(',')[0]!.trim();
  return `${proto}://${host}`;
}

/** The RFC 8707 resource identifier for this server's MCP endpoint. */
export function resourceUri(req: Request, config: Config): string {
  return `${baseUrl(req, config)}/mcp`;
}

interface PendingRequest extends Record<string, unknown> {
  typ: 'authz';
  cid: string;
  ru: string;
  cc: string;
  st: string | undefined;
  sc: string;
  rs: string | undefined;
  exp: number;
}

function noStore(res: Response): void {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Pragma', 'no-cache');
}

function oauthError(res: Response, status: number, code: string, description: string): void {
  noStore(res);
  res.status(status).json({ error: code, error_description: description });
}

function param(req: Request, name: string): string | undefined {
  const fromQuery = req.query[name];
  if (typeof fromQuery === 'string' && fromQuery) return fromQuery;
  const body = req.body as Record<string, unknown> | undefined;
  const fromBody = body?.[name];
  if (typeof fromBody === 'string' && fromBody) return fromBody;
  return undefined;
}

/** Redirects back to the client with an OAuth error, per RFC 6749 §4.1.2.1.
 * Only ever called once the redirect_uri has been validated — an unvalidated
 * one must produce a rendered error page instead, never a redirect. */
function redirectError(
  res: Response,
  redirectUri: string,
  code: string,
  description: string,
  state: string | undefined,
): void {
  const url = new URL(redirectUri);
  url.searchParams.set('error', code);
  url.searchParams.set('error_description', description);
  if (state) url.searchParams.set('state', state);
  noStore(res);
  res.redirect(302, url.toString());
}

export function buildOAuthRouter(config: Config): Router {
  const router = Router();
  const codes = new AuthorizationCodeStore();
  startCodeSweep(codes);

  const key = config.oauthSigningKey;
  const staticClient = config.oauthStaticClient;
  const allowedHosts = config.oauthAllowedRedirectHosts;

  // OAuth token and authorize endpoints take form-encoded bodies (RFC 6749
  // §4.1.3 / §3.2). The app-level express.json() does not cover that.
  router.use(express.urlencoded({ extended: false, limit: '64kb' }));

  // ---------------------------------------------------------------- metadata
  const asMetadata = (req: Request) => {
    const base = baseUrl(req, config);
    return {
      issuer: base,
      authorization_endpoint: `${base}/oauth/authorize`,
      token_endpoint: `${base}/oauth/token`,
      registration_endpoint: `${base}/oauth/register`,
      scopes_supported: [OAUTH_SCOPE],
      response_types_supported: ['code'],
      response_modes_supported: ['query'],
      grant_types_supported: ['authorization_code', 'refresh_token'],
      token_endpoint_auth_methods_supported: [
        'none',
        'client_secret_post',
        'client_secret_basic',
      ],
      // S256 only. OAuth 2.1 removes `plain`, which protects against nothing
      // an attacker who can read the authorization request cannot defeat.
      code_challenge_methods_supported: ['S256'],
      service_documentation: 'https://github.com/zitch-systems/zitch/tree/main/zitch-meta-connector',
    };
  };

  const prMetadata = (req: Request) => {
    const base = baseUrl(req, config);
    return {
      resource: resourceUri(req, config),
      authorization_servers: [base],
      scopes_supported: [OAUTH_SCOPE],
      bearer_methods_supported: ['header'],
      resource_documentation: 'https://github.com/zitch-systems/zitch/tree/main/zitch-meta-connector',
    };
  };

  router.get('/.well-known/oauth-authorization-server', (req, res) => {
    noStore(res);
    res.status(200).json(asMetadata(req));
  });

  // Both the bare path and the /mcp-suffixed form: RFC 9728 locates the
  // document by appending the resource's path, and clients differ on which
  // they try first.
  for (const path of ['/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/mcp']) {
    router.get(path, (req, res) => {
      noStore(res);
      res.status(200).json(prMetadata(req));
    });
  }

  // ------------------------------------------------------------ registration
  router.post('/oauth/register', (req, res) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    try {
      const client = registerClient(
        {
          redirectUris: body.redirect_uris,
          clientName: body.client_name,
          tokenEndpointAuthMethod: body.token_endpoint_auth_method,
        },
        { key, allowedHosts },
      );
      noStore(res);
      res.status(201).json({
        client_id: client.clientId,
        ...(client.clientSecret ? { client_secret: client.clientSecret } : {}),
        client_id_issued_at: client.metadata.iat,
        // 0 = does not expire. The credential is derived from the signing key,
        // so it stays valid exactly as long as that key does.
        ...(client.clientSecret ? { client_secret_expires_at: 0 } : {}),
        redirect_uris: client.metadata.redirectUris,
        client_name: client.metadata.clientName,
        token_endpoint_auth_method: client.metadata.tokenEndpointAuthMethod,
        grant_types: ['authorization_code', 'refresh_token'],
        response_types: ['code'],
        scope: OAUTH_SCOPE,
      });
    } catch (err) {
      if (err instanceof ClientError) {
        oauthError(res, 400, err.code, err.message);
        return;
      }
      oauthError(res, 400, 'invalid_client_metadata', 'Could not register this client.');
    }
  });

  // ------------------------------------------------------------- authorize
  /** Shared validation for GET and POST /authorize. Returns either a rendered
   * failure (already sent) or the validated request. */
  function validateAuthorizeRequest(
    req: Request,
    res: Response,
  ): { client: ClientMetadata; redirectUri: string; state: string | undefined } | undefined {
    const clientId = param(req, 'client_id');
    if (!clientId) {
      res.status(400).type('html').send(renderErrorPage('Invalid request', 'Missing client_id.'));
      return undefined;
    }
    const client = lookupClient(clientId, { key, staticClient });
    if (!client) {
      res
        .status(400)
        .type('html')
        .send(renderErrorPage('Unknown client', 'This client_id is not registered with this server.'));
      return undefined;
    }

    const requested = param(req, 'redirect_uri');
    // With exactly one registered URI, RFC 6749 lets the client omit it.
    const redirectUri = requested ?? (client.redirectUris.length === 1 ? client.redirectUris[0]! : undefined);
    if (!redirectUri) {
      res
        .status(400)
        .type('html')
        .send(renderErrorPage('Invalid request', 'Missing redirect_uri.'));
      return undefined;
    }
    // Exact match against what the client registered — never a prefix or
    // host-only comparison, which is how open redirectors get built.
    if (!client.redirectUris.some((uri) => safeEqualString(uri, redirectUri))) {
      res
        .status(400)
        .type('html')
        .send(
          renderErrorPage(
            'Invalid redirect URI',
            'This redirect_uri is not registered for this client, so the request cannot be completed.',
          ),
        );
      return undefined;
    }
    try {
      validateRedirectUri(redirectUri, allowedHosts);
    } catch {
      res
        .status(400)
        .type('html')
        .send(renderErrorPage('Invalid redirect URI', 'This redirect_uri is not permitted by this server.'));
      return undefined;
    }

    return { client, redirectUri, state: param(req, 'state') };
  }

  router.get('/oauth/authorize', (req, res) => {
    const validated = validateAuthorizeRequest(req, res);
    if (!validated) return;
    const { client, redirectUri, state } = validated;

    // Past this point the redirect_uri is trusted, so failures go back to the
    // client as OAuth errors rather than dead-ending on an HTML page.
    if (param(req, 'response_type') !== 'code') {
      redirectError(res, redirectUri, 'unsupported_response_type', 'Only response_type=code is supported.', state);
      return;
    }
    const challenge = param(req, 'code_challenge');
    if (!challenge) {
      redirectError(res, redirectUri, 'invalid_request', 'PKCE is required: code_challenge is missing.', state);
      return;
    }
    if ((param(req, 'code_challenge_method') ?? 'plain') !== 'S256') {
      redirectError(
        res,
        redirectUri,
        'invalid_request',
        'code_challenge_method must be S256; plain is not accepted.',
        state,
      );
      return;
    }

    const scope = param(req, 'scope') ?? OAUTH_SCOPE;
    const pending: PendingRequest = {
      typ: 'authz',
      cid: param(req, 'client_id')!,
      ru: redirectUri,
      cc: challenge,
      st: state,
      sc: scope,
      rs: param(req, 'resource'),
      // 10 minutes to type a passphrase; the signed blob is worthless after.
      exp: Math.floor(Date.now() / 1000) + 600,
    };

    noStore(res);
    res.status(200).type('html').send(
      renderConsentPage({
        clientName: client.clientName,
        redirectUri,
        scope,
        request: sign(pending, key),
      }),
    );
  });

  router.post('/oauth/authorize', (req, res) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const blob = typeof body.request === 'string' ? body.request : '';
    const pending = verify<PendingRequest>(blob, key);
    if (!pending || pending.typ !== 'authz') {
      res
        .status(400)
        .type('html')
        .send(
          renderErrorPage(
            'Request expired',
            'This authorization request is no longer valid. Start again from the application.',
          ),
        );
      return;
    }

    const password = typeof body.password === 'string' ? body.password : '';
    if (!safeEqualString(password, config.oauthLoginPassword)) {
      // Re-render rather than redirect: a wrong passphrase is the operator's
      // to correct, and telling the client about it would leak that someone
      // reached the consent screen at all.
      noStore(res);
      const client = lookupClient(pending.cid, { key, staticClient });
      res.status(401).type('html').send(
        renderConsentPage({
          clientName: client?.clientName ?? 'Unknown client',
          redirectUri: pending.ru,
          scope: pending.sc,
          request: blob,
          error: 'That passphrase was not correct.',
        }),
      );
      return;
    }

    const code = codes.issue({
      clientId: pending.cid,
      redirectUri: pending.ru,
      codeChallenge: pending.cc,
      scope: pending.sc,
      resource: pending.rs,
    });

    const url = new URL(pending.ru);
    url.searchParams.set('code', code);
    if (pending.st) url.searchParams.set('state', pending.st);
    noStore(res);
    res.redirect(302, url.toString());
  });

  // ----------------------------------------------------------------- token
  /** Resolves client credentials from either Basic auth or the form body. */
  function clientCredentials(req: Request): { clientId?: string; clientSecret?: string } {
    const header = req.header('authorization');
    if (header?.toLowerCase().startsWith('basic ')) {
      const decoded = Buffer.from(header.slice(6).trim(), 'base64').toString('utf8');
      const sep = decoded.indexOf(':');
      if (sep > 0) {
        return {
          clientId: decodeURIComponent(decoded.slice(0, sep)),
          clientSecret: decodeURIComponent(decoded.slice(sep + 1)),
        };
      }
    }
    return { clientId: param(req, 'client_id'), clientSecret: param(req, 'client_secret') };
  }

  /** Authenticates the client. Public clients (auth method `none`) are
   * allowed through without a secret — their protection is PKCE, which is
   * mandatory here for every client regardless. */
  function authenticateClient(req: Request): { ok: boolean; clientId?: string; client?: ClientMetadata } {
    const { clientId, clientSecret } = clientCredentials(req);
    if (!clientId) return { ok: false };
    const client = lookupClient(clientId, { key, staticClient });
    if (!client) return { ok: false };
    if (client.tokenEndpointAuthMethod === 'none') return { ok: true, clientId, client };
    if (!clientSecret) return { ok: false };
    if (!checkClientSecret(clientId, clientSecret, { key, staticClient })) return { ok: false };
    return { ok: true, clientId, client };
  }

  router.post('/oauth/token', (req, res) => {
    const grantType = param(req, 'grant_type');
    const auth = authenticateClient(req);
    if (!auth.ok || !auth.clientId) {
      noStore(res);
      res
        .status(401)
        .set('WWW-Authenticate', 'Basic realm="zitch-meta-connector"')
        .json({ error: 'invalid_client', error_description: 'Client authentication failed.' });
      return;
    }
    const clientId = auth.clientId;

    if (grantType === 'authorization_code') {
      const code = param(req, 'code');
      const verifier = param(req, 'code_verifier');
      if (!code || !verifier) {
        oauthError(res, 400, 'invalid_request', 'code and code_verifier are both required.');
        return;
      }
      const entry = codes.redeem(code);
      if (!entry) {
        oauthError(res, 400, 'invalid_grant', 'This authorization code is invalid, expired, or already used.');
        return;
      }
      // The code was issued to a specific client and redirect_uri; a mismatch
      // means someone is replaying another client's code.
      if (!safeEqualString(entry.clientId, clientId)) {
        oauthError(res, 400, 'invalid_grant', 'This authorization code was issued to a different client.');
        return;
      }
      const presentedRedirect = param(req, 'redirect_uri');
      if (presentedRedirect && !safeEqualString(presentedRedirect, entry.redirectUri)) {
        oauthError(res, 400, 'invalid_grant', 'redirect_uri does not match the authorization request.');
        return;
      }
      if (!verifyPkce(verifier, entry.codeChallenge)) {
        oauthError(res, 400, 'invalid_grant', 'PKCE verification failed.');
        return;
      }

      const claims = { clientId, scope: entry.scope, audience: entry.resource };
      noStore(res);
      res.status(200).json({
        access_token: issueAccessToken(claims, key),
        token_type: 'Bearer',
        expires_in: ACCESS_TOKEN_TTL_SECONDS,
        refresh_token: issueRefreshToken(claims, key),
        scope: entry.scope,
      });
      return;
    }

    if (grantType === 'refresh_token') {
      const presented = param(req, 'refresh_token');
      if (!presented) {
        oauthError(res, 400, 'invalid_request', 'refresh_token is required.');
        return;
      }
      const claims = verifyRefreshToken(presented, key);
      if (!claims) {
        oauthError(res, 400, 'invalid_grant', 'This refresh token is invalid or expired.');
        return;
      }
      if (!safeEqualString(claims.clientId, clientId)) {
        oauthError(res, 400, 'invalid_grant', 'This refresh token was issued to a different client.');
        return;
      }
      noStore(res);
      res.status(200).json({
        access_token: issueAccessToken(claims, key),
        token_type: 'Bearer',
        expires_in: ACCESS_TOKEN_TTL_SECONDS,
        // A NEW refresh token, but note the previous one stays valid until it
        // expires — these are stateless, so there is nothing to revoke it
        // against. See the header comment in tokens.ts for why, and for the
        // key-rotation lever that does revoke everything at once.
        refresh_token: issueRefreshToken(claims, key),
        scope: claims.scope,
      });
      return;
    }

    oauthError(res, 400, 'unsupported_grant_type', 'Supported grants: authorization_code, refresh_token.');
  });

  return router;
}
