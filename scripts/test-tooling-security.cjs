'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const { pathToFileURL } = require('node:url');
const { spawnSync } = require('node:child_process');
const { patchToolingSecurity } = require('./patch-tooling-security.cjs');
const patches = require('./security/tooling-patches.json');

test('postinstall applied every reviewed tooling patch and is idempotent', () => {
  assert.equal(patchToolingSecurity(), 0);
});

test('changed dependency source or version fails before any patch write', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zitch-tooling-patches-'));
  try {
    const packages = {};
    for (const patch of patches) {
      const location = `node_modules/${patch.package}`;
      packages[location] = { version: patch.version };
      const dir = path.join(root, location);
      const installed = path.dirname(require.resolve(`${patch.package}/package.json`));
      fs.mkdirSync(path.dirname(path.join(dir, patch.file)), { recursive: true });
      fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ name: patch.package, version: patch.version }));
      fs.copyFileSync(path.join(installed, patch.file), path.join(dir, patch.file));
    }
    fs.writeFileSync(path.join(root, 'package-lock.json'), JSON.stringify({ packages }));
    assert.equal(patchToolingSecurity(root), 0);
    const metro = path.join(root, 'node_modules/metro/src/Assets.js');
    let original = fs.readFileSync(metro, 'utf8');
    for (const [before, after] of [...patches[0].replacements].reverse()) original = original.replace(after, before);
    fs.writeFileSync(metro, original);
    const last = patches.at(-1);
    const changed = path.join(root, 'node_modules', last.package, last.file);
    fs.appendFileSync(changed, '\n// unexpected source');
    assert.throws(() => patchToolingSecurity(root), /source changed/);
    assert.equal(fs.readFileSync(metro, 'utf8'), original, 'later invalid source must prevent earlier writes');
    fs.writeFileSync(path.join(root, 'node_modules/metro/package.json'), '{"name":"metro","version":"999.0.0"}');
    assert.throws(() => patchToolingSecurity(root), /needs review/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('Metro adapter reads image-size 2.x dimensions from Buffer', () => {
  const { getAssetSize } = require('metro/src/Assets');
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aVqQAAAAASUVORK5CYII=', 'base64');
  assert.deepEqual(getAssetSize('png', png, 'one.png'), { width: 1, height: 1 });
  assert.deepEqual(getAssetSize('svg', Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="30" height="40"></svg>'), 'one.svg'), { width: 30, height: 40 });
});

test('Metro resolves regular and zip-contained asset file paths through the maintained parser', async () => {
  const { getAssetData } = require('metro/src/Assets');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zitch-image-assets-'));
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aVqQAAAAASUVORK5CYII=', 'base64');
  try {
    for (const relative of ['plain', 'archive.zip']) {
      const dir = path.join(root, relative);
      fs.mkdirSync(dir);
      const assetPath = path.join(dir, 'icon.png');
      fs.writeFileSync(assetPath, png);
      const asset = await getAssetData(assetPath, relative, [], 'android', '/assets');
      assert.equal(asset.width, 1);
      assert.equal(asset.height, 1);
      assert.deepEqual(asset.files, [assetPath]);
      assert.deepEqual(asset.scales, [1]);
    }
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('maintained image parser terminates on malformed ICNS, HEIF and JPEG-XL headers', () => {
  const child = spawnSync(process.execPath, ['-e', `
    const {imageSize} = require('image-size');
    const cases=[];
    const icns=Buffer.alloc(64);icns.write('icns');icns.writeUInt32BE(64,4);icns.write('ic09',8);cases.push(icns);
    const heif=Buffer.alloc(64);heif.write('ftypmif1',4);cases.push(heif);
    const jxl=Buffer.alloc(64);jxl.writeUInt32BE(12,0);jxl.write('JXL ',4);jxl.writeUInt32BE(0x0d0a870a,8);jxl.write('ftyp',16);jxl.write('jxl ',20);cases.push(jxl);
    for(const b of cases){try{imageSize(b)}catch{}}
  `], { cwd: path.resolve(__dirname, '..'), encoding: 'utf8', timeout: 2500 });
  assert.equal(child.error, undefined);
  assert.equal(child.status, 0, child.stderr);
});

test('selector backport preserves escaped classes, IDs, interpolation and insertion semantics', () => {
  const parser = require('postcss-selector-parser');
  const fixtures = ['.a.b', '#x.y', '.hover\\:bg-blue-500:hover', '.a\\.b', 'svg|a[href^="https:"]', ':not(.a, #b)', '#{color}.foo', '33.33333%'];
  for (const selector of fixtures) assert.equal(parser().processSync(selector), selector);
  assert.deepEqual(parser().astSync('.a.b').first.nodes.map(n => [n.type,n.value]), [['class','a'],['class','b']]);
  assert.deepEqual(parser().astSync('#x.y').first.nodes.map(n => [n.type,n.value]), [['id','x'],['class','y']]);
  // v6 mutation behavior remains untouched: parent tools keep their pinned API.
  const root = parser().astSync('.a.b');
  root.first.insertBefore(root.first.last, parser.className({ value: 'c' }));
  assert.equal(String(root), '.a.c.b');
});

test('large flat selectors and Sass interpolation cannot monopolize the parser', () => {
  const child = spawnSync(process.execPath, ['-e', `
    const p=require('postcss-selector-parser');
    for(const atom of ['.a','#a','#{a}']) {
      const ast=p().astSync(atom.repeat(60000));
      if(!ast.first || String(ast)!==atom.repeat(60000))process.exit(2);
    }
  `], { cwd: path.resolve(__dirname, '..'), encoding: 'utf8', timeout: 6000 });
  assert.equal(child.error, undefined);
  assert.equal(child.status, 0, child.stderr);
});

async function streamValue(api, input) {
  const decoded = await api.decode(api.encode(input));
  await decoded.done;
  return decoded.value;
}

test('both turbo-stream entry points preserve v2 values and never select global constructors from input', async () => {
  const modulePath = path.join(path.dirname(require.resolve('turbo-stream/package.json')), 'dist/turbo-stream.mjs');
  const modules = [require('turbo-stream'), await import(pathToFileURL(modulePath).href)];
  let constructed = 0;
  globalThis.ZitchUntrustedConstructor = class { constructor() { constructed++; } };
  try {
    for (const api of modules) {
      const input = { text: 'Ada', count: 23n, date: new Date('2026-01-01'), map: new Map([['x', 1]]), set: new Set([2, 3]) };
      assert.deepEqual(await streamValue(api, input), input);
      const original = new TypeError('safe message');
      original.name = 'ZitchUntrustedConstructor';
      const output = await streamValue(api, original);
      assert.equal(Object.getPrototypeOf(output), Error.prototype);
      assert.equal(output.message, 'safe message');
      const malicious = new ReadableStream({start(controller) {
        controller.enqueue(new TextEncoder().encode('[["E","message","ZitchUntrustedConstructor"]]\n'));
        controller.close();
      }});
      const decoded = await api.decode(malicious);
      await decoded.done;
      assert.equal(Object.getPrototypeOf(decoded.value), Error.prototype);
      assert.equal(decoded.value.message, 'message');
    }
    assert.equal(constructed, 0);
  } finally { delete globalThis.ZitchUntrustedConstructor; }
});

test('scoped UUID upgrades preserve every build-tool call and enforce buffer bounds', () => {
  for (const parent of ['xcode', '@expo/rudder-sdk-node', '@expo/bunyan']) {
    const fromParent = createRequire(require.resolve(`${parent}/package.json`));
    assert.equal(fromParent('uuid/package.json').version, '11.1.1');
    const uuid = fromParent('uuid');
    for (const version of [1,4]) {
      const values = new Set(Array.from({length:100}, () => uuid[`v${version}`]()));
      assert.equal(values.size, 100);
      for (const value of values) { assert(uuid.validate(value)); assert.equal(uuid.version(value), version); }
    }
    for (const name of ['v3', 'v5']) {
      assert.throws(() => uuid[name]('value', uuid[name].DNS, new Uint8Array(15)), RangeError);
      assert.throws(() => uuid[name]('value', uuid[name].DNS, new Uint8Array(30), -1), RangeError);
      assert.throws(() => uuid[name]('value', uuid[name].DNS, new Uint8Array(30), 15), RangeError);
    }
  }
  const project = require('xcode').project('test.pbxproj');
  project.hash = { project: { objects: {} } };
  assert.match(project.generateUuid(), /^[A-F0-9]{24}$/);
});

test('CLI argument values remain data rather than sprintf precision templates', () => {
  const { ArgumentParser } = require('argparse');
  const parser = new ArgumentParser({ addHelp: false });
  parser.addArgument('value');
  const hostile = '%.999999999999999999999999999999999999f';
  assert.equal(parser.parseArgs([hostile]).value, hostile);
  assert.equal(require('sprintf-js').sprintf('Argument: %s', hostile), `Argument: ${hostile}`);
});
