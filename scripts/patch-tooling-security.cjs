#!/usr/bin/env node
'use strict';

// These exact-source backports keep Expo 51's protocols and APIs. Versions stay
// unchanged, so advisory scanners still report the locally mitigated packages.
// Maintained image-size 2.x needs the reviewed Metro Buffer/file adapters.
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const patches = require('./security/tooling-patches.json');
const hash = value => createHash('sha256').update(value).digest('hex');

function patchToolingSecurity(projectRoot = path.resolve(__dirname, '..')) {
  const lock = JSON.parse(fs.readFileSync(path.join(projectRoot, 'package-lock.json'), 'utf8'));
  const changes = [];
  for (const patch of patches) {
    const locations = Object.keys(lock.packages).filter(location => location === `node_modules/${patch.package}` || location.endsWith(`/node_modules/${patch.package}`));
    if (!locations.length) throw new Error(`Tooling patch needs review: missing ${patch.package}`);
    for (const location of locations) {
      const packageRoot = path.resolve(projectRoot, location);
      if (!packageRoot.startsWith(path.resolve(projectRoot) + path.sep)) throw new Error('Invalid dependency path');
      const manifest = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));
      if (manifest.name !== patch.package || manifest.version !== patch.version) throw new Error(`Tooling patch needs review: expected ${patch.package}@${patch.version}`);
      const filename = path.join(packageRoot, patch.file);
      const source = fs.readFileSync(filename, 'utf8');
      if (hash(source) === patch.patchedSha256) continue;
      if (hash(source) !== patch.originalSha256) throw new Error(`Tooling patch source changed: ${patch.package}/${patch.file}`);
      let replacement = source;
      for (const [before, after] of patch.replacements) {
        if (replacement.split(before).length !== 2) throw new Error(`Tooling patch target changed: ${patch.file}`);
        replacement = replacement.replace(before, after);
      }
      if (hash(replacement) !== patch.patchedSha256) throw new Error(`Tooling patch output changed: ${patch.file}`);
      changes.push({ filename, replacement });
    }
  }
  // Validate the complete set before writing any dependency source.
  for (const { filename, replacement } of changes) fs.writeFileSync(filename, replacement);
  return changes.length;
}

if (require.main === module) {
  try { console.log(`Verified guarded tooling mitigations (${patchToolingSecurity()} files patched).`); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
module.exports = { patchToolingSecurity };
