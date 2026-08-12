/**
 * Environment configuration — the ONLY place process.env is read.
 *
 * Fails fast at startup if a required variable is missing, rather than letting
 * a tool call fail confusingly later with a bad Graph API request. Every value
 * here is trusted server-side config; none of it — least of all
 * META_ACCESS_TOKEN — is ever handed back to a caller. See redact.ts for the
 * belt-and-braces guard against that leaking through a log line or an error.
 */

export interface Config {
  metaAccessToken: string;
  metaWabaId: string;
  metaPhoneNumberId: string;
  /** Optional: only needed if a future tool calls Meta's /debug_token endpoint. */
  metaAppId: string | undefined;
  metaAppSecret: string | undefined;
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
   * Read-only is the only mode this server currently ships. It is still an
   * explicit, named switch (rather than "true because no write tools exist")
   * so that the day a write tool is proposed, turning it on is a deliberate,
   * reviewable change here — not a silent side effect of adding a file.
   */
  readOnly: true;
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

  cached = {
    metaAccessToken: requireEnv('META_ACCESS_TOKEN'),
    metaWabaId: requireEnv('META_WABA_ID'),
    metaPhoneNumberId: requireEnv('META_PHONE_NUMBER_ID'),
    metaAppId: optionalEnv('META_APP_ID'),
    metaAppSecret: optionalEnv('META_APP_SECRET'),
    connectorApiKey,
    port: intEnv('PORT', 8787),
    graphApiVersion: optionalEnv('META_GRAPH_API_VERSION') ?? 'v21.0',
    graphApiBaseUrl,
    graphTimeoutMs: intEnv('GRAPH_TIMEOUT_MS', 10_000),
    rateLimitWindowMs: intEnv('RATE_LIMIT_WINDOW_MS', 60_000),
    rateLimitMaxRequests: intEnv('RATE_LIMIT_MAX_REQUESTS', 30),
    ipRateLimitMaxRequests: intEnv('IP_RATE_LIMIT_MAX_REQUESTS', 60),
    readOnly: true,
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
