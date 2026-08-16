/**
 * The ONLY file that reads config.metaAccessToken. Every outbound Graph API
 * call goes through `graphGet` below, which puts the token in the
 * Authorization header (never the query string, so it can't end up in an
 * access log or a copy-pasted URL) and never returns it to the caller.
 */
import type { Config } from './config.js';

export class MetaApiError extends Error {
  constructor(
    message: string,
    public readonly status: number | undefined,
    public readonly metaErrorCode: number | undefined,
    public readonly metaErrorType: string | undefined,
  ) {
    super(message);
    this.name = 'MetaApiError';
  }
}

interface GraphErrorBody {
  error?: {
    message?: string;
    type?: string;
    code?: number;
    error_subcode?: number;
    fbtrace_id?: string;
  };
}

/**
 * GET a Graph API node/edge and return the parsed JSON body.
 *
 * - Hard timeout via AbortController — a hung upstream call must not hang a
 *   tool call indefinitely.
 * - Error responses are normalized into MetaApiError with a message safe to
 *   show a caller (Meta's `error.message` describes the problem, e.g. "Invalid
 *   OAuth access token", without ever including the token itself — the token
 *   was never in the request Meta echoes back, since it travels in a header).
 * - No retries: a maintenance/read tool should surface a real upstream
 *   problem immediately rather than mask it with hidden retries that also
 *   multiply rate-limit consumption against Meta's own API.
 */
export async function graphGet(
  config: Config,
  path: string,
  query: Record<string, string | number | undefined> = {},
): Promise<unknown> {
  const url = new URL(`${config.graphApiBaseUrl}/${config.graphApiVersion}/${path}`);
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.graphTimeoutMs);

  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${config.metaAccessToken}`,
        Accept: 'application/json',
      },
      signal: controller.signal,
    });

    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new MetaApiError(
        `Meta API returned a non-JSON response (HTTP ${response.status}).`,
        response.status,
        undefined,
        undefined,
      );
    }

    if (!response.ok) {
      const errBody = body as GraphErrorBody;
      throw new MetaApiError(
        errBody.error?.message ?? `Meta API request failed (HTTP ${response.status}).`,
        response.status,
        errBody.error?.code,
        errBody.error?.type,
      );
    }

    return body;
  } catch (err) {
    if (err instanceof MetaApiError) throw err;
    if (err instanceof Error && err.name === 'AbortError') {
      throw new MetaApiError(
        `Meta API did not respond within ${config.graphTimeoutMs}ms.`,
        undefined,
        undefined,
        'timeout',
      );
    }
    throw new MetaApiError(
      `Could not reach Meta API: ${err instanceof Error ? err.message : String(err)}`,
      undefined,
      undefined,
      'network_error',
    );
  } finally {
    clearTimeout(timer);
  }
}

/**
 * POST / DELETE against the Graph API — the write verbs.
 *
 * Separate functions rather than a `method` argument on `graphGet`, so that
 * "does this code path mutate anything at Meta?" is answerable by grepping for
 * two names. Every caller lives in src/tools/writes.ts, and every one of those
 * is gated on `config.readOnly` being false before it is even registered.
 *
 * Same timeout, same error normalisation, same token-in-a-header discipline as
 * the read path.
 */
async function graphMutate(
  config: Config,
  method: 'POST' | 'DELETE',
  path: string,
  body?: Record<string, unknown> | FormData,
  query: Record<string, string | number | undefined> = {},
): Promise<unknown> {
  if (config.readOnly) {
    // Belt and braces. Write tools are not registered in read-only mode, so
    // reaching here means a bug rather than a user action — fail loudly.
    throw new MetaApiError(
      'This connector is in read-only mode; write calls are refused.',
      undefined,
      undefined,
      'read_only',
    );
  }
  const url = new URL(`${config.graphApiBaseUrl}/${config.graphApiVersion}/${path}`);
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.graphTimeoutMs);
  try {
    const isMultipart = body instanceof FormData;
    const response = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${config.metaAccessToken}`,
        Accept: 'application/json',
        // fetch adds the multipart boundary. Setting Content-Type ourselves
        // would omit that boundary and make Meta reject an otherwise valid
        // Flow asset upload.
        ...(body && !isMultipart ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: isMultipart ? body : JSON.stringify(body) } : {}),
      signal: controller.signal,
    });

    let parsed: unknown;
    try {
      parsed = await response.json();
    } catch {
      throw new MetaApiError(
        `Meta API returned a non-JSON response (HTTP ${response.status}).`,
        response.status,
        undefined,
        undefined,
      );
    }
    if (!response.ok) {
      const errBody = parsed as GraphErrorBody;
      throw new MetaApiError(
        errBody.error?.message ?? `Meta API request failed (HTTP ${response.status}).`,
        response.status,
        errBody.error?.code,
        errBody.error?.type,
      );
    }
    return parsed;
  } catch (err) {
    if (err instanceof MetaApiError) throw err;
    if (err instanceof Error && err.name === 'AbortError') {
      throw new MetaApiError(
        `Meta API did not respond within ${config.graphTimeoutMs}ms.`,
        undefined,
        undefined,
        'timeout',
      );
    }
    throw new MetaApiError(
      `Could not reach Meta API: ${err instanceof Error ? err.message : String(err)}`,
      undefined,
      undefined,
      'network_error',
    );
  } finally {
    clearTimeout(timer);
  }
}

export function graphPost(
  config: Config,
  path: string,
  body: Record<string, unknown>,
): Promise<unknown> {
  return graphMutate(config, 'POST', path, body);
}

/** Upload a WhatsApp Flow JSON asset using Meta's required multipart shape.
 *
 * The `/assets` endpoint can return HTTP 200 even when an application/json
 * body contains fields named `file`, `name`, and `asset_type`; in that case it
 * leaves the default Flow untouched. Keeping this construction in the Graph
 * client makes the wire format explicit and prevents another write tool from
 * accidentally repeating that false-success request.
 */
export function graphPostFlowAsset(
  config: Config,
  flowId: string,
  flowJson: string,
): Promise<unknown> {
  const form = new FormData();
  form.set('name', 'flow.json');
  form.set('asset_type', 'FLOW_JSON');
  form.set('file', new Blob([flowJson], { type: 'application/json' }), 'flow.json');
  return graphMutate(config, 'POST', `${flowId}/assets`, form);
}

export function graphDelete(
  config: Config,
  path: string,
  query: Record<string, string | number | undefined> = {},
): Promise<unknown> {
  return graphMutate(config, 'DELETE', path, undefined, query);
}

/**
 * Hosts `fetchMetaAsset` will follow. Flow JSON does not come back on the
 * Graph node itself — the API hands out a signed `download_url` on a Meta CDN,
 * so fetching it is unavoidable.
 *
 * That makes the URL a value from a response deciding where we send a request,
 * which is the shape of an SSRF. Meta returning a hostile URL is not the threat
 * model; a bug, a proxy, or a future code path that passes some other
 * attacker-influenced URL into this function is. Pinning the host to
 * Meta-owned domains means this function can only ever talk to Meta, whatever
 * it is handed.
 *
 * Note the leading dot on each suffix: matching `endsWith('fbcdn.net')` would
 * also accept `evil-fbcdn.net`.
 */
const ASSET_HOST_SUFFIXES = ['.fbcdn.net', '.fbsbx.com', '.facebook.com', '.whatsapp.net'] as const;

export function isAllowedAssetUrl(raw: string): boolean {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.protocol !== 'https:') return false;
  return ASSET_HOST_SUFFIXES.some(
    (suffix) => url.hostname.endsWith(suffix) || url.hostname === suffix.slice(1),
  );
}

/** Fetches a signed Meta CDN asset (currently only Flow JSON) as parsed JSON. */
export async function fetchMetaAsset(config: Config, rawUrl: string): Promise<unknown> {
  if (!isAllowedAssetUrl(rawUrl)) {
    throw new MetaApiError(
      'Refusing to fetch an asset from a non-Meta host.',
      undefined,
      undefined,
      'blocked_host',
    );
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.graphTimeoutMs);
  try {
    // No Authorization header: the URL is already signed, and sending the
    // Meta token to a CDN host would put it somewhere it has no business being.
    const response = await fetch(rawUrl, { method: 'GET', signal: controller.signal });
    if (!response.ok) {
      throw new MetaApiError(
        `Could not download the Flow asset (HTTP ${response.status}).`,
        response.status,
        undefined,
        undefined,
      );
    }
    return await response.json();
  } catch (err) {
    if (err instanceof MetaApiError) throw err;
    if (err instanceof Error && err.name === 'AbortError') {
      throw new MetaApiError(
        `The Flow asset download did not complete within ${config.graphTimeoutMs}ms.`,
        undefined,
        undefined,
        'timeout',
      );
    }
    throw new MetaApiError(
      `Could not download the Flow asset: ${err instanceof Error ? err.message : String(err)}`,
      undefined,
      undefined,
      'network_error',
    );
  } finally {
    clearTimeout(timer);
  }
}
