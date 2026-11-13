#!/usr/bin/env node
/**
 * Bakes the mobile app's loader sprite sheet from the web renderer, so the app
 * plays exactly the animation the website draws live.
 *
 *   source:  landing/assets/zitch-loader.v1.js   (ZitchLoader.drawFrame)
 *   output:  assets/images/zitch-loader-sprite.png
 *
 * Dev-only tool — not part of the app build. Needs Playwright + Chromium:
 *   npx --yes -p playwright node scripts/bake-loader-sprite.cjs
 *
 * If you change FRAMES / COLS / FRAME_W / FRAME_H here, change the matching
 * constants in components/design/Loading.tsx.
 */
const fs = require('fs');
const path = require('path');

const FRAMES = 84;          // 2.8s beat at 30fps
const COLS = 12;
const ROWS = Math.ceil(FRAMES / COLS);
const FRAME_W = 168;        // px per cell
const FRAME_H = 256;
const SCALE = 1.2;          // px per logical unit (140-unit-wide box → 168px)
const SUPER = 4;            // draw 4× then box-filter down for clean edges

const ROOT = path.join(__dirname, '..');
const SRC = path.join(ROOT, 'landing', 'assets', 'zitch-loader.v1.js');
const OUT = path.join(ROOT, 'assets', 'images', 'zitch-loader-sprite.png');

(async () => {
  const { chromium } = require('playwright');
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setContent('<!doctype html><html><body></body></html>');
  await page.addScriptTag({ content: fs.readFileSync(SRC, 'utf8') });

  const result = await page.evaluate(({ FRAMES, COLS, ROWS, FRAME_W, FRAME_H, SCALE, SUPER }) => {
    const sheet = document.createElement('canvas');
    sheet.width = COLS * FRAME_W; sheet.height = ROWS * FRAME_H;
    const sctx = sheet.getContext('2d');
    const big = document.createElement('canvas');
    big.width = FRAME_W * SUPER; big.height = FRAME_H * SUPER;
    const bctx = big.getContext('2d');
    const halfC = document.createElement('canvas');
    halfC.width = FRAME_W * 2; halfC.height = FRAME_H * 2;
    const hctx = halfC.getContext('2d');
    const cell = document.createElement('canvas');
    cell.width = FRAME_W; cell.height = FRAME_H;
    const cctx = cell.getContext('2d');
    let touching = [];

    for (let i = 0; i < FRAMES; i++) {
      const k = SCALE * SUPER;
      bctx.setTransform(1, 0, 0, 1, 0, 0);
      bctx.clearRect(0, 0, big.width, big.height);
      bctx.setTransform(k, 0, 0, k, big.width / 2, big.height / 2);
      window.ZitchLoader.drawFrame(bctx, i / FRAMES, { reveal: 1, shadow: false });
      // 4× → 2× → 1× (each step an exact 2:1 box filter)
      hctx.clearRect(0, 0, halfC.width, halfC.height);
      hctx.drawImage(big, 0, 0, halfC.width, halfC.height);
      cctx.clearRect(0, 0, FRAME_W, FRAME_H);
      cctx.drawImage(halfC, 0, 0, FRAME_W, FRAME_H);
      // every cell must keep a transparent border, or neighbouring frames would bleed
      const d = cctx.getImageData(0, 0, FRAME_W, FRAME_H).data;
      let edge = false;
      for (let x = 0; x < FRAME_W && !edge; x++) for (const y of [0, 1, FRAME_H - 2, FRAME_H - 1]) if (d[(y * FRAME_W + x) * 4 + 3]) edge = true;
      for (let y = 0; y < FRAME_H && !edge; y++) for (const x of [0, 1, FRAME_W - 2, FRAME_W - 1]) if (d[(y * FRAME_W + x) * 4 + 3]) edge = true;
      if (edge) touching.push(i);
      sctx.drawImage(cell, (i % COLS) * FRAME_W, Math.floor(i / COLS) * FRAME_H);
    }
    return { png: sheet.toDataURL('image/png'), touching, w: sheet.width, h: sheet.height };
  }, { FRAMES, COLS, ROWS, FRAME_W, FRAME_H, SCALE, SUPER });

  await browser.close();
  if (result.touching.length) {
    console.error('Frames touch the cell edge (would bleed):', result.touching.join(', '));
    process.exit(1);
  }
  fs.writeFileSync(OUT, Buffer.from(result.png.split(',')[1], 'base64'));
  console.log(`Wrote ${path.relative(ROOT, OUT)} — ${result.w}×${result.h}, ${FRAMES} frames (${COLS}×${ROWS}), ${(fs.statSync(OUT).size / 1024).toFixed(0)} KB`);
})().catch((e) => { console.error(e); process.exit(1); });
