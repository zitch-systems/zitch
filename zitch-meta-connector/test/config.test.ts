import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { _resetConfigForTests, loadConfig } from '../src/config.js';

const REQUIRED_VARS = ['META_ACCESS_TOKEN', 'META_WABA_ID', 'META_PHONE_NUMBER_ID', 'CONNECTOR_API_KEY'];
const originalEnv: Record<string, string | undefined> = {};

function setValidEnv(): void {
  process.env.META_ACCESS_TOKEN = 'test-meta-access-token';
  process.env.META_WABA_ID = '123456789';
  process.env.META_PHONE_NUMBER_ID = '987654321';
  process.env.CONNECTOR_API_KEY = 'a'.repeat(32);
}

describe('loadConfig', () => {
  beforeEach(() => {
    for (const key of [...REQUIRED_VARS, 'PORT', 'RATE_LIMIT_MAX_REQUESTS']) {
      originalEnv[key] = process.env[key];
      delete process.env[key];
    }
    _resetConfigForTests();
  });

  afterEach(() => {
    for (const [key, value] of Object.entries(originalEnv)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    _resetConfigForTests();
  });

  it('loads successfully when every required variable is set', () => {
    setValidEnv();
    const config = loadConfig();
    expect(config.metaAccessToken).toBe('test-meta-access-token');
    expect(config.metaWabaId).toBe('123456789');
    expect(config.readOnly).toBe(true);
  });

  it.each(REQUIRED_VARS)('throws when %s is missing', (missingVar) => {
    setValidEnv();
    delete process.env[missingVar];
    expect(() => loadConfig()).toThrow(new RegExp(missingVar));
  });

  it('rejects a CONNECTOR_API_KEY shorter than 24 characters', () => {
    setValidEnv();
    process.env.CONNECTOR_API_KEY = 'too-short';
    expect(() => loadConfig()).toThrow(/too short/);
  });

  it('applies sane defaults for optional numeric settings', () => {
    setValidEnv();
    const config = loadConfig();
    expect(config.port).toBe(8787);
    expect(config.rateLimitMaxRequests).toBe(30);
    expect(config.graphTimeoutMs).toBe(10_000);
  });

  it('rejects a non-positive-integer override', () => {
    setValidEnv();
    process.env.RATE_LIMIT_MAX_REQUESTS = 'not-a-number';
    expect(() => loadConfig()).toThrow(/positive integer/);
  });

  it('caches the config across calls (does not re-read env)', () => {
    setValidEnv();
    const first = loadConfig();
    process.env.META_WABA_ID = 'changed';
    const second = loadConfig();
    expect(second).toBe(first);
    expect(second.metaWabaId).toBe('123456789');
  });
});
