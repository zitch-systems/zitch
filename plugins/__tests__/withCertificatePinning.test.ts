import { resolvePins, buildXml } from '../withCertificatePinning';

// 43 base64 chars + '=' — the shape of a SHA-256 SPKI digest.
const PIN_A = 'sha256/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=';
const PIN_B = 'sha256/BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=';
const NOW = new Date('2026-07-30T00:00:00Z');

const valid = {
  domains: ['api.zitch.ng'],
  pins: [PIN_A, PIN_B],
  expiration: '2027-01-01',
};

describe('resolvePins', () => {
  it('treats absent configuration as pinning-off, not an error', () => {
    // The default state. A misconfigured build must fail loudly, but an
    // unconfigured one must build normally.
    expect(resolvePins(undefined, NOW)).toBeNull();
    expect(resolvePins({}, NOW)).toBeNull();
  });

  it('accepts a well-formed pin-set', () => {
    expect(resolvePins(valid, NOW)).toEqual(valid);
  });

  it('refuses a single pin', () => {
    // Losing the only pinned key locks every install out of the API permanently,
    // with no remote fix.
    expect(() => resolvePins({ ...valid, pins: [PIN_A] }, NOW)).toThrow(/backup/);
  });

  it('refuses a pin that is not a base64 SHA-256 SPKI digest', () => {
    expect(() => resolvePins({ ...valid, pins: [PIN_A, 'sha256/short'] }, NOW))
      .toThrow(/SPKI digest/);
    // A raw certificate fingerprint is the classic wrong value here — it pins the
    // certificate rather than the key, so it breaks on every renewal.
    expect(() => resolvePins({ ...valid, pins: [PIN_A, 'AA:BB:CC:DD'] }, NOW))
      .toThrow(/SPKI digest/);
  });

  it('requires an expiration', () => {
    const { expiration, ...withoutExpiry } = valid;
    expect(() => resolvePins(withoutExpiry, NOW)).toThrow(/expiration/);
  });

  it('refuses an expiration that has already passed', () => {
    // Would ship unenforced while looking like protection.
    expect(() => resolvePins({ ...valid, expiration: '2020-01-01' }, NOW))
      .toThrow(/in the past/);
  });

  it('refuses an unparseable expiration', () => {
    expect(() => resolvePins({ ...valid, expiration: 'next year' }, NOW))
      .toThrow(/not a YYYY-MM-DD date/);
  });

  it('refuses pins with no domain, and domains with no pins', () => {
    expect(() => resolvePins({ pins: [PIN_A, PIN_B] }, NOW)).toThrow(/no `domains`/);
    expect(() => resolvePins({ domains: ['api.zitch.ng'] }, NOW)).toThrow(/no `pins`/);
  });
});

describe('buildXml', () => {
  const xml = buildXml(valid);

  it('pins every configured domain in one domain-config', () => {
    expect(xml).toContain('<domain includeSubdomains="false">api.zitch.ng</domain>');
    expect(xml).not.toContain('zitch-api.onrender.com');
  });

  it('emits the digests without the sha256/ prefix, as Android expects', () => {
    expect(xml).toContain('<pin digest="SHA-256">AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=</pin>');
    expect(xml).not.toContain('>sha256/');
  });

  it('carries the expiration and keeps cleartext off', () => {
    expect(xml).toContain('<pin-set expiration="2027-01-01">');
    expect(xml).toContain('cleartextTrafficPermitted="false"');
  });

  it('still trusts the system CA store', () => {
    // Pinning narrows which keys are acceptable; it must not replace chain validation.
    expect(xml).toContain('<certificates src="system" />');
  });
});
