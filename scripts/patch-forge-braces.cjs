#!/usr/bin/env node
'use strict';

// Local mitigations for releases that still have no published upstream fix:
// - node-forge GHSA-86w9-cpqp-85rv: canonical encoding from pending upstream PR 1159,
//   https://github.com/digitalbazaar/forge/pull/1159 (not an official release).
// - braces GHSA-vfj7-8cjw-p6xm: a local 100-container AST depth bound, following
//   https://github.com/micromatch/braces/issues/70, including parentheses and
//   caller-provided ASTs. This bounds traversal depth, not all glob complexity.
// Keep package versions truthful. Audit still identifies these releases; source
// hashes and behavioral regressions verify the mitigations independently.
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { createRequire } = require('node:module');

const forgePatch = require('./security/forge-canonical-patch.json');

const digest = source => createHash('sha256').update(source).digest('hex');
const DEPTH_ERROR = "      throw new SyntaxError('braces AST nesting exceeds safety limit (100)');";

function replaceOnce(source, original, replacement) {
  if (source.split(original).length !== 2) {
    throw new Error('Forge/braces patch anchor changed; refusing an unreviewed modification');
  }
  return source.replace(original, replacement);
}

function patchWalker(source, name) {
  const fn = name === 'stringify' ? 'stringify' : 'walk';
  source = replaceOnce(source,
    `const ${fn} = (node, parent = {}) => {`,
    `const ${fn} = (node, parent = {}, depth = 0) => {\n` +
      '    if (node.nodes && depth > 100) {\n' + DEPTH_ERROR + '\n    }');
  // stringify historically omits its parent argument when descending. Preserve
  // that behavior; introducing parent here would also change escaping semantics.
  return replaceOnce(source,
    name === 'stringify' ? 'stringify(child);' : 'walk(child, node);',
    name === 'stringify' ? 'stringify(child, undefined, depth + 1);' : 'walk(child, node, depth + 1);');
}

const PACKAGES = {
  'node-forge': {
    version: '1.4.0',
    files: [{
      relative: 'lib/rsa.js',
      original: forgePatch.originalSha256,
      patched: forgePatch.patchedSha256,
      transform: source => forgePatch.replacements.reduce(
        (result, [before, after]) => replaceOnce(result, before, after), source),
    }],
  },
  braces: {
    version: '3.0.3',
    files: [{
      relative: 'lib/parse.js',
      original: 'e572166565f15fa6ad9865ae49d678218e32aabfd1b3720f6d0d43d39800d310',
      patched: 'a08b38927dbf640e53d6684fdb54608a14e6d5ef38fe25b1e1cd2efbdd28872e',
      transform: source => replaceOnce(source, '  const push = node => {',
        '  const push = node => {\n' +
        '    if (node.nodes && stack.length > 100) {\n' + DEPTH_ERROR + '\n    }'),
    }, {
      relative: 'lib/compile.js',
      original: 'dc98f22eee3d511785d92a00758d5f0d48efed5f5813bdecc2de430c529b5c9f',
      patched: '5cec8655cb05930265f5a26006365232fe9dd0f9b7ca5db7332b7294356edd8e',
      transform: source => patchWalker(source, 'compile'),
    }, {
      relative: 'lib/expand.js',
      original: '41ccc196ebfa7b7781a634e721eb744e4e7bcb54cba427a7e3d6806a1b9e58f7',
      patched: '8fff3d53b2bba418fb493d9cda74ae63bcdf3ffe5ffaf4ffddd68b49395315fe',
      transform: source => patchWalker(source, 'expand'),
    }, {
      relative: 'lib/stringify.js',
      original: '379f22d77bfa1478341ccd49c5e4267464aabcbba03558bab332aac23fc6f23a',
      patched: '920185dd9d149ccaed0891193e11d70d846635081d5e2f3f7e537c537bc5a87b',
      transform: source => patchWalker(source, 'stringify'),
    }],
  },
};

function packageManifests(projectRoot) {
  const projectRequire = createRequire(path.join(projectRoot, 'package.json'));
  const manifests = new Map(Object.keys(PACKAGES).map(name => [projectRequire.resolve(`${name}/package.json`), name]));
  const lockPath = path.join(projectRoot, 'package-lock.json');
  if (fs.existsSync(lockPath)) {
    const lock = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
    for (const relative of Object.keys(lock.packages || {})) {
      const match = relative.match(/(?:^|\/)node_modules\/(node-forge|braces)$/);
      if (!match) continue;
      const filename = path.resolve(projectRoot, relative, 'package.json');
      if (!filename.startsWith(projectRoot + path.sep)) {
        throw new Error('Forge/braces lockfile package path escapes the project');
      }
      // Optional platform-specific packages can be present in a lockfile while
      // absent on disk; the two root-resolved runtime copies are always required.
      if (fs.existsSync(filename)) manifests.set(filename, match[1]);
    }
  }
  return manifests;
}

function patchForgeBraces(projectRoot = path.resolve(__dirname, '..')) {
  projectRoot = path.resolve(projectRoot);
  const changes = [];
  for (const [manifestPath, name] of packageManifests(projectRoot)) {
    const spec = PACKAGES[name];
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    if (manifest.name !== name || manifest.version !== spec.version) {
      throw new Error(`Forge/braces patch needs review: expected ${name} ${spec.version}, got ${manifest.name} ${manifest.version}`);
    }
    for (const file of spec.files) {
      const filename = path.join(path.dirname(manifestPath), file.relative);
      const source = fs.readFileSync(filename, 'utf8');
      const hash = digest(source);
      if (hash === file.patched) continue;
      if (hash !== file.original) {
        throw new Error(`Forge/braces patch source changed: ${name}/${file.relative}`);
      }
      const replacement = file.transform(source);
      if (digest(replacement) !== file.patched) {
        throw new Error(`Forge/braces backport changed: review output hash for ${name}/${file.relative}`);
      }
      changes.push({ filename, source: replacement });
    }
  }
  // Prevalidate every copy and file before modifying any of them.
  for (const change of changes) fs.writeFileSync(change.filename, change.source);
  return changes.length;
}

if (require.main === module) {
  try {
    console.log(`Verified local node-forge 1.4.0 / braces 3.0.3 mitigations (${patchForgeBraces()} file(s) patched).`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}

module.exports = { patchForgeBraces };
