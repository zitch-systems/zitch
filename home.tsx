# Ship the Bank-Linking Wallet to the live app

This bundle adds the **Mono "Connected accounts" bank-linking feature** to the Zitch
mobile app: connected external banks on the **Wallet** tab, a summary on **Home**,
fund-in (Mono direct debit) / fund-out (PIN payout) / refresh / unlink, and a
**Link a bank** screen.

> **These are production React Native / TypeScript files written for THIS codebase**
> (`zitch-systems/zitch`, Expo + expo-router). They are not HTML mocks — apply them
> directly. The feature is fully built on the frontend; it was simply never committed
> to the repo, which is why the deployed app still shows the old wallet.

- **Target repo:** `zitch-systems/zitch` (branch `main`)
- **Backend:** the `/api/banklink/*` endpoints + Mono webhook are **already built and
  live** (confirmed). This is a frontend-only apply.

---

## 1. File map — copy these into the repo at the same paths

Paths below are identical in this bundle and in the repo (the bundle mirrors the repo
tree). **NEW** = file does not exist in the repo yet. **CHANGED** = replace / merge.

| Bundle file | Repo path | Action |
|---|---|---|
| `components/design/banklink.tsx` | `components/design/banklink.tsx` | **NEW** — the whole feature (cards, sheets, `ConnectedAccounts`, `LinkedBanksSummary`) |
| `lib/mono.tsx` | `lib/mono.tsx` | **NEW** — Mono Connect provider/hook (`useMono`, `MonoLauncherProvider`) |
| `app/(servicesscreen)/linkbank.tsx` | `app/(servicesscreen)/linkbank.tsx` | **NEW** — the `/linkbank` screen |
| `lib/wallet.tsx` | `lib/wallet.tsx` | **CHANGED (replace)** — adds linked-accounts state, `reloadLinked()`, `LinkedAccount` type, `/api/banklink/list/` fetch |
| `app/(homepage)/wallet.tsx` | `app/(homepage)/wallet.tsx` | **CHANGED (replace)** — new "ZITCH WALLET" card + `<ConnectedAccounts/>` |
| `app/(homepage)/home.tsx` | `app/(homepage)/home.tsx` | **CHANGED (2-line patch)** — `import { LinkedBanksSummary }` + render `<LinkedBanksSummary/>` right after the balance `<Hero>` |
| `app/_layout.tsx` | `app/_layout.tsx` | **CHANGED (merge)** — `import { MonoLauncherProvider } from '@/lib/mono'` and wrap the tree: `<WalletProvider><MonoLauncherProvider>…</MonoLauncherProvider></WalletProvider>` |
| `app/(servicesscreen)/_layout.tsx` | `app/(servicesscreen)/_layout.tsx` | **CHANGED (merge)** — register a `<Stack.Screen name="linkbank" />` alongside the existing service screens |
| `components/design/ZIcon.tsx` | `components/design/ZIcon.tsx` | **CHANGED (merge)** — ensure these glyphs exist: `deposit`, `withdraw`, `link`, `unlink` (used by the bank cards/sheets). Add any that are missing; don't drop existing ones |
| `package.json` | `package.json` | **CHANGED (merge)** — add dependency `"@mono.co/connect-react-native"` |
| `.env.example` | `.env.example` | **CHANGED (merge)** — add `EXPO_PUBLIC_MONO_PUBLIC_KEY=` |

**No change needed to `lib/api.ts`** — the repo's version already exports `apiPost`,
`apiJson`, and `newIdempotencyKey`, which the new code uses.

> For `home.tsx`, `app/_layout.tsx`, `(servicesscreen)/_layout.tsx`, `ZIcon.tsx`,
> `package.json`, `.env.example` — **merge, don't blind-overwrite**, in case the repo
> has diverged. The other files are safe full replacements / new files.

---

## 2. Install + configure

```bash
# from the repo root
npx expo install @mono.co/connect-react-native
```

- `@mono.co/connect-react-native` is a **native module** — it does **not** run in
  **Expo Go**. You need a custom **dev build** or an **EAS build**. Add the SDK's
  config plugin to `app.json` / `app.config.js` if its docs require one.
- `lib/mono.tsx` is **crash-safe**: when the native module isn't present (Expo Go, or
  before the dev build is cut) it falls back to a *simulated* "Connecting…" sheet that
  returns an obviously-fake `MONO-SIM-…` code the backend rejects — so the UI is
  testable everywhere but real linking only works in a build that includes the SDK.

Set the Mono **public** key (safe to expose — public key only):

```bash
# .env  (local)            and  EAS env / secrets (builds)
EXPO_PUBLIC_MONO_PUBLIC_KEY=pk_live_xxx   # from https://app.mono.co
```

---

## 3. Backend contract (already built — for verification only)

The frontend calls these via `apiJson(path, body)` (authenticated POST, JSON in/out).
Confirm field names match your endpoints:

- `POST /api/banklink/list/` → `{ accounts: [{ id, bank_name, account_number, account_name, balance: number|null, balance_updated, status: 'active'|'reauth', mono_account_id }] }` — `balance: null` ⇒ link needs re-auth (UI shows "Reconnect").
- `POST /api/banklink/connect/` body `{ code }` → `{ success, message? }` — exchange the Mono auth code (new link **and** re-auth).
- `POST /api/banklink/fund/` body `{ linked_id, amount, idempotency_key }` → `{ success, authorization_url?, message? }` — money **in** (Mono direct debit). Wallet is credited by **webhook** after the user approves at `authorization_url`.
- `POST /api/banklink/payout/` body `{ linked_id, amount, pin, idempotency_key }` → `{ success, message? }` — money **out** (wallet debit → bank, PIN-verified).
- `POST /api/banklink/refresh/` body `{ linked_id }` → `{ success, message? }` — re-sync one bank's balance.
- `POST /api/banklink/unlink/` body `{ linked_id }` → `{ success, message? }`.

Money-moving calls send an `idempotency_key` — dedupe on it server-side so a
double-tap/retry never debits twice.

---

## 4. Smoke test, then ship

1. Run the dev/EAS build on a device, sign in.
2. **Wallet** tab → "Connected accounts" → **Connect a bank** → complete a real Mono
   link → the bank card shows a balance.
3. **Fund Zitch** (approve the Mono debit) and **Fund {bank}** (enter PIN) move money
   both ways; **Home** shows the "Connected banks" summary total.
4. `eas build` (iOS/Android) → submit / OTA per your release process.

---

## Ready-to-paste prompt for Claude Code

Open this folder + your `zitch-systems/zitch` checkout in Claude Code and paste:

> Apply the bank-linking feature in `_banklink_handoff/` to this repo, following
> `_banklink_handoff/APPLY_TO_REPO.md`. Copy the **NEW** files as-is; for the
> **CHANGED** files, make the minimal merge described in the file map (don't drop
> existing repo code). Then run `npx expo install @mono.co/connect-react-native`, add
> `EXPO_PUBLIC_MONO_PUBLIC_KEY` to `.env.example`, and add a `linkbank` Stack.Screen.
> The `/api/banklink/*` backend is already live, so don't stub it. Typecheck, then open
> a PR titled "Wallet: Mono bank-linking (Connected accounts)" summarizing the changes.
