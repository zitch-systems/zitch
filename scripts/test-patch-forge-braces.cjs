'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { patchForgeBraces } = require('./patch-forge-braces.cjs');

test('forge/braces guarded patches are installed and idempotent', () => {
  assert.equal(patchForgeBraces(), 0);
});

test('forge/braces refuses changed source and version before writing another package', () => {
  const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zitch-forge-braces-'));
  try {
    fs.writeFileSync(path.join(root, 'package.json'), '{}');
    for (const [name, files] of Object.entries({ 'node-forge': ['lib/rsa.js'], braces: ['lib/parse.js', 'lib/compile.js', 'lib/expand.js', 'lib/stringify.js'] })) {
      const installed = path.dirname(require.resolve(`${name}/package.json`));
      const dir = path.join(root, 'node_modules', name);
      fs.mkdirSync(path.join(dir, 'lib'), { recursive: true });
      for (const file of ['package.json', ...files]) fs.copyFileSync(path.join(installed, file), path.join(dir, file));
    }
    assert.equal(patchForgeBraces(root), 0);
    const forgePath = path.join(root, 'node_modules/node-forge/lib/rsa.js');
    let original = fs.readFileSync(forgePath, 'utf8');
    for (const [before, after] of [...require('./security/forge-canonical-patch.json').replacements].reverse()) {
      original = original.replace(after, before);
    }
    fs.writeFileSync(forgePath, original);
    fs.appendFileSync(path.join(root, 'node_modules/braces/lib/stringify.js'), '\n// unreviewed');
    assert.throws(() => patchForgeBraces(root), /source changed/);
    assert.equal(fs.readFileSync(forgePath, 'utf8'), original);
    fs.writeFileSync(path.join(root, 'node_modules/node-forge/package.json'), '{"name":"node-forge","version":"999.0.0"}');
    assert.throws(() => patchForgeBraces(root), /needs review/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('real RSA verification accepts valid signatures and rejects nested DigestAlgorithm garbage', () => {
  const forge = require('node-forge');
  const { privateKey, publicKey } = forge.pki.rsa.generateKeyPair({ bits: 1024, e: 3 });
  const md = forge.md.sha256.create().update('Zitch certificate verification regression');
  const digest = md.digest().getBytes();
  const valid = privateKey.sign(md);
  assert.equal(publicKey.verify(digest, valid), true);
  assert.equal(publicKey.verify(forge.md.sha256.create().update('different').digest().getBytes(), valid), false);
  const asn1 = forge.asn1;
  const node = (type, constructed, value) => asn1.create(asn1.Class.UNIVERSAL, type, constructed, value);
  const oid = () => node(asn1.Type.OID, false, asn1.oidToDer(forge.pki.oids.sha256).getBytes());
  const nil = () => node(asn1.Type.NULL, false, '');
  const garbage = () => node(asn1.Type.OCTETSTRING, false, 'unexpected data');
  const derFor = algorithm => {
    const info = node(asn1.Type.SEQUENCE, true, [node(asn1.Type.SEQUENCE, true, algorithm), node(asn1.Type.OCTETSTRING, false, digest)]);
    // Sign a constructed DigestInfo using real RSA PKCS#1 padding. No padding or
    // verification checks are skipped; this isolates the nested ASN.1 boundary.
    return asn1.toDer(info).getBytes();
  };
  const sign = algorithm => privateKey.sign(derFor(algorithm), 'NONE');
  assert.equal(publicKey.verify(digest, sign([oid()])), true);
  assert.equal(publicKey.verify(digest, sign([oid(), nil()])), true);
  for (const algorithm of [[oid(), nil(), garbage()], [oid(), garbage()], [oid(), nil(), nil()], [oid(), node(asn1.Type.NULL, false, 'X'.repeat(30))]]) {
    assert.throws(() => publicKey.verify(digest, sign(algorithm)), /valid RSASSA-PKCS1-v1_5 DigestInfo/);
  }
  const der = derFor([oid(), nil()]);
  const withOuterLength = der[0] + '\x81' + der.slice(1);
  const withInnerLength = der[0] + String.fromCharCode(der.charCodeAt(1) + 1) + der[2] + '\x81' + der.slice(3);
  const digestOffset = 4 + der.charCodeAt(3);
  const withDigestLength = der[0] + String.fromCharCode(der.charCodeAt(1) + 1) + der.slice(2, digestOffset + 1) + '\x81' + der.slice(digestOffset + 1);
  for (const noncanonical of [withOuterLength, withInnerLength, withDigestLength]) {
    assert.throws(() => publicKey.verify(digest, privateKey.sign(noncanonical, 'NONE')), /valid RSASSA-PKCS1-v1_5 DigestInfo/);
  }
});

test('canonical Forge backport interoperates with Node/OpenSSL signatures and preserves PSS/NONE', () => {
  const crypto = require('node:crypto');
  const forge = require('node-forge');
  const data = Buffer.from('Zitch real OpenSSL interoperability');
  for (const exponent of [3, 65537]) {
    const keys = crypto.generateKeyPairSync('rsa', { modulusLength: 2048, publicExponent: exponent });
    const publicKey = forge.pki.publicKeyFromPem(keys.publicKey.export({ type: 'pkcs1', format: 'pem' }));
    for (const algorithm of ['sha256', 'sha384', 'sha512']) {
      const signature = crypto.sign(algorithm, data, keys.privateKey);
      const digest = crypto.createHash(algorithm).update(data).digest('binary');
      assert.equal(publicKey.verify(digest, signature.toString('binary')), true);
      assert.equal(publicKey.verify('wrong digest', signature.toString('binary')), false);
    }
    const pssSignature = crypto.sign('sha256', data, { key: keys.privateKey, padding: crypto.constants.RSA_PKCS1_PSS_PADDING, saltLength: 32 });
    const pss = forge.pss.create({ md: forge.md.sha256.create(), mgf: forge.mgf.mgf1.create(forge.md.sha256.create()), saltLength: 32 });
    assert.equal(publicKey.verify(crypto.createHash('sha256').update(data).digest('binary'), pssSignature.toString('binary'), pss), true);
    const privateKey = forge.pki.privateKeyFromPem(keys.privateKey.export({ type: 'pkcs1', format: 'pem' }));
    assert.equal(publicKey.verify('plain', privateKey.sign('plain', 'NONE'), 'NONE'), true);
  }
});

test('braces preserves common glob/range/quoted/escaped patterns', () => {
  const braces = require('braces');
  assert.deepEqual(braces.expand('src/{app,lib}/*.{ts,tsx}'), ['src/app/*.ts','src/app/*.tsx','src/lib/*.ts','src/lib/*.tsx']);
  assert.deepEqual(braces.expand('{1..3}'), ['1','2','3']);
  assert.deepEqual(braces.expand('{a..c}'), ['a','b','c']);
  assert.deepEqual(braces.expand('a\\{b,c\\}'), ['a{b,c}']);
  assert.equal(braces.compile('a/{b,c}/d'), 'a/(b|c)/d');
  for (const pattern of ['plain','a/{b,c}','{1..3}','{a..c}','{a,{b,c}}','(a|b)','"{a,b}"','[a-z]','foo/{a,b','a\\{b,c\\}']) {
    assert.equal(braces.stringify(braces.parse(pattern)), pattern.replace(/\\([{}])/g, '$1').replace(/^"|"$/g, ''));
  }
});

test('all braces APIs bound parentheses and braces at 100 AST containers', () => {
  const braces = require('braces');
  for (const [open, close] of [['{','}'], ['(',')']]) {
    for (const api of ['parse','compile','expand','stringify']) {
      assert.doesNotThrow(() => braces[api](open.repeat(100) + 'a' + close.repeat(100)));
      for (const depth of [101,4500]) {
        assert.throws(() => braces[api](open.repeat(depth) + 'a' + close.repeat(depth)), /AST nesting exceeds safety limit/);
      }
    }
  }
});

test('caller-provided ASTs cannot bypass braces traversal bounds', () => {
  const braces = require('braces');
  function deepAst() {
    const root = {type:'root', nodes:[]};
    let current = root;
    for (let n=0;n<150;n++) { const child={type:'paren',nodes:[],parent:current}; current.nodes.push(child);current=child; }
    current.nodes.push({type:'text',value:'a'});
    return root;
  }
  for (const api of ['compile','expand','stringify']) assert.throws(() => braces[api](deepAst()), /AST nesting exceeds safety limit/);
});
