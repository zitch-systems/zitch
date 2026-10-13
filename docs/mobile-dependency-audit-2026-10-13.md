# Mobile dependency review — 2026-10-13

The dependency tree is **not audit-clean**. This review separates maintained dependency upgrades from exact-source local
mitigations. It also records the one remaining format-template finding whose
attacker-controlled template boundary is absent from this app.
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
  The follow-up removes the obsolete image-size exceptions from
  `check-npm-audit.mjs`; it now reports every production high/critical advisory.
  Local mitigation evidence does not suppress npm output.
- Initial PR #551 validation passed: 59 Jest suites / 516 tests / 1 snapshot, TypeScript,
  changed-file ESLint, 11 offline build-patch tests, and a clean Android Metro
  export with source maps. Decoder tests include UTF-8, phone numbers, plus
  signs, encoded percent signs, invalid code points, a 60 KB malformed run and
  10,000 separated malformed runs under a subprocess deadline.

Follow-up validation passed: **59 Jest suites / 509 tests**, TypeScript,
changed-file ESLint, **30 build/security checks**, and a full Android Hermes/Metro
export after the Buffer/file adapter regression was fixed. Independent review
reproduced a gap in the initial Forge count-only draft; the installed canonical
backport rejects that reproducer and passed a separate positive/negative review.

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
| image-size 2.0.4 | [High, fixed](https://github.com/advisories/GHSA-w3rx-r6r6-pgpr), [High, fixed](https://github.com/advisories/GHSA-5p2g-fcmc-qvqq) | 0 | Maintained release, pinned beneath Metro. A guarded adapter preserves synchronous Buffer calls and awaits `imageSizeFromFile` for filesystem paths, which reads at most 512 KB. Zip-path Buffer handling is retained. ICNS, HEIF/AVIF and JPEG-XL remain disabled as an extra boundary. All 74 repository image dimensions matched the former parser before the upgrade; tests exercise real Metro Buffer, filesystem and zip-path calls. |
| braces 3.0.3 | [High, locally mitigated](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) | 0 | No fixed release. Local parser and compile/expand/stringify walker guards bound AST nesting to 100 containers, including parentheses and caller-supplied ASTs. Depth 100 remains accepted; 101 and 4,500 fail deterministically. Ordinary glob/range/quoted/escaped fixtures are retained. This does not claim to bound every glob-expansion complexity. |
| node-forge 1.4.0 | [High, locally mitigated](https://github.com/advisories/GHSA-86w9-cpqp-85rv) | 0 | Expo certificate/signature tooling and development HTTPS. Exact RSA backport from **pending, unreleased** [upstream PR #1159](https://github.com/digitalbazaar/forge/pull/1159), commit `db05494352dd3c9a5a07c5270333dcc78866cad2`, requires canonical DigestInfo encoding. Rejects nested garbage, nonempty NULL and nonminimal DER lengths. An earlier count-only proposal was independently found insufficient and is not the installed mitigation. Real RSA/OpenSSL SHA-256/384/512 signatures with exponents 3 and 65537, optional NULL, wrong-digest rejection, PSS and NONE are tested. No claim of an upstream fixed release. |
| turbo-stream 2.4.1 | [High, locally mitigated](https://github.com/advisories/GHSA-rxv8-25v2-qmq8) | 0 | Backports [React Router's vendored-v2 fix](https://github.com/remix-run/react-router/commit/59811921d3c7d599077b8cadccdcd65a233165e0) to both CommonJS and ESM entry points. Stream data cannot select arbitrary global constructors through an error name; errors decode as ordinary Error. V2 framing, ordinary structured values and encoder/decoder compatibility are retained. No deployed single-fetch endpoint was identified; review server mode before adopting it. |
| postcss-selector-parser 6.1.4 | [Moderate, locally mitigated](https://github.com/advisories/GHSA-rj75-hqrm-r3gf) | 0 | Backports the three Set-based membership fixes from [7.1.6 / commit 62b1917](https://github.com/postcss/postcss-selector-parser/commit/62b191792df0a0bc56062e5a875bc74aae2a51cd), preserving the parent tools' v6 mutation API. Large flat class/id/Sass-interpolation inputs finish under a subprocess deadline; escaped selectors, namespaces, deduplication and insertion behavior are tested. |
| sprintf-js 1.0.3 | [Moderate, template boundary not exposed](https://github.com/advisories/GHSA-hp3w-g68c-fv3c) | 0 | Expo CLI → js-yaml 3 → argparse 1 formats configured CLI templates. No fixed release. The advisory requires attacker control of format templates; Zitch exposes no format-template endpoint and app inputs are not templates. Tests prove extreme precision text remains ordinary data through argparse argument parsing and `%s` formatting. The library still rejects invalid templates; suppressing that normal exception would change its contract without closing a reachable app boundary. Re-review any future dynamic templates. |
| uuid 11.1.1 | [Moderate, fixed](https://github.com/advisories/GHSA-w5hq-g745-h8pq) | 0 | Maintained CommonJS-compatible legacy-11 release, scoped to xcode, Expo Rudder and Expo Bunyan. Their calls use v4()/v1() without options. Tests resolve the dependency from every parent, validate UUIDs/uniqueness, exercise xcode's 24-character project IDs and verify v3/v5 buffer-bound rejection. No mobile spend nonce uses this package. |

## Audit counts and reproducibility

The initial full audit reported 109 inherited findings (79 high, 29 moderate,
1 low, 0 critical), arising from 9 packages with direct advisory records.
After PR #551's send fix, a fresh full audit reported 122 inherited findings
(98 high, 24 moderate, 0 low, 0 critical), from 8 packages / 9 advisory records.
The compatible follow-up upgrades now report **109 inherited findings (97 high,
12 moderate, 0 low, 0 critical), from 6 packages / 6 advisory records**.
The image-size and uuid root findings are removed by maintained releases.
Five remaining packages have tested local mitigations; sprintf's finding
requires untrusted format templates, which are not exposed here.

These counts preserve npm's actual advisory metadata, including propagated
findings. Patched versions are not relabeled and no new exceptions are added.
`npm audit` and `check-npm-audit.mjs` intentionally remain nonzero while the
upstream advisory records remain. `--omit=dev` includes Expo toolchain packages
and does not prove Android bundle membership.

The exact input/output hashes, replacements and provenance are checked into
`scripts/security/tooling-patches.json` and `forge-canonical-patch.json`;
Forge/braces guards are in `scripts/patch-forge-braces.cjs`. All targeted copies
in the lockfile are checked. Unexpected versions or bytes fail installation
before any package source is modified. The package versions remain truthful.
The Forge file exactly matches the pinned pending PR's RSA source, SHA-256
`c7b72e7ef41f4fc0e5228dce3e37851e9ca064c789365d5e392c25d1c5f6e710`.

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
`entry-bb26c796568dcbb04dfb3efaa5336a5c.hbc.map`; SHA-256:
`764ed93eda063133ade41ef4c63c2df7aba5848d8f88ed327d6de9e717ed1aaf`.
The map proves membership for this Android export only; it does not certify
native libraries, other targets, deployment configuration or live-device flows.

Long-term dependency work remains a coordinated Expo/Metro/CSS/server-tool
upgrade so local backports can be retired in favor of maintained parent stacks,
and upstream releases for the packages currently patched locally. Recheck the advisory
links when those parents change; do not erase the residual findings or replace
this evidence with a zero-vulnerability readiness claim.
