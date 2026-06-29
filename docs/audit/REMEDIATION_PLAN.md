# Zitch — Remediation Plan

Tracks the full `CLAUDECODE_ZITCH_REMEDIATION.md` program against what is **done**, **safe-to-do**, and **isolated/risky**. Agreed execution rule for this engagement: **design alignment first → security; apply safe changes directly, isolate risky ones (native modules, destructive `src/` refactor) on branches and document them — never touch the working build with unverifiable native changes.**

## Status legend
✅ done · 🟢 safe (do in place) · 🟡 isolate to branch · 🔵 backend/external · ⚪ optional

---

## Phase 0 — Design-handoff alignment ✅ (committed `2a6fdb7`)
Full alignment to `design_handoff_zitch_app`: `Tap` spring-press, top toast pill, receipt PNG/PDF export, splash coin-flip, KYC method-menu sub-flows, LinkedBankCard fund chips, plus all screen copy/structure fixes. `tsc` clean, jest 12/12. Audit trail in this folder + the original audit in chat.

## Phase 1 — Repository discovery ✅
`ARCHITECTURE_AUDIT.md` — module maps (client + Django apps), dependency graph, auth/transaction/state/API flows with Mermaid diagrams.

## Phase 2 — Security hardening — partially ✅, remainder 🟡/🔵
- ✅ Verified strong existing controls; `SECURITY_AUDIT.md` + `SECURITY_FIXES.md` produced; secrets/cleartext scan clean.
- 🟡 `security/native-hardening` branch: cert pinning (SEC-01), root/jailbreak (SEC-02), integrity attestation (SEC-03), debugger detection (SEC-08).
- 🔵 Token rotation/refresh (SEC-04), drop body-mirrored token (SEC-06).
- 🟢→CI Dependency triage via `npx expo install --fix` + Dependabot/Snyk (SEC-05).

## Phase 3 — Architecture refactor 🟡 (isolate; do NOT auto-merge)
Moving to `src/domains/...` is high-churn, low user-value, and risks breaking a working build. **Recommended lighter alternative (🟢):** introduce `lib/services/*` + `endpoints.ts` + response DTOs (observations A1, A3, A4) without relocating the tree. Full restructure only on a dedicated branch with a green build.

## Phase 4 — Performance 🟢 (incremental)
TanStack Query for cache/dedup/optimistic updates (A2); `FlashList` for history (A6); image caching; measure startup/render. Each is additive and branch-testable.

## Phase 5 — Testing 🟢
Expand Jest unit coverage (auth, wallet, transfers, format — format already covered); add Maestro E2E for sign-in + a transfer. Target >80% on `lib/`.

## Phase 6 — DevOps 🟢
Extend CI (`.github/`, `codemagic.yaml`): lint + `tsc` + jest gates, Dependabot, CodeQL, Semgrep, Snyk.

## Phase 7 — Observability 🟢/🟡
Sentry (`@sentry/react-native` — native module → branch-test), structured logging, crash reporting, perf monitoring.

## Phase 8 — Marketing site ⚪ (separate project)
There is an existing `landing/`. A Next.js SSR site (sitemap/robots/schema/OG) is its own deliverable; scope separately.

## Phase 9 — Fintech compliance 🔵 (backend-led)
PCI-DSS scope for `cards`, NDPA for BVN/NIN handling, immutable audit logs, server-side velocity/fraud limits, transaction signing. Belongs to `backend/`.

---

## Recommended next increments (all safe, in place)
1. **SEC-05 dependency triage** — `npx expo install --fix`, re-run `tsc`/jest, commit.
2. **A1/A3/A4** — `lib/services/` + `endpoints.ts` + typed DTOs (no tree move).
3. **Phase 5** — unit tests for `lib/session`, `lib/secureStore`, transfer validation.
4. **Phase 6** — CI quality gates + Dependabot.

## Items requiring your go-ahead before starting
- Create and work the `security/native-hardening` branch (Phase 2 🟡) — needs an EAS/Codemagic build + device test loop.
- Whether to attempt the Phase 3 full `src/` restructure (recommended: do the lighter A1/A3/A4 instead).
- Phase 8 marketing site as a separate project.
