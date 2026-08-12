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
    readOnly: true,
  };
}

function fakeReq(headers: Record<string, string>): any {
  return { header: (name: string) => headers[name.toLowerCase()] };
}

describe('checkAuth', () => {
  const config = fakeConfig('correct-key-0123456789abcdef');

  it('accepts a matching Bearer token', () => {
    const result = checkAuth(fakeReq({ authorization: 'Bearer correct-key-0123456789abcdef' }), config);
    expect(result.ok).toBe(true);
  });

  it('accepts a matching X-Connector-Api-Key header', () => {
    const result = checkAuth(
      fakeReq({ 'x-connector-api-key': 'correct-key-0123456789abcdef' }),
      config,
    );
    expect(result.ok).toBe(true);
  });

  it('rejects a wrong key', () => {
    const result = checkAuth(fakeReq({ authorization: 'Bearer wrong-key' }), config);
    expect(result.ok).toBe(false);
  });

  it('rejects a missing key', () => {
    const result = checkAuth(fakeReq({}), config);
    expect(result.ok).toBe(false);
    expect(result.keyFingerprint).toBe('(none)');
  });

  it('rejects a key that differs only in length (no crash, no false positive)', () => {
    const result = checkAuth(fakeReq({ authorization: 'Bearer correct-key' }), config);
    expect(result.ok).toBe(false);
  });

  it('never returns more than a 6-char fingerprint', () => {
    const result = checkAuth(fakeReq({ authorization: 'Bearer correct-key-0123456789abcdef' }), config);
    expect(result.keyFingerprint.length).toBeLessThanOrEqual(6);
    expect(result.keyFingerprint).not.toContain(config.connectorApiKey);
  });

  it('is case-sensitive and exact — no partial/prefix match', () => {
    const result = checkAuth(fakeReq({ authorization: 'Bearer correct-key-0123456789ABCDEF' }), config);
    expect(result.ok).toBe(false);
  });
});
