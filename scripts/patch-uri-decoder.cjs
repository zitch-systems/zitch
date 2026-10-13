#!/usr/bin/env node
'use strict';

// Navigation 6 / query-string 7 require a callable CommonJS decoder. Upstream's
// patched 0.5.0 is ESM-only and also changed '+' handling, so backport only its
// linear UTF-8 fallback to the exact legacy source. Keep its package version
// truthful: npm audit still reports the advisory, with this mitigation recorded
// and exercised separately. Changed versions or source require a fresh review.
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { createRequire } = require('node:module');

const VERSION = '0.2.2';
const ORIGINAL_SHA256 = '3b8ba0a765e1089d11bba9a919d0d8789ecd9a6833ea078d8d9114e1db093863';
const digest = source => createHash('sha256').update(source).digest('hex');

function patchUriDecoder(projectRoot = path.resolve(__dirname, '..')) {
  const projectRequire = createRequire(path.join(projectRoot, 'package.json'));
  const queryRequire = createRequire(projectRequire.resolve('query-string/package.json'));
  const manifestPath = queryRequire.resolve('decode-uri-component/package.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  if (manifest.version !== VERSION) throw new Error(`URI decoder patch needs review: expected ${VERSION}, got ${manifest.version}`);
  const filename = path.join(path.dirname(manifestPath), 'index.js');
  const source = fs.readFileSync(filename, 'utf8');
  const linear = fs.readFileSync(path.join(__dirname, 'security/decode-uri-component-linear.inc'), 'utf8') + '\n';
  const start = source.indexOf('function decodeComponents(');
  const end = source.indexOf('module.exports = function (encodedURI)');
  if (digest(source) === PATCHED_SHA256) return false;
  if (digest(source) !== ORIGINAL_SHA256 || start < 0 || end <= start) {
    throw new Error('URI decoder patch source changed; refusing an unreviewed modification');
  }
  const replacement = (source.slice(0, start) + linear + source.slice(end))
    .replace("var singleMatcher = new RegExp('(' + token + ')|([^%]+?)', 'gi');\n", '');
  if (digest(replacement) !== PATCHED_SHA256) throw new Error('URI decoder backport changed; review the patch hash');
  fs.writeFileSync(filename, replacement);
  return true;
}

const PATCHED_SHA256 = 'a5c7d5b07e6c2a11e8c20f54cdb018c9723a85d4e053e8b1071d8710351e9dab';

if (require.main === module) {
  try { console.log(`Verified URI decoder ${VERSION} linear fallback (${patchUriDecoder() ? 'patched' : 'already patched'}).`); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}

module.exports = { patchUriDecoder };
