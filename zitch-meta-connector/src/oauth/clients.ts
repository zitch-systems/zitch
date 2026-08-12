/**
 * OAuth client identity — Dynamic Client Registration (RFC 7591) plus one
 * optional statically-configured client.
 *
 * Registration is STATELESS: the `client_id` we hand back is itself a signed
 * token carrying the client's own metadata (its redirect URIs, name, and auth
 * method). Verifying a `client_id` later means checking that signature and
 * reading the metadata straight out of it — no registry, no database, and
 * nothing to lose when the instance restarts on a deploy. A client registered
 * before a redeploy keeps working after it, which an in-memory Map would not
 * survive: Claude would silently have to re-register on every deploy.
 *
 * The `client_secret` is derived deterministically from the `client_id`, so it
 * likewise needs no storage and cannot drift out of sync with it.
 *
 * Redirect URIs are validated against a host allowlist at REGISTRATION time
 * and again at AUTHORIZATION time. Registration is an unauthenticated
 * endpoint by design (that is what makes DCR work for Claude), so without
 * that allowlist anyone on the internet could register a client pointing at
 * their own callback and turn this service into an open redirector.
 */
import { sign, verify, b64uEncode, safeEqualString } from './sign.js';
import { createHmac } from 'node:crypto';

export interface ClientMetadata {
  redirectUris: string[];
  clientName: string;
  /** 'none' = public client (PKCE only); otherwise a secret is required. */
  tokenEndpointAuthMethod: 'none' | 'client_secret_post' | 'client_secret_basic';
  /** Issued-at, seconds. Present so two registrations of the same shape still
   * produce distinct client_ids. */
  iat: number;
}

interface ClientIdPayload extends Record<string, unknown> {
  typ: 'client';
  ru: string[];
  cn: string;
  am: ClientMetadata['tokenEndpointAuthMethod'];
  iat: number;
}

export class ClientError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'ClientError';
  }
}

/** Parses and host-checks a redirect URI. Returns a normalised absolute URI. */
export function validateRedirectUri(raw: string, allowedHosts: readonly string[]): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new ClientError('invalid_redirect_uri', `Not an absolute URI: ${raw}`);
  }
  const isLoopback = url.hostname === 'localhost' || url.hostname === '127.0.0.1' || url.hostname === '::1';
  // http is tolerated ONLY for loopback (local development of a client);
  // anywhere else an authorization code would cross the network in cleartext.
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && isLoopback)) {
    throw new ClientError(
      'invalid_redirect_uri',
      `Redirect URI must use https (http is allowed only for loopback): ${raw}`,
    );
  }
  if (url.hash) {
    throw new ClientError('invalid_redirect_uri', `Redirect URI must not contain a fragment: ${raw}`);
  }
  const allowed = allowedHosts.some(
    (host) => url.hostname === host || url.hostname.endsWith(`.${host}`),
  );
  if (!allowed) {
    throw new ClientError(
      'invalid_redirect_uri',
      `Redirect URI host "${url.hostname}" is not allowed. Allowed: ${allowedHosts.join(', ')}`,
    );
  }
  return url.toString();
}

export function deriveClientSecret(clientId: string, key: string): string {
  return b64uEncode(createHmac('sha256', key).update(`client_secret:${clientId}`).digest());
}

export interface RegisteredClient {
  clientId: string;
  clientSecret: string | undefined;
  metadata: ClientMetadata;
}

export function registerClient(
  input: {
    redirectUris: unknown;
    clientName?: unknown;
    tokenEndpointAuthMethod?: unknown;
  },
  opts: { key: string; allowedHosts: readonly string[]; now?: number },
): RegisteredClient {
  const { key, allowedHosts, now = Date.now() } = opts;

  if (!Array.isArray(input.redirectUris) || input.redirectUris.length === 0) {
    throw new ClientError('invalid_redirect_uri', 'redirect_uris must be a non-empty array.');
  }
  if (input.redirectUris.length > 10) {
    throw new ClientError('invalid_client_metadata', 'At most 10 redirect_uris are supported.');
  }
  const redirectUris = input.redirectUris.map((uri) => {
    if (typeof uri !== 'string') {
      throw new ClientError('invalid_redirect_uri', 'Every redirect_uri must be a string.');
    }
    return validateRedirectUri(uri, allowedHosts);
  });

  const rawName = typeof input.clientName === 'string' ? input.clientName.trim() : '';
  const clientName = (rawName || 'Unnamed MCP client').slice(0, 120);

  const rawMethod = input.tokenEndpointAuthMethod;
  const authMethod: ClientMetadata['tokenEndpointAuthMethod'] =
    rawMethod === 'none' || rawMethod === 'client_secret_basic' || rawMethod === 'client_secret_post'
      ? rawMethod
      : 'client_secret_post';

  const payload: ClientIdPayload = {
    typ: 'client',
    ru: redirectUris,
    cn: clientName,
    am: authMethod,
    iat: Math.floor(now / 1000),
  };
  const clientId = sign(payload, key);

  return {
    clientId,
    clientSecret: authMethod === 'none' ? undefined : deriveClientSecret(clientId, key),
    metadata: {
      redirectUris,
      clientName,
      tokenEndpointAuthMethod: authMethod,
      iat: payload.iat,
    },
  };
}

/** The optional statically-configured client (OAUTH_CLIENT_ID / _SECRET). */
export interface StaticClient {
  clientId: string;
  clientSecret: string;
  redirectUris: string[];
}

/** Resolves a client_id to its metadata — either the static client or a
 * signed DCR client_id. Returns undefined when it is neither. */
export function lookupClient(
  clientId: string,
  opts: { key: string; staticClient?: StaticClient | undefined },
): ClientMetadata | undefined {
  const { key, staticClient } = opts;

  if (staticClient && safeEqualString(clientId, staticClient.clientId)) {
    return {
      redirectUris: staticClient.redirectUris,
      clientName: 'Zitch Meta Connector (configured client)',
      tokenEndpointAuthMethod: 'client_secret_post',
      iat: 0,
    };
  }

  const payload = verify<ClientIdPayload>(clientId, key);
  if (!payload || payload.typ !== 'client' || !Array.isArray(payload.ru)) return undefined;
  return {
    redirectUris: payload.ru,
    clientName: typeof payload.cn === 'string' ? payload.cn : 'Unnamed MCP client',
    tokenEndpointAuthMethod: payload.am,
    iat: typeof payload.iat === 'number' ? payload.iat : 0,
  };
}

/** Verifies a presented client_secret for a client_id. */
export function checkClientSecret(
  clientId: string,
  presented: string,
  opts: { key: string; staticClient?: StaticClient | undefined },
): boolean {
  const { key, staticClient } = opts;
  if (staticClient && safeEqualString(clientId, staticClient.clientId)) {
    return safeEqualString(presented, staticClient.clientSecret);
  }
  return safeEqualString(presented, deriveClientSecret(clientId, key));
}
