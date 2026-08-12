/**
 * Belt-and-braces defense against the Meta access token ever reaching a
 * client, a log line, or an error message.
 *
 * The primary control is architectural: metaClient.ts is the only file that
 * reads `config.metaAccessToken`, and it's used exclusively to build an
 * outbound `Authorization` header — no tool handler ever touches it or puts
 * it in a return value. This module is the fallback for the case that
 * discipline misses: a Graph API error body that happens to echo the request
 * URL (which — like every Graph API call in this project — never contains
 * the token, since it always goes in the header, not the query string), or a
 * future contributor who logs something wider than intended.
 */

let secrets: string[] = [];

/** Registers a value that must never appear in an outbound response or log. */
export function registerSecret(value: string | undefined): void {
  if (value && value.length >= 8 && !secrets.includes(value)) {
    secrets.push(value);
  }
}

/** Test-only: clear registered secrets between test files. */
export function _resetRedactionForTests(): void {
  secrets = [];
}

const MASK = '[REDACTED]';

/** Deep-redacts any registered secret out of a string, recursively through objects/arrays. */
export function redactDeep<T>(value: T): T {
  if (secrets.length === 0) return value;
  if (typeof value === 'string') {
    return redactString(value) as unknown as T;
  }
  if (Array.isArray(value)) {
    return value.map((item) => redactDeep(item)) as unknown as T;
  }
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(value as Record<string, unknown>)) {
      out[key] = redactDeep(val);
    }
    return out as T;
  }
  return value;
}

function redactString(input: string): string {
  let result = input;
  for (const secret of secrets) {
    if (result.includes(secret)) {
      result = result.split(secret).join(MASK);
    }
  }
  return result;
}
