# Mobile dependency review — 2026-10-13

The dependency tree is **not audit-clean**. This review fixes the compatible
server dependency, mitigates the one advisory-bearing package included in the
Android JavaScript bundle, and records the remaining build/tooling exposure.
Expo stays on 51.0.39 and React Native on 0.74.5. No forced SDK downgrade or
unreviewed major override was applied.

## Changes and verification

- `@expo/cli` now resolves `send` 0.19.2 instead of 0.18.0. The lockfile changes
  only that dependency family and deduplicates the existing serve-static copy.
- `npm postinstall` applies an exact-version, SHA-256-guarded mitigation to
  `decode-uri-component` 0.2.2. The UTF-8 scanner is backported from upstream
  0.5.0, with output chunks joined once and a fixed-regex callback wrapper
  replacing the legacy input-derived regex/replacement map. The original
  CommonJS callable export, plus-to-space behavior and valid URI decoding remain.
  The fallback keeps BOM/orphan-C2 handling, recovers valid runs in malformed
  input and avoids recursive decoding. Package metadata is not relabeled, so
  npm continues to report the advisory honestly.
- The installed source must match either the original or reviewed patched hash;
  an unexpected source/version fails installation. Tests require the patched
  hash before builds. Do not build with lifecycle scripts disabled.
- The existing image parser restriction is now behavior-tested by the app CI
  build-patch suite. All four vulnerable parsers refuse identifying headers.
  No additional advisory exceptions were added to `check-npm-audit.mjs`.
- Validation passed: 59 Jest suites / 516 tests / 1 snapshot, TypeScript,
  changed-file ESLint, 11 offline build-patch tests, and a clean Android Metro
  export with source maps. Decoder tests include UTF-8, phone numbers, plus
  signs, encoded percent signs, invalid code points, a 60 KB malformed run and
  10,000 separated malformed runs under a subprocess deadline.

Upstream scanner: [decode-uri-component v0.5.0](https://github.com/SamVerschueren/decode-uri-component/releases/tag/v0.5.0).
The MIT license and attributed source are retained in `scripts/security/`.
Patched source SHA-256:
`a5c7d5b07e6c2a11e8c20f54cdb018c9723a85d4e053e8b1071d8710351e9dab`.

## Advisory and reachability record

“Android sources” counts exact package paths in the final Metro source map.
Being absent from that bundle does not make a build dependency harmless; the
input boundary and unresolved dependency work are recorded separately.

| Package / installed version | Advisory severity | Android sources | Exposure and disposition |
| --- | --- | ---: | --- |
| decode-uri-component 0.2.2 | [Moderate](https://github.com/advisories/GHSA-vcc3-ghjq-m6fr) | 1 | React Navigation → query-string decodes externally supplied links. Guarded scanner backport and malformed-input tests mitigate this runtime path. Upstream 0.5.0 is ESM-only and changes plus handling, so a direct override is incompatible with query-string 7's callable `require`. |
| send 0.19.2 | [Low, fixed](https://github.com/advisories/GHSA-m6fv-jmcg-4jfg) | 0 | Expo CLI's development asset server. Targeted compatible update removes the finding; all installed copies are now 0.19.2. |
| image-size 1.2.1 | [High](https://github.com/advisories/GHSA-w3rx-r6r6-pgpr), [High](https://github.com/advisories/GHSA-5p2g-fcmc-qvqq) | 0 | Metro reads repository image assets. ICNS, HEIF/AVIF and both JPEG-XL parsers are disabled before use and verified by CI. Patched 2.0.3 changes the callable CommonJS API used by Metro 0.80; requires a coordinated Metro/Expo upgrade. |
| braces 3.0.3 | [High](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) | 0 | Build/test glob parsing through micromatch, chokidar and Tailwind; patterns come from project/tool configuration, not a mobile request endpoint. No patched upstream release was available. Remains a tooling risk if hostile glob configuration is supplied. |
| node-forge 1.4.0 | [High](https://github.com/advisories/GHSA-86w9-cpqp-85rv) | 0 | Expo certificate/signature tooling and development HTTPS. Expo's certificate package does invoke signature verification; this is not a blanket “unused crypto” dismissal. The advisory concerns RSA PKCS#1 v1.5 verification with low-exponent keys. No upstream patch was available. Keep certificate inputs trusted; retain this unresolved tooling finding. APK signature verification uses Android/JDK tooling, not this JS package. |
| turbo-stream 2.4.1 | [High](https://github.com/advisories/GHSA-rxv8-25v2-qmq8) | 0 | Expo Router → @expo/server → Remix server runtime. The affected route is Remix/React Router framework single-fetch. This app uses native output and static web output with a Python backend; no deployed single-fetch endpoint was identified. A 3.x override changes protocol/API and was not applied. Re-review before adopting that server mode. |
| postcss-selector-parser 6.1.4 | [Moderate](https://github.com/advisories/GHSA-rj75-hqrm-r3gf) | 0 | CSS build tooling parses project selectors. Fix starts at 7.1.6; major upgrade must be coordinated with the parent CSS tools. No runtime mobile parser path identified. |
| sprintf-js 1.0.3 | [Moderate](https://github.com/advisories/GHSA-hp3w-g68c-fv3c) | 0 | Expo CLI → js-yaml 3 → argparse 1 formats CLI messages. No patched upstream version was available; app inputs are not used as its format templates. |
| uuid 7.0.3 / 8.3.2 | [Moderate](https://github.com/advisories/GHSA-w5hq-g745-h8pq) | 0 | Build tools; inspected xcode and Expo bunyan callers use v4/v1 without caller buffers. The advisory covers affected name/version functions with supplied buffers. Fix starts at 11.1.1 and needs coordinated parent upgrades. Mobile spend nonces use expo-crypto, not this package. |

## Audit counts and reproducibility

Before the change, `npm audit --json` reported 109 inherited findings
(79 high, 29 moderate, 1 low, 0 critical), arising from 9 packages with direct
advisory records. After the change, a fresh full audit reported 122 inherited
findings (98 high, 24 moderate, 0 low, 0 critical), arising from **8 packages /
9 advisory records**. `send` disappeared and no new underlying advisory was
introduced. npm's peer/transitive propagation added inherited nodes and changed
their severities despite the narrow lockfile change. Counts are observations of
the advisory service, not the number of exploitable mobile vulnerabilities.
`--omit=dev` still includes Expo's toolchain dependencies and is not evidence of
Android bundle membership.

Reproduce the checks from a clean installation:

```sh
npm ci
npm run test:build-patches
npm test -- --runInBand
npx tsc --noEmit
npx expo export --platform android --source-maps --clear --output-dir /tmp/zitch-dependency-export
npm audit --json
```

The inspected Android map contains 1,510 sources, with exactly one decoder
module whose source hash matches the patched hash above and no recursive
`decodeComponents` function. Map filename:
`entry-fe86250a2f15f8baa5e9e56ee23bed9f.hbc.map`; SHA-256:
`63197b37ec5cacb636a200dff47b059d6a4a8d7bdb5872131f6d584b3d399943`.
The map proves membership for this Android export only; it does not certify
native libraries, other targets, deployment configuration or live-device flows.

Outstanding dependency work is a coordinated Expo/Metro/CSS/server-tool upgrade
and upstream fixes for packages with no patched release. Recheck the advisory
links when those parents change; do not erase the residual findings or replace
this evidence with a zero-vulnerability readiness claim.
