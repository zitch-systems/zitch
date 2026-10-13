#!/usr/bin/env node
/**
 * Report every production high/critical advisory without exceptions. Locally
 * patched packages retain truthful versions, so this command intentionally
 * remains nonzero while upstream advisories remain. Behavioral mitigations are
 * verified separately by test:build-patches; they do not suppress audit output.
 * Exit 1: an advisory. Exit 75: the advisory service could not be checked.
 */
import { spawnSync } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

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

if (findings.length) {
  for (const finding of findings) {
    console.error(`${finding.severity}: ${finding.title} (${finding.url})`);
  }
  fail(`${findings.length} production high/critical npm advisory records; see the separate mitigation evidence`);
}
console.log('No production high/critical npm advisories.');
