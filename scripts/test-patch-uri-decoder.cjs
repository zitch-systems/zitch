'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { spawnSync } = require('node:child_process');
const { patchUriDecoder } = require('./patch-uri-decoder.cjs');

test('the installed decoder remains callable and preserves navigation query semantics', () => {
  assert.equal(patchUriDecoder(), false, 'npm postinstall must apply the decoder mitigation before tests/builds');
  const decode = require('decode-uri-component');
  for (const [input, expected] of [
    ['Ada+Lovelace', 'Ada Lovelace'], ['Ada%20Lovelace', 'Ada Lovelace'],
    ['%2B2348012345678', '+2348012345678'], ['%E2%82%A6', '₦'],
    ['%F0%9F%92%B8', '💸'], ['%FE%FF', '\uFFFD\uFFFD'],
    ['%C2', '\uFFFD'], ['%FF%41', '%FFA'], ['%GZ', '%GZ'],
    ['%FF%2541', '%FF%41'], ['%FF%FE%41', '\uFFFD\uFFFDA'],
    ['%FF%2B+%E2%82%AC', '%FF+ €'], ['%C0%AF', '%C0%AF'],
    ['%ED%A0%80', '%ED%A0%80'], ['%F4%90%80%80', '%F4%90%80%80'],
  ]) assert.equal(decode(input), expected);
  const query = require('query-string');
  assert.equal(query.parse('phone=%2B2348012345678&name=Ada+Lovelace').phone, '+2348012345678');
  assert.equal(query.parse('phone=%2B2348012345678&name=Ada+Lovelace').name, 'Ada Lovelace');
});

test('the backport needs only JavaScript built-ins available in Hermes', () => {
  const context = { module: { exports: {} } };
  vm.runInNewContext(fs.readFileSync(require.resolve('decode-uri-component'), 'utf8'), context);
  assert.equal(context.module.exports('%FF%41'), '%FFA');
});

test('large malformed UTF-8 cannot stall the app decoder', () => {
  // Separate process + hard deadline also terminates the legacy recursive
  // implementation if the installation hook is accidentally removed.
  const result = spawnSync(process.execPath, ['-e', `
    const decode = require(${JSON.stringify(require.resolve('decode-uri-component'))});
    const input = '%FF'.repeat(20000) + '%41';
    const output = decode(input);
    if (output !== '%FF'.repeat(20000) + 'A') process.exit(2);
    const runs = Array.from({length: 10000}, (_, n) => '%FF%41' + n + '/').join('');
    const expected = Array.from({length: 10000}, (_, n) => '%FFA' + n + '/').join('');
    if (decode(runs) !== expected) process.exit(3);
  `], { timeout: 2500, encoding: 'utf8' });
  assert.equal(result.error, undefined);
  assert.equal(result.status, 0, result.stderr.slice(0, 2000));
});

test('patch is idempotent and refuses changed source/version before writing', () => {
  assert.equal(patchUriDecoder(), false);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zitch-uri-patch-test-'));
  try {
    const queryPath = path.join(root, 'node_modules/query-string');
    const decoderPath = path.join(root, 'node_modules/decode-uri-component');
    fs.mkdirSync(queryPath, { recursive: true });
    fs.mkdirSync(decoderPath, { recursive: true });
    fs.writeFileSync(path.join(root, 'package.json'), '{}');
    fs.writeFileSync(path.join(queryPath, 'package.json'), '{"name":"query-string"}');
    fs.writeFileSync(path.join(decoderPath, 'package.json'), '{"name":"decode-uri-component","version":"0.5.0"}');
    const filename = path.join(decoderPath, 'index.js');
    fs.writeFileSync(filename, 'unreviewed');
    assert.throws(() => patchUriDecoder(root), /needs review/);
    fs.writeFileSync(path.join(decoderPath, 'package.json'), '{"name":"decode-uri-component","version":"0.2.2"}');
    assert.throws(() => patchUriDecoder(root), /source changed/);
    assert.equal(fs.readFileSync(filename, 'utf8'), 'unreviewed');
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
