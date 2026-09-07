// Expo config plugin: Android TLS certificate pinning for the Zitch API hosts.
//
// ── Read this before enabling it ────────────────────────────────────────────────
//
// Pinning is OFF by default, and that is a decision, not an omission.
//
// A wrong or stale pin does not degrade the app, it bricks it: every HTTPS call to
// the pinned host fails on every installed copy, and a shipped mobile app has no
// remote kill switch. The recovery is a store release, which takes days.
//
// Zitch's API host makes that risk concrete:
//
//   * `api.zitch.ng` sits behind a CDN/WAF whose certificate we do not control or
//     get told about before it rotates (see lib/netPatch.ts).
//
// So pinning either host to the key it happens to serve today schedules an outage.
// The prerequisite is not code, it is control of the key: a custom certificate whose
// key WE hold across renewals, or a documented rotation runbook that guarantees the
// backup pin is already deployed before the primary changes.
//
// Until then, transport security rests on system CA validation plus
// `usesCleartextTraffic: false` (already set) and the release-build host allowlist in
// components/configFiles/apiConfig.
//
// ── Enabling it, once a key you control exists ──────────────────────────────────
//
// In app.json:
//
//     ["./plugins/withCertificatePinning", {
//       "domains": ["api.zitch.ng"],
//       "pins": ["sha256/PRIMARY_SPKI_BASE64", "sha256/BACKUP_SPKI_BASE64"],
//       "expiration": "2027-01-01"
//     }]
//
// Compute a pin from the served certificate:
//
//     openssl s_client -servername api.zitch.ng -connect api.zitch.ng:443 </dev/null \
//       | openssl x509 -pubkey -noout \
//       | openssl pkey -pubin -outform der \
//       | openssl dgst -sha256 -binary | openssl enc -base64
//
// The plugin REFUSES to build unless three conditions hold, each of which exists to
// stop a foreseeable brick:
//
//   1. At least two pins. Android's own guidance: without a backup pin, losing the
//      primary key locks every install out permanently.
//   2. An `expiration` date. When a pin-set expires Android stops ENFORCING it —
//      pinning fails open. For an app that cannot be patched remotely, an expiry that
//      degrades to normal CA validation is far better than a hard outage, so it is
//      mandatory rather than optional.
//   3. That expiration is in the future. A pin-set already expired at build time
//      would ship doing nothing while looking like protection.

const fs = require('fs');
const path = require('path');

const { withAndroidManifest, withDangerousMod } = require('expo/config-plugins');

const CONFIG_RESOURCE = 'network_security_config';

/**
 * Validate plugin props and return them normalised, or null when pinning is not
 * configured at all (the default, deliberate, state).
 *
 * Exported for unit testing: the guards below are the whole safety value of this
 * plugin, so they are tested directly rather than only through a prebuild.
 */
function resolvePins(props, now = new Date()) {
  const domains = (props && props.domains) || [];
  const pins = (props && props.pins) || [];
  const expiration = props && props.expiration;

  if (!pins.length && !domains.length) return null; // not configured — no-op
  if (!domains.length) {
    throw new Error('withCertificatePinning: `pins` given with no `domains` to apply them to');
  }
  if (!pins.length) {
    throw new Error('withCertificatePinning: `domains` given with no `pins`');
  }
  if (pins.length < 2) {
    throw new Error(
      'withCertificatePinning: at least two pins are required — a primary and a backup. ' +
        'With one pin, losing that key locks every installed app out of the API permanently, ' +
        'and there is no remote fix.'
    );
  }
  for (const pin of pins) {
    if (!/^sha256\/[A-Za-z0-9+/]{43}=$/.test(pin)) {
      throw new Error(
        `withCertificatePinning: pin "${pin}" is not a base64 SHA-256 SPKI digest ` +
          '(expected "sha256/…="). See the openssl recipe in this file.'
      );
    }
  }
  if (!expiration) {
    throw new Error(
      'withCertificatePinning: `expiration` (YYYY-MM-DD) is required. An expired pin-set ' +
        'stops being enforced, which is the only safe failure mode for an app that cannot ' +
        'be patched remotely — so it must never be absent.'
    );
  }
  const when = new Date(`${expiration}T00:00:00Z`);
  if (Number.isNaN(when.getTime())) {
    throw new Error(`withCertificatePinning: expiration ${expiration} is not a YYYY-MM-DD date`);
  }
  if (when <= now) {
    throw new Error(
      `withCertificatePinning: expiration ${expiration} is in the past — this pin-set would ` +
        'ship already unenforced while looking like protection.'
    );
  }
  return { domains, pins, expiration };
}

function buildXml({ domains, pins, expiration }) {
  const domainEls = domains
    .map((d) => `        <domain includeSubdomains="false">${d}</domain>`)
    .join('\n');
  const pinEls = pins.map((p) => `            <pin digest="SHA-256">${p.slice('sha256/'.length)}</pin>`).join('\n');
  return `<?xml version="1.0" encoding="utf-8"?>
<!-- Generated by plugins/withCertificatePinning.js — do not edit; android/ is regenerated by prebuild. -->
<network-security-config>
    <base-config cleartextTrafficPermitted="false">
        <trust-anchors>
            <certificates src="system" />
        </trust-anchors>
    </base-config>
    <domain-config cleartextTrafficPermitted="false">
${domainEls}
        <pin-set expiration="${expiration}">
${pinEls}
        </pin-set>
    </domain-config>
</network-security-config>
`;
}

const withCertificatePinning = (config, props) => {
  const resolved = resolvePins(props);
  if (resolved === null) return config; // pinning not configured — nothing to do

  config = withDangerousMod(config, [
    'android',
    async (cfg) => {
      const dir = path.join(cfg.modRequest.platformProjectRoot, 'app', 'src', 'main', 'res', 'xml');
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, `${CONFIG_RESOURCE}.xml`), buildXml(resolved), 'utf8');
      return cfg;
    },
  ]);

  return withAndroidManifest(config, (cfg) => {
    const app = cfg.modResults.manifest.application?.[0];
    if (!app) {
      throw new Error('withCertificatePinning: no <application> in AndroidManifest.xml');
    }
    app.$['android:networkSecurityConfig'] = `@xml/${CONFIG_RESOURCE}`;
    return cfg;
  });
};

module.exports = withCertificatePinning;
module.exports.resolvePins = resolvePins;
module.exports.buildXml = buildXml;
