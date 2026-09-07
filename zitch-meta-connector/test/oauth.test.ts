import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { randomBytes } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import express from 'express';

import type { Config } from '../src/config.js';
import { sign, verify, safeEqualString } from '../src/oauth/sign.js';
import { AuthorizationCodeStore, s256, verifyPkce } from '../src/oauth/codes.js';
import {
  ClientError,
  checkClientSecret,
  lookupClient,
  registerClient,
  validateRedirectUri,
} from '../src/oauth/clients.js';
import {
  issueAccessToken,
  issueRefreshToken,
  verifyAccessToken,
  verifyRefreshToken,
} from '../src/oauth/tokens.js';
import { buildOAuthRouter } from '../src/oauth/router.js';
import { checkAuth, requireApiKey } from '../src/auth.js';

const KEY = 'test-signing-key-0000000000000000';
const PASSWORD = 'operator-passphrase-0000000000';
const ALLOWED = ['chatgpt.com', 'claude.ai', 'claude.com', 'localhost', '127.0.0.1'] as const;

function testConfig(overrides: Partial<Config> = {}): Config {
  return {
    metaAccessToken: 'meta-token-never-exposed',
    metaWabaId: '111',
    metaPhoneNumberId: '222',
    metaAppId: undefined,
    metaAppSecret: undefined,
    connectorApiKey: 'a'.repeat(32),
    port: 0,
    graphApiVersion: 'v21.0',
    graphApiBaseUrl: 'https://graph.facebook.com',
    graphTimeoutMs: 1000,
    rateLimitWindowMs: 60_000,
    rateLimitMaxRequests: 1000,
    ipRateLimitMaxRequests: 1000,
    readOnly: true,
    requireWriteConfirmation: true,
    publicBaseUrl: undefined,
    oauthSigningKey: KEY,
    oauthLoginPassword: PASSWORD,
    oauthAllowedRedirectHosts: ALLOWED,
    oauthStaticClient: undefined,
    ...overrides,
  };
}

// --------------------------------------------------------------------- sign
describe('signed tokens', () => {
  it('round-trips a payload', () => {
    const token = sign({ typ: 'x', v: 1 }, KEY);
    expect(verify<{ typ: string; v: number }>(token, KEY)?.v).toBe(1);
  });

  it('rejects a token signed with a different key', () => {
    expect(verify(sign({ a: 1 }, KEY), 'other-key-0000000000000000000000')).toBeUndefined();
  });

  it('rejects a tampered payload', () => {
    const token = sign({ admin: false }, KEY);
    const forged = `${Buffer.from(JSON.stringify({ admin: true })).toString('base64url')}.${token.split('.')[1]}`;
    expect(verify(forged, KEY)).toBeUndefined();
  });

  it('rejects an expired token', () => {
    const token = sign({ exp: Math.floor(Date.now() / 1000) - 1 }, KEY);
    expect(verify(token, KEY)).toBeUndefined();
  });

  it('rejects malformed input without throwing', () => {
    for (const bad of ['', 'nodot', 'a.b.c', '...', 'Zz.Zz']) {
      expect(() => verify(bad, KEY)).not.toThrow();
      expect(verify(bad, KEY)).toBeUndefined();
    }
  });

  it('has no client-controlled algorithm field to confuse', () => {
    // The whole point of not using a JWT here: there is no `alg` to override.
    const decoded = JSON.parse(Buffer.from(sign({ a: 1 }, KEY).split('.')[0]!, 'base64url').toString());
    expect(decoded).not.toHaveProperty('alg');
  });
});

// --------------------------------------------------------------------- PKCE
describe('PKCE', () => {
  it('accepts a correct S256 verifier', () => {
    const verifier = randomBytes(32).toString('base64url');
    expect(verifyPkce(verifier, s256(verifier))).toBe(true);
  });

  it('rejects a wrong verifier', () => {
    const verifier = randomBytes(32).toString('base64url');
    const other = randomBytes(32).toString('base64url');
    expect(verifyPkce(other, s256(verifier))).toBe(false);
  });

  it('rejects a `plain`-style verifier presented against an S256 challenge', () => {
    // If plain were silently tolerated, sending challenge === verifier would
    // pass. It must not.
    const verifier = randomBytes(32).toString('base64url');
    expect(verifyPkce(verifier, verifier)).toBe(false);
  });

  it('enforces the RFC 7636 length bounds', () => {
    expect(verifyPkce('short', s256('short'))).toBe(false);
    const tooLong = 'a'.repeat(129);
    expect(verifyPkce(tooLong, s256(tooLong))).toBe(false);
  });
});

// ------------------------------------------------------------ code store
describe('authorization codes', () => {
  const entry = {
    clientId: 'cid',
    redirectUri: 'https://claude.ai/api/mcp/auth_callback',
    codeChallenge: 'challenge',
    scope: 'mcp:read',
    resource: undefined,
  };

  it('redeems exactly once', () => {
    const store = new AuthorizationCodeStore();
    const code = store.issue(entry);
    expect(store.redeem(code)).toBeDefined();
    expect(store.redeem(code)).toBeUndefined();
  });

  it('rejects an expired code', () => {
    const store = new AuthorizationCodeStore();
    const now = 1_000_000;
    const code = store.issue(entry, now);
    expect(store.redeem(code, now + 61_000)).toBeUndefined();
  });

  it('spends a code even when the attempt is rejected, so it cannot be retried', () => {
    const store = new AuthorizationCodeStore();
    const now = 1_000_000;
    const code = store.issue(entry, now);
    store.redeem(code, now + 61_000); // expired -> rejected
    expect(store.redeem(code, now)).toBeUndefined(); // and now gone entirely
  });
});

// -------------------------------------------------------------- clients
describe('client registration', () => {
  it('registers and looks up a client statelessly', () => {
    const client = registerClient(
      { redirectUris: ['https://claude.ai/api/mcp/auth_callback'], clientName: 'Claude' },
      { key: KEY, allowedHosts: ALLOWED },
    );
    const found = lookupClient(client.clientId, { key: KEY });
    expect(found?.clientName).toBe('Claude');
    expect(found?.redirectUris).toEqual(['https://claude.ai/api/mcp/auth_callback']);
  });

  it('derives a verifiable client_secret', () => {
    const client = registerClient(
      { redirectUris: ['https://claude.ai/api/mcp/auth_callback'] },
      { key: KEY, allowedHosts: ALLOWED },
    );
    expect(checkClientSecret(client.clientId, client.clientSecret!, { key: KEY })).toBe(true);
    expect(checkClientSecret(client.clientId, 'wrong-secret', { key: KEY })).toBe(false);
  });

  it('does not honour a client_id forged with another key', () => {
    const forged = sign({ typ: 'client', ru: ['https://evil.test/cb'], cn: 'x', am: 'none', iat: 0 }, 'wrong-key-000000');
    expect(lookupClient(forged, { key: KEY })).toBeUndefined();
  });

  it('rejects a redirect URI outside the allowlist', () => {
    expect(() => validateRedirectUri('https://evil.test/cb', ALLOWED)).toThrow(ClientError);
  });

  it('allows a subdomain of an allowed host', () => {
    expect(() => validateRedirectUri('https://api.claude.ai/cb', ALLOWED)).not.toThrow();
  });

  it('allows ChatGPT dynamic-client callbacks', () => {
    expect(() => validateRedirectUri('https://chatgpt.com/connector_platform_oauth_redirect', ALLOWED)).not.toThrow();
  });

  it('rejects plain http except on loopback', () => {
    expect(() => validateRedirectUri('http://claude.ai/cb', ALLOWED)).toThrow(ClientError);
    expect(() => validateRedirectUri('http://localhost:1234/cb', ALLOWED)).not.toThrow();
  });

  it('rejects a redirect URI carrying a fragment', () => {
    expect(() => validateRedirectUri('https://claude.ai/cb#x', ALLOWED)).toThrow(ClientError);
  });

  it('refuses registration with no redirect_uris', () => {
    expect(() => registerClient({ redirectUris: [] }, { key: KEY, allowedHosts: ALLOWED })).toThrow(ClientError);
  });
});

// --------------------------------------------------------------- tokens
describe('access and refresh tokens', () => {
  const claims = { clientId: 'cid', scope: 'mcp:read', audience: 'https://x.test/mcp' };

  it('issues and verifies an access token', () => {
    expect(verifyAccessToken(issueAccessToken(claims, KEY), KEY, 'https://x.test/mcp')?.clientId).toBe('cid');
  });

  it('refuses a refresh token presented as an access token', () => {
    // Both are signed with the same key; only the `typ` claim separates them.
    expect(verifyAccessToken(issueRefreshToken(claims, KEY), KEY)).toBeUndefined();
  });

  it('refuses an access token presented as a refresh token', () => {
    expect(verifyRefreshToken(issueAccessToken(claims, KEY), KEY)).toBeUndefined();
  });

  it('rejects a token minted for a different audience', () => {
    expect(verifyAccessToken(issueAccessToken(claims, KEY), KEY, 'https://other.test/mcp')).toBeUndefined();
  });

  it('accepts an audience-less token (client predating RFC 8707 support)', () => {
    const noAud = issueAccessToken({ ...claims, audience: undefined }, KEY);
    expect(verifyAccessToken(noAud, KEY, 'https://x.test/mcp')).toBeDefined();
  });
});

// ------------------------------------------------------- audit identity
describe('audit fingerprint for OAuth callers', () => {
  const config = testConfig();
  const req = (headers: Record<string, string>) =>
    ({ header: (n: string) => headers[n.toLowerCase()] }) as never;

  it('does not use the token prefix, which is identical for every token', () => {
    const a = issueAccessToken({ clientId: 'c1', scope: 'mcp:read', audience: undefined }, KEY);
    const b = issueAccessToken({ clientId: 'c2', scope: 'mcp:read', audience: undefined }, KEY);
    // The shared encoded `{"typ":…` prefix — if the fingerprint were just the
    // first characters of the token, these two callers would be
    // indistinguishable in the log.
    expect(a.slice(0, 6)).toBe(b.slice(0, 6));

    const fpA = checkAuth(req({ authorization: `Bearer ${a}` }), config).keyFingerprint;
    const fpB = checkAuth(req({ authorization: `Bearer ${b}` }), config).keyFingerprint;
    expect(fpA).not.toBe(fpB);
    expect(fpA).not.toBe(a.slice(0, 6));
  });

  it('never puts any part of the token secret in the fingerprint', () => {
    const token = issueAccessToken({ clientId: 'c1', scope: 'mcp:read', audience: undefined }, KEY);
    const { keyFingerprint, method } = checkAuth(req({ authorization: `Bearer ${token}` }), config);
    expect(method).toBe('oauth');
    // The signature segment is the secret part; the fingerprint is the token's
    // own random id, which appears in the payload and grants nothing.
    expect(token.split('.')[1]).not.toContain(keyFingerprint);
  });
});

// ------------------------------------------------------ end-to-end flow
describe('OAuth 2.1 authorization-code + PKCE flow over HTTP', () => {
  let base = '';
  let server: ReturnType<express.Express['listen']>;
  const config = testConfig();
  const REDIRECT = 'https://claude.ai/api/mcp/auth_callback';

  beforeAll(async () => {
    const app = express();
    app.use(express.json());
    app.use(buildOAuthRouter(config));
    // A stand-in for /mcp: same auth middleware the real endpoint uses.
    app.post('/mcp', requireApiKey(config), (_req, res) => {
      res.json({ ok: true, method: res.locals.authMethod });
    });
    await new Promise<void>((resolve) => {
      server = app.listen(0, () => resolve());
    });
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  async function register(): Promise<{ client_id: string; client_secret: string }> {
    const res = await fetch(`${base}/oauth/register`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ redirect_uris: [REDIRECT], client_name: 'Claude' }),
    });
    expect(res.status).toBe(201);
    return (await res.json()) as { client_id: string; client_secret: string };
  }

  /** Drives GET /authorize then POST /authorize, returning the redirect URL. */
  async function authorize(clientId: string, challenge: string, state = 'st-1'): Promise<URL> {
    const url = new URL(`${base}/oauth/authorize`);
    url.searchParams.set('response_type', 'code');
    url.searchParams.set('client_id', clientId);
    url.searchParams.set('redirect_uri', REDIRECT);
    url.searchParams.set('code_challenge', challenge);
    url.searchParams.set('code_challenge_method', 'S256');
    url.searchParams.set('state', state);
    const page = await fetch(url);
    expect(page.status).toBe(200);
    const html = await page.text();
    const blob = /name="request" value="([^"]+)"/.exec(html)?.[1];
    expect(blob).toBeTruthy();

    const approved = await fetch(`${base}/oauth/authorize`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ request: blob!, password: PASSWORD }),
      redirect: 'manual',
    });
    expect(approved.status).toBe(302);
    return new URL(approved.headers.get('location')!);
  }

  it('publishes authorization-server metadata', async () => {
    const res = await fetch(`${base}/.well-known/oauth-authorization-server`);
    const body = (await res.json()) as Record<string, unknown>;
    expect(res.status).toBe(200);
    expect(body.code_challenge_methods_supported).toEqual(['S256']);
    expect(body.grant_types_supported).toContain('authorization_code');
    expect(body.registration_endpoint).toContain('/oauth/register');
  });

  it('publishes protected-resource metadata', async () => {
    const res = await fetch(`${base}/.well-known/oauth-protected-resource`);
    const body = (await res.json()) as Record<string, unknown>;
    expect(res.status).toBe(200);
    expect(String(body.resource)).toContain('/mcp');
  });

  it('points an unauthenticated MCP request at the metadata via WWW-Authenticate', async () => {
    const res = await fetch(`${base}/mcp`, { method: 'POST' });
    expect(res.status).toBe(401);
    expect(res.headers.get('www-authenticate')).toContain('resource_metadata=');
  });

  it('completes the full flow and returns a working access token', async () => {
    const client = await register();
    const verifier = randomBytes(32).toString('base64url');
    const redirect = await authorize(client.client_id, s256(verifier));

    expect(redirect.searchParams.get('state')).toBe('st-1');
    const code = redirect.searchParams.get('code');
    expect(code).toBeTruthy();

    const tokenRes = await fetch(`${base}/oauth/token`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        code: code!,
        code_verifier: verifier,
        redirect_uri: REDIRECT,
        client_id: client.client_id,
        client_secret: client.client_secret,
      }),
    });
    expect(tokenRes.status).toBe(200);
    const token = (await tokenRes.json()) as { access_token: string; token_type: string; refresh_token: string };
    expect(token.token_type).toBe('Bearer');

    const mcp = await fetch(`${base}/mcp`, {
      method: 'POST',
      headers: { authorization: `Bearer ${token.access_token}` },
    });
    expect(mcp.status).toBe(200);
    expect(((await mcp.json()) as { method: string }).method).toBe('oauth');
  });

  it('never puts the Meta access token in any OAuth response', async () => {
    const client = await register();
    const verifier = randomBytes(32).toString('base64url');
    const redirect = await authorize(client.client_id, s256(verifier));
    const tokenRes = await fetch(`${base}/oauth/token`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        code: redirect.searchParams.get('code')!,
        code_verifier: verifier,
        client_id: client.client_id,
        client_secret: client.client_secret,
      }),
    });
    const raw = await tokenRes.text();
    expect(raw).not.toContain(config.metaAccessToken);
    const meta = await (await fetch(`${base}/.well-known/oauth-authorization-server`)).text();
    expect(meta).not.toContain(config.metaAccessToken);
  });

  it('rejects a replayed authorization code', async () => {
    const client = await register();
    const verifier = randomBytes(32).toString('base64url');
    const redirect = await authorize(client.client_id, s256(verifier));
    const body = () =>
      new URLSearchParams({
        grant_type: 'authorization_code',
        code: redirect.searchParams.get('code')!,
        code_verifier: verifier,
        client_id: client.client_id,
        client_secret: client.client_secret,
      });
    const headers = { 'content-type': 'application/x-www-form-urlencoded' };

    expect((await fetch(`${base}/oauth/token`, { method: 'POST', headers, body: body() })).status).toBe(200);
    const replay = await fetch(`${base}/oauth/token`, { method: 'POST', headers, body: body() });
    expect(replay.status).toBe(400);
    expect(((await replay.json()) as { error: string }).error).toBe('invalid_grant');
  });

  it('rejects a token exchange with the wrong PKCE verifier', async () => {
    const client = await register();
    const verifier = randomBytes(32).toString('base64url');
    const redirect = await authorize(client.client_id, s256(verifier));
    const res = await fetch(`${base}/oauth/token`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        code: redirect.searchParams.get('code')!,
        code_verifier: randomBytes(32).toString('base64url'),
        client_id: client.client_id,
        client_secret: client.client_secret,
      }),
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe('invalid_grant');
  });

  it('refuses to issue a code without the operator passphrase', async () => {
    const client = await register();
    const url = new URL(`${base}/oauth/authorize`);
    url.searchParams.set('response_type', 'code');
    url.searchParams.set('client_id', client.client_id);
    url.searchParams.set('redirect_uri', REDIRECT);
    url.searchParams.set('code_challenge', s256('x'.repeat(50)));
    url.searchParams.set('code_challenge_method', 'S256');
    const blob = /name="request" value="([^"]+)"/.exec(await (await fetch(url)).text())?.[1];

    const res = await fetch(`${base}/oauth/authorize`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ request: blob!, password: 'wrong' }),
      redirect: 'manual',
    });
    expect(res.status).toBe(401);
    expect(res.headers.get('location')).toBeNull();
  });

  it('refuses code_challenge_method=plain', async () => {
    const client = await register();
    const url = new URL(`${base}/oauth/authorize`);
    url.searchParams.set('response_type', 'code');
    url.searchParams.set('client_id', client.client_id);
    url.searchParams.set('redirect_uri', REDIRECT);
    url.searchParams.set('code_challenge', 'abc');
    url.searchParams.set('code_challenge_method', 'plain');
    const res = await fetch(url, { redirect: 'manual' });
    expect(res.status).toBe(302);
    expect(new URL(res.headers.get('location')!).searchParams.get('error')).toBe('invalid_request');
  });

  it('refuses an authorize request with no PKCE at all', async () => {
    const client = await register();
    const url = new URL(`${base}/oauth/authorize`);
    url.searchParams.set('response_type', 'code');
    url.searchParams.set('client_id', client.client_id);
    url.searchParams.set('redirect_uri', REDIRECT);
    const res = await fetch(url, { redirect: 'manual' });
    expect(res.status).toBe(302);
    expect(new URL(res.headers.get('location')!).searchParams.get('error')).toBe('invalid_request');
  });

  it('will not redirect to an unregistered redirect_uri', async () => {
    const client = await register();
    const url = new URL(`${base}/oauth/authorize`);
    url.searchParams.set('response_type', 'code');
    url.searchParams.set('client_id', client.client_id);
    url.searchParams.set('redirect_uri', 'https://claude.ai/somewhere-else');
    url.searchParams.set('code_challenge', s256('y'.repeat(50)));
    url.searchParams.set('code_challenge_method', 'S256');
    const res = await fetch(url, { redirect: 'manual' });
    // An HTML error, NOT a redirect — bouncing to an unvalidated URI is
    // exactly how an open redirector behaves.
    expect(res.status).toBe(400);
    expect(res.headers.get('location')).toBeNull();
  });

  it('refuses to register a client pointing off the allowlist', async () => {
    const res = await fetch(`${base}/oauth/register`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ redirect_uris: ['https://attacker.test/cb'] }),
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe('invalid_redirect_uri');
  });

  it('exchanges a refresh token and returns a distinct new one', async () => {
    const client = await register();
    const verifier = randomBytes(32).toString('base64url');
    const redirect = await authorize(client.client_id, s256(verifier));
    const headers = { 'content-type': 'application/x-www-form-urlencoded' };
    const first = (await (
      await fetch(`${base}/oauth/token`, {
        method: 'POST',
        headers,
        body: new URLSearchParams({
          grant_type: 'authorization_code',
          code: redirect.searchParams.get('code')!,
          code_verifier: verifier,
          client_id: client.client_id,
          client_secret: client.client_secret,
        }),
      })
    ).json()) as { refresh_token: string };

    const refreshed = await fetch(`${base}/oauth/token`, {
      method: 'POST',
      headers,
      body: new URLSearchParams({
        grant_type: 'refresh_token',
        refresh_token: first.refresh_token,
        client_id: client.client_id,
        client_secret: client.client_secret,
      }),
    });
    expect(refreshed.status).toBe(200);
    const next = (await refreshed.json()) as { access_token: string; refresh_token: string };
    expect(next.refresh_token).not.toBe(first.refresh_token);
    expect(verifyAccessToken(next.access_token, KEY)).toBeDefined();

    // Characterisation, not endorsement: these refresh tokens are stateless,
    // so the PREVIOUS one is still accepted after a refresh. Pinning that
    // here means the day someone adds real reuse-detection, this test fails
    // and forces the docs in tokens.ts to be updated with it.
    const reused = await fetch(`${base}/oauth/token`, {
      method: 'POST',
      headers,
      body: new URLSearchParams({
        grant_type: 'refresh_token',
        refresh_token: first.refresh_token,
        client_id: client.client_id,
        client_secret: client.client_secret,
      }),
    });
    expect(reused.status).toBe(200);
  });

  it('invalidates every outstanding token when the signing key rotates', async () => {
    // The documented revocation lever: tokens are only as durable as the key
    // that signed them, so rotating CONNECTOR_API_KEY / OAUTH_SIGNING_KEY is
    // what actually kills a leaked token.
    const claims = { clientId: 'cid', scope: 'mcp:read', audience: undefined };
    const token = issueAccessToken(claims, KEY);
    expect(verifyAccessToken(token, KEY)).toBeDefined();
    expect(verifyAccessToken(token, 'rotated-key-1111111111111111111')).toBeUndefined();
  });

  it('rejects the token endpoint with a bad client secret', async () => {
    const client = await register();
    const res = await fetch(`${base}/oauth/token`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        code: 'whatever',
        code_verifier: 'x'.repeat(50),
        client_id: client.client_id,
        client_secret: 'not-the-secret',
      }),
    });
    expect(res.status).toBe(401);
    expect(((await res.json()) as { error: string }).error).toBe('invalid_client');
  });

  it('still accepts the raw CONNECTOR_API_KEY, so non-browser clients keep working', async () => {
    const res = await fetch(`${base}/mcp`, {
      method: 'POST',
      headers: { authorization: `Bearer ${config.connectorApiKey}` },
    });
    expect(res.status).toBe(200);
    expect(((await res.json()) as { method: string }).method).toBe('api_key');
  });
});
