#!/usr/bin/env node
/**
 * Fail on every production high/critical npm advisory except the two
 * image-size parser findings that are mitigated in metro.config.js.
 *
 * npm currently offers no fixed image-size release; `npm audit fix --force`
 * proposes downgrading Expo 57 to 53.  The vulnerable parsers are disabled at
 * Metro startup, none of those asset formats exist in this repository, and this
 * exception expires so an upstream fix cannot be forgotten.
 */
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const ALLOWED = new Set([
  'https://github.com/advisories/GHSA-w3rx-r6r6-pgpr',
  'https://github.com/advisories/GHSA-5p2g-fcmc-qvqq',
]);
const REVIEW_AFTER = new Date('2026-09-12T00:00:00Z');

function fail(message) {
  console.error(`ERROR: ${message}`);
  process.exit(1);
}

if (new Date() >= REVIEW_AFTER) {
  fail('the temporary image-size advisory mitigation expired; review Expo/Metro for a patched release');
}

const metroConfig = readFileSync(new URL('../metro.config.js', import.meta.url), 'utf8');
for (const type of ['icns', 'heif', 'jxl', 'jxl-stream']) {
  if (!metroConfig.includes(`'${type}'`)) {
    fail(`metro.config.js no longer disables the vulnerable ${type} parser`);
  }
}

const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const result = spawnSync(npmCommand, ['audit', '--omit=dev', '--json'], {
  encoding: 'utf8',
  maxBuffer: 16 * 1024 * 1024,
});

let report;
try {
  report = JSON.parse(result.stdout || '{}');
} catch {
  console.error(result.stdout || result.stderr);
  fail('npm audit endpoint returned an error or invalid JSON response body');
}
if (!report.auditReportVersion || !report.vulnerabilities) {
  console.error(result.stdout || result.stderr);
  fail('npm audit endpoint returned an error or an unsupported report');
}

const findings = [];
for (const item of Object.values(report.vulnerabilities)) {
  for (const via of item.via || []) {
    if (typeof via !== 'object') continue; // dependency propagation, not a root advisory
    if (!['high', 'critical'].includes(String(via.severity).toLowerCase())) continue;
    findings.push(via);
  }
}

const unexpected = findings.filter((finding) => !ALLOWED.has(finding.url));
if (unexpected.length) {
  for (const finding of unexpected) {
    console.error(`${finding.severity}: ${finding.title} (${finding.url})`);
  }
  fail(`${unexpected.length} unmitigated production high/critical npm advisories`);
}

const present = new Set(findings.map((finding) => finding.url));
for (const allowed of ALLOWED) {
  if (!present.has(allowed)) {
    console.log(`Resolved upstream: ${allowed}`);
  }
}

if (findings.length) {
  console.warn(
    `Accepted temporarily: ${findings.length} image-size build-parser advisories; ` +
    'affected formats are disabled in Metro and the exception expires 2026-09-12.'
  );
} else {
  console.log('No production high/critical npm advisories.');
}
