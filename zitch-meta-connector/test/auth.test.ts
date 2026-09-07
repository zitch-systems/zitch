import { describe, expect, it } from 'vitest';

import { checkAuth } from '../src/auth.js';
import type { Config } from '../src/config.js';

function fakeConfig(apiKey: string): Config {
  return {
    metaAccessToken: 'token',
    metaWabaId: '1',
    metaPhoneNumberId: '2',
    metaAppId: undefined,
    metaAppSecret: undefined,
    connectorApiKey: apiKey,
    port: 0,
    graphApiVersion: 'v21.0',
    graphApiBaseUrl: 'https://graph.facebook.com',
    graphTimeoutMs: 1000,
    rateLimitWindowMs: 1000,
    rateLimitMaxRequests: 1,
    ipRateLimitMaxRequests: 1,
    readOnly: true,
    requireWriteConfirmation: true,
    publicBaseUrl: undefined,
    oauthSigningKey: 'oauth-signing-key-for-tests-0000',
    oauthLoginPassword: 'operator-passphrase-for-tests-00',
    oauthAllowedRedirectHosts: ['claude.ai'],
    oauthStaticClient: undefined,
  };
}

function fakeReq(headers: Record<string, string>): any {
  return { header: (name: string) => headers[name.toLowerCase()] };
}

// Deliberately low-entropy (repeated-character) fixture values rather than a
// realistic-looking random key: a plausible-looking secret in test source is
// exactly what a repo-wide secret scanner (gitleaks et al.) is built to catch,
// and a false positive there is a real CI failure to chase down later.
const FIXTURE_KEY = 'a'.repeat(28);
const FIXTURE_KEY_UPPER = 'A'.repeat(28);
const FIXTURE_KEY_SHORT = 'a'.repeat(10);

describe('checkAuth', () => {
  const config = fakeConfig(FIXTURE_KEY);

  it('accepts a matching Bearer token', () => {
    const result = checkAuth(fakeReq({ authorization: `Bearer ${FIXTURE_KEY}` }), config);
    expect(result.ok).toBe(true);
  });

  it('accepts a matching X-Connector-Api-Key header', () => {
    const result = checkAuth(fakeReq({ 'x-connector-api-key': FIXTURE_KEY }), config);
    expect(result.ok).toBe(true);
  });

  it('rejects a wrong key', () => {
    const result = checkAuth(fakeReq({ authorization: `Bearer ${'b'.repeat(28)}` }), config);
    expect(result.ok).toBe(false);
  });

  it('rejects a missing key', () => {
    const result = checkAuth(fakeReq({}), config);
    expect(result.ok).toBe(false);
    expect(result.keyFingerprint).toBe('(none)');
  });

  it('rejects a key that differs only in length (no crash, no false positive)', () => {
    const result = checkAuth(fakeReq({ authorization: `Bearer ${FIXTURE_KEY_SHORT}` }), config);
    expect(result.ok).toBe(false);
  });

  it('never returns more than a 6-char fingerprint', () => {
    const result = checkAuth(fakeReq({ authorization: `Bearer ${FIXTURE_KEY}` }), config);
    expect(result.keyFingerprint.length).toBeLessThanOrEqual(6);
    expect(result.keyFingerprint).not.toContain(config.connectorApiKey);
  });

  it('is case-sensitive and exact — no partial/prefix match', () => {
    const result = checkAuth(fakeReq({ authorization: `Bearer ${FIXTURE_KEY_UPPER}` }), config);
    expect(result.ok).toBe(false);
  });

  it('reports which credential type authenticated, for the audit trail', () => {
    expect(checkAuth(fakeReq({ authorization: `Bearer ${FIXTURE_KEY}` }), config).method).toBe('api_key');
    expect(checkAuth(fakeReq({}), config).method).toBe('none');
  });

  it('rejects a well-formed but unsigned bearer token without throwing', () => {
    // Shaped like one of our signed tokens (two dot-separated segments) so it
    // reaches the OAuth verification path rather than being dismissed early.
    const shaped = `${Buffer.from('{"typ":"access"}').toString('base64url')}.${Buffer.from('nope').toString('base64url')}`;
    expect(() => checkAuth(fakeReq({ authorization: `Bearer ${shaped}` }), config)).not.toThrow();
    expect(checkAuth(fakeReq({ authorization: `Bearer ${shaped}` }), config).ok).toBe(false);
  });
});
