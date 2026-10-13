'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');

test('Metro refuses each vulnerable image parser before reading its contents', () => {
  require('../metro.config.js');
  const { imageSize } = require('image-size');
  // Well-formed identifying headers: safe even if the mitigation regresses.
  const probes = {
    icns: b => { b.write('icns'); b.writeUInt32BE(16, 4); b.write('ic09', 8); b.writeUInt32BE(8, 12); },
    heif: b => { b.writeUInt32BE(24, 0); b.write('ftypmif1', 4); },
    jxl: b => { b.writeUInt32BE(12, 0); b.write('JXL ', 4); b.writeUInt32BE(0x0d0a870a, 8); b.writeUInt32BE(20, 12); b.write('ftyp', 16); b.write('jxl ', 20); },
    'jxl-stream': b => { b[0] = 0xff; b[1] = 0x0a; },
  };
  for (const [type, prepare] of Object.entries(probes)) {
    const bytes = Buffer.alloc(64);
    prepare(bytes);
    assert.throws(() => imageSize(bytes), { message: `disabled file type: ${type}` });
  }
});
