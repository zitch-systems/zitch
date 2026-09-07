#!/usr/bin/env node
/**
 * Parse every browser-served JS/JSX file that nothing else ever parses.
 *
 * The operator console, the portal and the landing app ship raw `.jsx` to the
 * browser and transpile it there (`<script type="text/babel">`). Nothing in the
 * build touches these files: they are not in the Expo bundle, tsc does not see
 * them, and eslint's project config does not cover them. A syntax error is
 * therefore invisible until a human opens the page and finds it blank — on the
 * operator console, that is the screen used to investigate incidents.
 *
 * This parses each file exactly as the browser's Babel would and fails on the
 * first error, so the mistake is caught in CI instead of in production.
 *
 * Parsing only. It deliberately does not lint, type-check or execute: the point
 * is the one class of failure that is both certain to break the page and
 * currently undetected.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, extname } from 'node:path';
import { parse } from '@babel/parser';

const ROOTS = [
  'backend/portal/static',
  'backend/console/static',
  'landing',
];

// Vendored libraries are shipped as-is and are not ours to fix; parsing them
// only produces noise about whatever dialect they were minified into.
const SKIP = /(^|\/)(node_modules|vendor|_ds\/vendor)(\/|$)/;

const files = [];
const walk = (dir) => {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return; // a root that doesn't exist yet is not a failure
  }
  for (const entry of entries) {
    const full = join(dir, entry);
    if (SKIP.test(full)) continue;
    if (statSync(full).isDirectory()) {
      walk(full);
    } else if (['.js', '.jsx'].includes(extname(full))) {
      files.push(full);
    }
  }
};
ROOTS.forEach(walk);

const failures = [];
for (const file of files) {
  try {
    parse(readFileSync(file, 'utf8'), {
      // These load as classic scripts, not modules — `sourceType: module` would
      // silently accept top-level `import`, which the browser would then reject.
      sourceType: 'script',
      errorRecovery: false,
      plugins: ['jsx'],
    });
  } catch (err) {
    failures.push({ file: relative(process.cwd(), file), message: err.message });
  }
}

if (failures.length) {
  console.error(`\n✖ ${failures.length} browser asset(s) failed to parse:\n`);
  for (const { file, message } of failures) console.error(`  ${file}\n    ${message}\n`);
  process.exit(1);
}

console.log(`✓ ${files.length} browser-served JS/JSX file(s) parse cleanly.`);
