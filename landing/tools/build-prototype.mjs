// Compiles the web app demo (prototype.html) ahead of time.
//
// The demo used to ship its .jsx files to the browser with Babel standalone and
// the React development builds, so every visitor downloaded ~3 MB and compiled
// the app on their phone before seeing anything. This turns the same sources
// into one plain script, in the same order, with only the JSX rewritten: each
// file keeps its own IIFE and its window exports, exactly as before.
//
//   npm install --no-save esbuild@0.24.2   # once, at the repo root
//   node landing/tools/build-prototype.mjs
//
// Edit the .jsx files, run this, and commit the new bundle. The output name
// carries a content hash because /assets/* is cached as immutable; the script
// rewrites the <script> tag in prototype.html to point at it.
import { readFileSync, writeFileSync, readdirSync, unlinkSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { transformSync } from 'esbuild';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const SOURCES = ['shared.jsx', 'app/data.js', 'app/ui.jsx', 'app/flows.jsx', 'app/tabs.jsx', 'app/auth.jsx', 'app/App.jsx'];
const OUT_DIR = 'assets/proto';

const parts = SOURCES.map((file) => {
  const src = readFileSync(join(root, file), 'utf8');
  const { code } = transformSync(src, {
    loader: file.endsWith('.jsx') ? 'jsx' : 'js', jsx: 'transform',
    target: 'es2019', minify: true, sourcefile: file, legalComments: 'none',
  });
  return `/* ${file} */\n${code}`;
});
const bundle = parts.join('\n');
const hash = createHash('sha256').update(bundle).digest('hex').slice(0, 10);
const name = `app.${hash}.js`;

for (const old of readdirSync(join(root, OUT_DIR))) if (/^app\.[0-9a-f]+\.js$/.test(old) && old !== name) unlinkSync(join(root, OUT_DIR, old));
writeFileSync(join(root, OUT_DIR, name), bundle);

const page = join(root, 'prototype.html');
const html = readFileSync(page, 'utf8');
const next = html.replace(/assets\/proto\/app\.[0-9a-f]+\.js/, `${OUT_DIR}/${name}`);
if (next === html && !html.includes(`${OUT_DIR}/${name}`)) throw new Error('prototype.html has no assets/proto/app.<hash>.js script tag to update');
writeFileSync(page, next);
console.log(`${OUT_DIR}/${name}  ${(bundle.length / 1024).toFixed(1)} KB`);
