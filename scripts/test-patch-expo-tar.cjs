'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { patchExpoTar } = require('./patch-expo-tar.cjs');

function fixture(t, { cli = '0.18.31', tar = '7.5.22', secondSource } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zitch-expo-tar-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const cliRoot = path.join(root, 'node_modules/@expo/cli');
  fs.mkdirSync(path.join(cliRoot, 'build/src/utils'), { recursive: true });
  fs.mkdirSync(path.join(root, 'node_modules/tar'), { recursive: true });
  fs.writeFileSync(path.join(root, 'package.json'), '{}');
  fs.writeFileSync(path.join(cliRoot, 'package.json'), JSON.stringify({ name: '@expo/cli', version: cli }));
  fs.writeFileSync(path.join(root, 'node_modules/tar/package.json'), JSON.stringify({ name: 'tar', version: tar }));
  const files = ['tar.js', 'npm.js'].map((name) => path.join(cliRoot, 'build/src/utils', name));
  fs.writeFileSync(files[0], 'await _tar().default.extract({ file: input });');
  fs.writeFileSync(files[1], secondSource ?? 'pipeline(stream, _tar().default.extract({ cwd }));');
  return { root, files };
}

test('patches both known sites and is safe to run repeatedly', (t) => {
  const { root, files } = fixture(t);
  assert.equal(patchExpoTar(root), 2);
  for (const file of files) assert.match(fs.readFileSync(file, 'utf8'), /\(_tar\(\)\.default \|\| _tar\(\)\)\.extract/);
  assert.equal(patchExpoTar(root), 0);
});

test('rejects changed source before modifying either file', (t) => {
  const { root, files } = fixture(t, { secondSource: 'different.extract({});' });
  const original = fs.readFileSync(files[0], 'utf8');
  assert.throws(() => patchExpoTar(root), /source changed/);
  assert.equal(fs.readFileSync(files[0], 'utf8'), original);
});

test('rejects an unreviewed Expo CLI version', (t) => {
  assert.throws(() => patchExpoTar(fixture(t, { cli: '0.19.0' }).root), /needs review/);
});

test('rejects an unreviewed tar version', (t) => {
  assert.throws(() => patchExpoTar(fixture(t, { tar: '8.0.0' }).root), /needs review/);
});
