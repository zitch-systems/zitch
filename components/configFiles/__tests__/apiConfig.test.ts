import {
  PRIMARY_API_URL,
  resolveApiBaseUrl,
} from '../apiConfig';

describe('resolveApiBaseUrl', () => {
  it('allows only the production API origins in release builds', () => {
    expect(resolveApiBaseUrl(PRIMARY_API_URL, false)).toBe(PRIMARY_API_URL);
    // The provider-assigned Render origin bypasses the public WAF and must never
    // be accepted by a release build, even as an HTTPS override.
    expect(resolveApiBaseUrl('https://zitch-api.onrender.com/', false)).toBe(PRIMARY_API_URL);
    expect(resolveApiBaseUrl('https://evil.example', false)).toBe(PRIMARY_API_URL);
    expect(resolveApiBaseUrl('http://api.zitch.ng', false)).toBe(PRIMARY_API_URL);
  });

  it('allows private development hosts without weakening release builds', () => {
    expect(resolveApiBaseUrl('http://10.0.2.2:8000/', true)).toBe('http://10.0.2.2:8000');
    expect(resolveApiBaseUrl('http://192.168.1.20:8000', true)).toBe('http://192.168.1.20:8000');
    expect(resolveApiBaseUrl('http://public.example:8000', true)).toBe(PRIMARY_API_URL);
  });

  it('fails closed on malformed configuration', () => {
    expect(resolveApiBaseUrl('not a url', false)).toBe(PRIMARY_API_URL);
    expect(resolveApiBaseUrl('', true)).toBe(PRIMARY_API_URL);
  });
});

