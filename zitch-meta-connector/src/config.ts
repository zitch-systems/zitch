/**
 * Environment configuration — the ONLY place process.env is read.
 *
 * Fails fast at startup if a required variable is missing, rather than letting
 * a tool call fail confusingly later with a bad Graph API request. Every value
 * here is trusted server-side config; none of it — least of all
 * META_ACCESS_TOKEN — is ever handed back to a caller. See redact.ts for the
 * belt-and-braces guard against that leaking through a log line or an error.
 */
import { createHmac } from 'node:crypto';

import type { StaticClient } from './oauth/clients.js';

export interface Config {
  metaAccessToken: string;
  metaWabaId: string;
  metaPhoneNumberId: string;
  /** Optional: only needed if a future tool calls Meta's /debug_token endpoint. */
  metaAppId: string | undefined;
  metaAppSecret: string | undefined;
  /** The Zitch backend data-exchange endpoint attached before publishing a
   * Flow. Optional so read-only deployments and non-endpoint Flows keep
   * working; production sets it explicitly. */
  metaFlowEndpointUri?: string;
  connectorApiKey: string;
  port: number;
  graphApiVersion: string;
  graphApiBaseUrl: string;
  /** Hard ceiling on any outbound Graph API call. */
  graphTimeoutMs: number;
  rateLimitWindowMs: number;
  rateLimitMaxRequests: number;
  /** Coarser, pre-auth budget keyed by IP alone — see rateLimit.ts. Must be
   * >= rateLimitMaxRequests or a legitimate authenticated caller could be
   * blocked by the IP-level check before their own per-key budget even
   * applies. */
  ipRateLimitMaxRequests: number;
  /**
   * True unless META_ALLOW_WRITES is explicitly enabled. When true, write
   * tools are not registered AT ALL — they are absent from tools/list, from
   * the REST mirror, and from the MCP dispatch table, rather than present and
   * refusing. A tool that cannot be named cannot be called by mistake.
   *
   * Default-off is the point: this connector shares a WhatsApp Business
   * Account with the live customer banking channel, so enabling writes has to
   * be a deliberate act of configuration, never a side effect of deploying a
   * build that happens to contain write code.
   */
  readOnly: boolean;
  /**
   * Write tools additionally refuse unless the caller restates the exact
   * resource being changed. Off means the interlock is skipped — available
   * because it is the operator's call, but it is on by default for a reason:
   * it is what stops a misread instruction deprecating the wrong Flow.
   */
  requireWriteConfirmation: boolean;

  // --- OAuth 2.1 (see src/oauth/) -----------------------------------------
  /** Public origin, e.g. https://zitch-meta-connector.onrender.com. When
   * unset it is derived per-request from X-Forwarded-Proto/Host, which is
   * correct behind Render's proxy; set it explicitly if this ever sits behind
   * something that does not send those. */
  publicBaseUrl: string | undefined;
  /** Signs client_ids, authorization-request blobs, and access/refresh
   * tokens. Defaults to a value DERIVED from connectorApiKey rather than
   * equal to it, so the two secrets are never interchangeable; the practical
   * consequence is that rotating CONNECTOR_API_KEY also invalidates every
   * outstanding OAuth token, which is the behaviour you want from a rotation. */
  oauthSigningKey: string;
  /** What the operator types on the consent screen. Defaults to
   * connectorApiKey so OAuth works with no extra configuration. */
  oauthLoginPassword: string;
  /** Hosts a redirect_uri may point at. Registration is unauthenticated (that
   * is what makes Dynamic Client Registration work for MCP clients), so this list
   * is the only thing stopping this service being used as an open redirector. */
  oauthAllowedRedirectHosts: readonly string[];
  /** Optional pre-shared client, for clients that cannot do dynamic
   * registration. Unset means DCR is the only way in. */
  oauthStaticClient: StaticClient | undefined;
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value || !value.trim()) {
    throw new Error(
      `Missing required environment variable: ${name}. See .env.example.`,
    );
  }
  return value.trim();
}

function optionalEnv(name: string): string | undefined {
  const value = process.env[name];
  return value && value.trim() ? value.trim() : undefined;
}

/** Boolean env var. Only the explicit affirmatives count — anything else
 * (including a typo'd "ture", or an empty value) reads as the fallback, so a
 * mistake can never accidentally switch writes ON. */
function boolEnv(name: string, fallback: boolean): boolean {
  const raw = optionalEnv(name)?.toLowerCase();
  if (raw === undefined) return fallback;
  if (['1', 'true', 'yes', 'on'].includes(raw)) return true;
  if (['0', 'false', 'no', 'off'].includes(raw)) return false;
  throw new Error(`Environment variable ${name} must be true/false, got: ${raw}`);
}

/** Comma-separated list env var; undefined when unset so a caller can apply
 * its own default. Empty entries are dropped rather than becoming "". */
function listEnv(name: string): string[] | undefined {
  const raw = optionalEnv(name);
  if (!raw) return undefined;
  const items = raw.split(',').map((item) => item.trim()).filter(Boolean);
  return items.length > 0 ? items : undefined;
}

/** The optional pre-shared OAuth client. Both halves must be present, and the
 * secret must be long enough to be worth having — a half-configured client is
 * a configuration mistake, not a usable fallback, so it fails at boot. */
function staticClient(): StaticClient | undefined {
  const clientId = optionalEnv('OAUTH_CLIENT_ID');
  const clientSecret = optionalEnv('OAUTH_CLIENT_SECRET');
  if (!clientId && !clientSecret) return undefined;
  if (!clientId || !clientSecret) {
    throw new Error('OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET must be set together, or neither.');
  }
  if (clientSecret.length < 24) {
    throw new Error(
      'OAUTH_CLIENT_SECRET is too short (< 24 chars) — generate one with `openssl rand -hex 32`.',
    );
  }
  return {
    clientId,
    clientSecret,
    redirectUris: listEnv('OAUTH_REDIRECT_URIS') ?? [
      'https://claude.ai/api/mcp/auth_callback',
      'https://claude.com/api/mcp/auth_callback',
    ],
  };
}

function intEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`Environment variable ${name} must be a positive integer, got: ${raw}`);
  }
  return parsed;
}

let cached: Config | undefined;

/** Loads and validates config once per process; safe to call from anywhere. */
export function loadConfig(): Config {
  if (cached) return cached;

  const connectorApiKey = requireEnv('CONNECTOR_API_KEY');
  if (connectorApiKey.length < 24) {
    throw new Error(
      'CONNECTOR_API_KEY is too short (< 24 chars) — generate one with ' +
        "`openssl rand -hex 32` so it can't be brute-forced.",
    );
  }

  const graphApiBaseUrl = optionalEnv('META_GRAPH_API_BASE_URL') ?? 'https://graph.facebook.com';
  if (!graphApiBaseUrl.startsWith('https://')) {
    // The access token travels in an Authorization header on every Graph API
    // call (metaClient.ts) — over plain HTTP that header is sent in the
    // clear. There is no legitimate reason to point this at a non-HTTPS
    // endpoint, so refuse to boot rather than silently downgrade transport
    // security because of a typo'd or misconfigured override.
    throw new Error(
      `META_GRAPH_API_BASE_URL must start with https:// (got: ${graphApiBaseUrl}) — ` +
        'the access token is sent in a header on every request and must never travel over plain HTTP.',
    );
  }

  const metaFlowEndpointUri = optionalEnv('META_FLOW_ENDPOINT_URI');
  if (metaFlowEndpointUri && !metaFlowEndpointUri.startsWith('https://')) {
    throw new Error(
      `META_FLOW_ENDPOINT_URI must start with https:// (got: ${metaFlowEndpointUri}).`,
    );
  }

  cached = {
    metaAccessToken: requireEnv('META_ACCESS_TOKEN'),
    metaWabaId: requireEnv('META_WABA_ID'),
    metaPhoneNumberId: requireEnv('META_PHONE_NUMBER_ID'),
    metaAppId: optionalEnv('META_APP_ID'),
    metaAppSecret: optionalEnv('META_APP_SECRET'),
    metaFlowEndpointUri,
    connectorApiKey,
    port: intEnv('PORT', 8787),
    graphApiVersion: optionalEnv('META_GRAPH_API_VERSION') ?? 'v21.0',
    graphApiBaseUrl,
    graphTimeoutMs: intEnv('GRAPH_TIMEOUT_MS', 10_000),
    rateLimitWindowMs: intEnv('RATE_LIMIT_WINDOW_MS', 60_000),
    rateLimitMaxRequests: intEnv('RATE_LIMIT_MAX_REQUESTS', 30),
    ipRateLimitMaxRequests: intEnv('IP_RATE_LIMIT_MAX_REQUESTS', 60),
    readOnly: !boolEnv('META_ALLOW_WRITES', false),
    requireWriteConfirmation: boolEnv('META_REQUIRE_WRITE_CONFIRMATION', true),
    publicBaseUrl: optionalEnv('PUBLIC_BASE_URL'),
    oauthSigningKey:
      optionalEnv('OAUTH_SIGNING_KEY') ??
      createHmac('sha256', connectorApiKey).update('zitch-meta-connector/oauth-signing').digest('hex'),
    oauthLoginPassword: optionalEnv('OAUTH_LOGIN_PASSWORD') ?? connectorApiKey,
    oauthAllowedRedirectHosts: listEnv('OAUTH_ALLOWED_REDIRECT_HOSTS') ?? [
      // ChatGPT and Claude custom-connector callbacks, plus loopback for local
      // client development. Anything else has to be added deliberately.
      'chatgpt.com',
      'claude.ai',
      'claude.com',
      'localhost',
      '127.0.0.1',
    ],
    oauthStaticClient: staticClient(),
  };
  if (cached.ipRateLimitMaxRequests < cached.rateLimitMaxRequests) {
    throw new Error(
      'IP_RATE_LIMIT_MAX_REQUESTS must be >= RATE_LIMIT_MAX_REQUESTS — otherwise the ' +
        "pre-auth, per-IP budget could block a legitimate caller before their own " +
        'per-key budget is even checked.',
    );
  }
  return cached;
}

/** Test-only: clear the cached config so a test can reload with different env. */
export function _resetConfigForTests(): void {
  cached = undefined;
}
