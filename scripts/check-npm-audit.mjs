#!/usr/bin/env node
/**
 * Fail on every production high/critical npm advisory except the two
 * image-size parser findings that are mitigated in metro.config.js.
 *
 * npm currently offers no fixed image-size release; `npm audit fix --force`
 * proposes downgrading Expo 57 to 53.  The vulnerable parsers are disabled at
 * Metro startup, none of those asset formats exist in this repository, and this
 * exception expires so an upstream fix cannot be forgotten.
 *
 * EXIT CODES.  The caller has to be able to tell "your dependencies are
 * vulnerable" from "we never got an answer out of npm", because those want
 * different reactions from whoever is looking at the red build:
 *
 *   0   clean, or only the mitigated exceptions
 *   1   a real finding — unmitigated advisory, expired exception, or the Metro
 *       mitigation that the exception depends on has been removed
 *   75  npm's advisory service did not return a usable report (EX_TEMPFAIL)
 *
 * 75 still fails the build.  This is a bank's dependency gate: not knowing
 * whether we shipped a critical CVE is not a pass.  The distinction exists so
 * the failure names itself instead of looking like a vulnerability.
 */
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { setTimeout as sleep } from 'node:timers/promises';

const require = createRequire(import.meta.url);

const ALLOWED = new Set([
  'https://github.com/advisories/GHSA-w3rx-r6r6-pgpr',
  'https://github.com/advisories/GHSA-5p2g-fcmc-qvqq',
]);
// Pushed out from 2026-09-12. Every published image-size is still in the
// advisory range (`latest` is 2.0.2; the advisories cover <=2.0.2), so the old
// date was going to fire into a wall — there was nothing to upgrade TO, and the
// only available action would have been to move the date anyway. What changed
// is that the mitigation is now PROVEN on every run rather than asserted (see
// assertVulnerableParsersDisabled below), so this date is a prompt to re-check
// upstream, not the only thing standing between us and an unreviewed CVE.
const REVIEW_AFTER = new Date('2026-12-12T00:00:00Z');

// The four parsers the two allowed advisories cover.
const VULNERABLE_TYPES = ['icns', 'heif', 'jxl', 'jxl-stream'];

// npm's advisory service goes down for minutes at a time, not seconds. The old
// window was three tries five seconds apart — about ten seconds of tolerance,
// which is shorter than most single outages and turned every one of them into a
// red build on a branch whose dependencies had not changed.
const ATTEMPTS = Number(process.env.NPM_AUDIT_ATTEMPTS || 6);
const FIRST_BACKOFF_MS = Number(process.env.NPM_AUDIT_BACKOFF_MS || 10_000);
// Doubling from 10s over six attempts spends ~5m10s before giving up.
const CALL_TIMEOUT_MS = Number(process.env.NPM_AUDIT_TIMEOUT_MS || 120_000);

const EXIT_REAL_FAILURE = 1;
const EXIT_SERVICE_UNAVAILABLE = 75;

function fail(message) {
  console.error(`ERROR: ${message}`);
  process.exit(EXIT_REAL_FAILURE);
}

if (new Date() >= REVIEW_AFTER) {
  const installed = (() => {
    try { return require('image-size/package.json').version; } catch { return 'unknown'; }
  })();
  console.error(`Installed image-size: ${installed}. Advisories cover <=2.0.2.`);
  console.error('Check `npm view image-size versions` for a release outside that range.');
  console.error('If there still is not one, extend REVIEW_AFTER — the Metro mitigation below');
  console.error('is verified on every run, so the exception is not resting on this date alone.');
  fail('the image-size advisory exception is due for review');
}

/**
 * Prove the mitigation the exception depends on is actually in force.
 *
 * This used to grep metro.config.js for the four type names. That check passes
 * on a file that merely MENTIONS them — it would have stayed green if
 * disableTypes were deleted, renamed, called with the wrong argument, or if a
 * future image-size stopped honouring it. For a pair of accepted high-severity
 * advisories, "the source contains these four strings" is not evidence.
 *
 * So load the real metro.config.js — the same module Metro loads, which is what
 * calls disableTypes — and then ask the library directly whether each parser is
 * refused. Costs ~10s (getDefaultConfig is not cheap) and buys an assertion
 * about behaviour instead of about text.
 *
 * THE PROBES ARE DELIBERATELY WELL-FORMED, not the malicious inputs from the
 * advisories. A malformed ICNS is precisely the thing that loops forever, so a
 * guard built on one would HANG when the mitigation was missing — the single
 * worst way for a safety check to report a problem. Each probe below is instead
 * the smallest input whose validate() claims it for that parser and whose
 * calculate() terminates, so the run ends either way and the outcome is
 * legible: "disabled file type: x" when the mitigation holds, anything else
 * (a size, a parser error) when it does not.
 */
function probeFor(type) {
  const b = Buffer.alloc(64);
  switch (type) {
    case 'icns':
      // One entry whose length lands imageOffset exactly on fileLength, so
      // calculate() returns rather than looping.
      b.write('icns', 0, 'ascii'); b.writeUInt32BE(16, 4);
      b.write('ic09', 8, 'ascii'); b.writeUInt32BE(8, 12);
      return b;
    case 'heif':
      b.write('ftypmif1', 4, 'ascii');
      return b;
    case 'jxl':
      // Container form: signature box, then an ftyp box branded 'jxl '.
      b.writeUInt32BE(12, 0); b.write('JXL ', 4, 'ascii'); b.writeUInt32BE(0x0d0a870a, 8);
      b.writeUInt32BE(20, 12); b.write('ftyp', 16, 'ascii'); b.write('jxl ', 20, 'ascii');
      return b;
    case 'jxl-stream':
      b[0] = 0xff; b[1] = 0x0a;
      return b;
    default:
      throw new Error(`no probe defined for ${type}`);
  }
}

function assertVulnerableParsersDisabled() {
  try {
    require('../metro.config.js');
  } catch (error) {
    fail(`could not load metro.config.js to verify the image-size mitigation: ${error.message}`);
  }

  let imageSize;
  try {
    ({ imageSize } = require('image-size'));
  } catch (error) {
    // No image-size in the tree means no exposure and nothing to mitigate; the
    // advisory check below will also stop finding the allowed advisories.
    console.log(`image-size is not installed (${error.code || 'not resolvable'}); mitigation check skipped.`);
    return;
  }

  for (const type of VULNERABLE_TYPES) {
    let outcome;
    try {
      outcome = `parsed it and returned ${JSON.stringify(imageSize(probeFor(type)))}`;
    } catch (error) {
      if (error.message === `disabled file type: ${type}`) continue;
      outcome = `reached the parser and threw "${error.message}"`;
    }
    fail(
      `the ${type} parser is NOT disabled — image-size ${outcome}. ` +
      'metro.config.js must call disableTypes for it, or the advisory exception in ' +
      'this script is no longer justified and should be removed.'
    );
  }
  console.log(`Mitigation verified: ${VULNERABLE_TYPES.join(', ')} parsers are refused.`);
}

assertVulnerableParsersDisabled();

/**
 * One attempt at getting an audit report out of npm.
 *
 * Returns the parsed report, or a string explaining why there isn't one. Every
 * "why" here is retryable by construction: a real report always parses and
 * always carries auditReportVersion, so no way of *having* a vulnerability can
 * land in this branch. That is what makes retrying safe — it can only ever be
 * retrying the absence of an answer, never the presence of a bad one.
 *
 * Note that a non-zero exit status is NOT one of the reasons: `npm audit` exits
 * non-zero precisely when it finds something, and finding something is the
 * report we want. Severity filtering is ours to do, below.
 */
function fetchAuditReport() {
  const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  const result = spawnSync(npmCommand, ['audit', '--omit=dev', '--json'], {
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
    timeout: CALL_TIMEOUT_MS,
  });

  if (result.error) {
    return { reason: `npm audit could not be run: ${result.error.message}` };
  }
  // A killed process means our own timeout fired: npm was still waiting on a
  // socket. Without this the run would sit there for the job's whole limit.
  if (result.signal) {
    return { reason: `npm audit timed out after ${CALL_TIMEOUT_MS}ms (signal ${result.signal})` };
  }

  let report;
  try {
    report = JSON.parse(result.stdout || '');
  } catch {
    return {
      reason: 'npm audit returned an invalid JSON response body',
      output: result.stdout || result.stderr,
    };
  }
  // npm reports registry-side failures in-band, as a JSON error envelope with a
  // 200-shaped body, so this is the usual face of an outage rather than a rare one.
  //
  // Where the explanation lands depends on which layer failed. A registry-side
  // rejection fills error.summary/detail; a transport failure leaves those two
  // empty strings and puts the whole story in the top-level `message` ("request
  // to … failed, reason: connect ECONNREFUSED …"). Reading only the former is
  // how you end up logging "unknown: no detail" for the outage you most want
  // described, so take whichever one actually has text.
  if (report && (report.error || report.message)) {
    const { code, summary, detail } = report.error || {};
    const explanation = [summary, detail, report.message].map((part) => String(part || '').trim()).find(Boolean);
    const label = [code, explanation].filter(Boolean).join(': ');
    return { reason: `npm audit endpoint returned an error (${label || 'no detail given'})` };
  }
  if (!report || !report.auditReportVersion || !report.vulnerabilities) {
    return {
      reason: 'npm audit endpoint returned an unsupported report',
      output: result.stdout || result.stderr,
    };
  }
  return { report };
}

let report;
let lastReason = 'unknown';
for (let attempt = 1; attempt <= ATTEMPTS; attempt += 1) {
  const outcome = fetchAuditReport();
  if (outcome.report) {
    report = outcome.report;
    if (attempt > 1) console.log(`npm audit succeeded on attempt ${attempt}.`);
    break;
  }
  lastReason = outcome.reason;
  if (outcome.output) console.error(outcome.output);
  console.error(`npm audit attempt ${attempt}/${ATTEMPTS} failed: ${outcome.reason}`);
  if (attempt < ATTEMPTS) {
    const waitMs = FIRST_BACKOFF_MS * 2 ** (attempt - 1);
    console.error(`Retrying in ${Math.round(waitMs / 1000)}s…`);
    await sleep(waitMs);
  }
}

if (!report) {
  console.error(`ERROR: no usable npm audit report after ${ATTEMPTS} attempts (${lastReason}).`);
  console.error('Failing closed: an unknown dependency-vulnerability state is not a pass.');
  process.exit(EXIT_SERVICE_UNAVAILABLE);
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
    'affected formats are verified disabled in Metro (above) and this exception is ' +
    `due for review on ${REVIEW_AFTER.toISOString().slice(0, 10)}.`
  );
} else {
  console.log('No production high/critical npm advisories.');
}
