#!/usr/bin/env node
'use strict';

// Expo SDK 51 imports tar through Babel's default-import helper. Patched tar 7
// exposes CommonJS named exports with __esModule, so that old helper does not
// synthesize `.default`. Keep the security update while bridging only those two
// known import sites. A changed package version/source fails installation and
// requires review instead of silently applying a potentially incorrect patch.
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');

const CLI_VERSION = '0.18.31';
const TAR_VERSION = '7.5.22';
const FILES = ['build/src/utils/tar.js', 'build/src/utils/npm.js'];
const ORIGINAL = '_tar().default.extract(';
const PATCHED = '(_tar().default || _tar()).extract(';

function patchExpoTar(projectRoot = path.resolve(__dirname, '..')) {
  const projectRequire = createRequire(path.join(projectRoot, 'package.json'));
  const cliManifest = projectRequire.resolve('@expo/cli/package.json');
  const cliRequire = createRequire(cliManifest);
  const cliVersion = JSON.parse(fs.readFileSync(cliManifest, 'utf8')).version;
  const tarVersion = cliRequire('tar/package.json').version;
  if (cliVersion !== CLI_VERSION || tarVersion !== TAR_VERSION) {
    throw new Error(`Expo tar compatibility patch needs review: expected CLI ${CLI_VERSION} / tar ${TAR_VERSION}, got ${cliVersion} / ${tarVersion}`);
  }
  const changes = FILES.map((relative) => {
    const filename = path.join(path.dirname(cliManifest), relative);
    const source = fs.readFileSync(filename, 'utf8');
    const originalCount = source.split(ORIGINAL).length - 1;
    const patchedCount = source.split(PATCHED).length - 1;
    if (originalCount === 0 && patchedCount === 1) return { filename, changed: false };
    if (originalCount !== 1 || patchedCount !== 0) {
      throw new Error(`Expo tar compatibility patch source changed: ${relative}`);
    }
    return { filename, changed: true, source: source.replace(ORIGINAL, PATCHED) };
  });
  // Validate BOTH sources before any write, so a failed check leaves them alone.
  for (const change of changes) if (change.changed) fs.writeFileSync(change.filename, change.source);
  return changes.filter((change) => change.changed).length;
}

if (require.main === module) {
  try {
    const changed = patchExpoTar();
    console.log(`Verified Expo ${CLI_VERSION} / tar ${TAR_VERSION} compatibility (${changed} file(s) patched).`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}

module.exports = { patchExpoTar };
