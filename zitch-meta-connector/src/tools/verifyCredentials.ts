/**
 * Tool 6: verify_meta_credentials
 *
 * Confirms META_ACCESS_TOKEN is valid and actually scoped to read the
 * configured WABA and phone number, by making the smallest possible read
 * against each and reporting pass/fail + Meta's own error type (e.g.
 * "OAuthException" / code 190 for an expired/invalid token). Deliberately
 * does NOT call Meta's `/debug_token` endpoint: that call requires putting
 * the inspected token in the request's query string, which would break this
 * project's one absolute rule (metaClient.ts sends the token only in a
 * header, never a URL, so it can never end up in a proxy/access log). The
 * trade-off is no expiry/scope introspection — just "does it work right now".
 */
import type { Config } from '../config.js';
import { MetaApiError, graphGet } from '../metaClient.js';

interface CredentialCheck {
  resource: 'waba' | 'phone_number';
  ok: boolean;
  httpStatus: number | undefined;
  metaErrorType: string | undefined;
  message: string;
}

export interface VerifyCredentialsResult {
  allValid: boolean;
  checks: CredentialCheck[];
}

async function probe(
  resource: CredentialCheck['resource'],
  run: () => Promise<unknown>,
): Promise<CredentialCheck> {
  try {
    await run();
    return { resource, ok: true, httpStatus: 200, metaErrorType: undefined, message: 'OK' };
  } catch (err) {
    if (err instanceof MetaApiError) {
      return {
        resource,
        ok: false,
        httpStatus: err.status,
        metaErrorType: err.metaErrorType,
        message: err.message,
      };
    }
    return {
      resource,
      ok: false,
      httpStatus: undefined,
      metaErrorType: undefined,
      message: err instanceof Error ? err.message : String(err),
    };
  }
}

export async function verifyMetaCredentials(config: Config): Promise<VerifyCredentialsResult> {
  const checks = await Promise.all([
    probe('waba', () => graphGet(config, config.metaWabaId, { fields: 'id,name' })),
    probe('phone_number', () =>
      graphGet(config, config.metaPhoneNumberId, { fields: 'id,display_phone_number' }),
    ),
  ]);
  return { allValid: checks.every((c) => c.ok), checks };
}
