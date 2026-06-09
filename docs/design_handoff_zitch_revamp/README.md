# Handoff: Zitch — App Revamp (Mobile Fintech)

> **Bundle update — June 2026.** This package now covers **three surfaces**, all sharing the Zitch brand (teal `#23B1A8` / deep teal `#00847B` / cyan `#5CF5EB` / navy `#02344A`, ribbon‑Z logo in `assets/brand/`):
>
> 1. **Mobile app prototype** — `Zitch Prototype.html` + `app/` + `shared.jsx` (documented below). Now supports a chromeless **embed mode** (`?embed=1` boots straight to Home, no device chrome, scales to fill its window) used by the landing page.
> 2. **Marketing landing page** — `Zitch Landing v3.html` (static HTML/CSS/JS, Space Grotesk + Inter via Google Fonts). Features: **light/dark theme** (nav toggle, persists in `localStorage` as `zland-theme`, syncs the embedded app via `z-theme`), **fully responsive** (desktop → tablet ≤1020px with hamburger menu → fold ≤760px → mobile ≤480px → fold-cover ≤340px; the app iframe sizes by `aspect-ratio: 414/868`), 4K‑ready zoom scaling (`html{zoom}` at 1680/2240/3300px), **two live hero demos in twin phone bezels** — the real app embedded via `?embed=1` iframe and an **interactive WhatsApp banking demo** (deterministic message router: send money / airtime / balance / FX convert, reply buttons for Fingerprint / Face ID / PIN, masked PIN, in‑chat receipt cards), App Store / Google Play badges, scroll reveals gated on `prefers-reduced-motion` with a force‑reveal fallback, and layered background patterns (dot grids, cyan crosshatch on navy, ribbon watermarks). Target: Next.js or any web stack; the WhatsApp demo logic mirrors `backend/whatsapp/router.py` flows in the `zitch-systems/zitch` repo.
> 3. **Admin / operator portal** — `Zitch Admin Portal.html` + `admin/` (React 18 + Babel‑in‑browser, Inter + Space Grotesk). **Twelve views** grounded in `backend/BUILD.md` of the repo: Overview, Users & KYC, **KYC queue** (manual reviews, tier bumps), Transactions (requery/refund/flag release), FX & Treasury (margin bps editor, corridor toggles, CNY quote‑only lock, float balances), **Products** (loan book approve/disburse/remind, Fixed Save maturities + idempotent sweep, virtual card freeze/unfreeze), WhatsApp inbox (conversation monitor, human handover / return‑to‑bot, agent replies, parsed AI intents), Broadcasts (opt‑in aware), AI controls (global kill switch), **Providers & recon** (HMAC‑verified webhook log, nightly reconciliation runs), append‑only Audit log, Settings & team with a full **RBAC matrix** (super_admin / finance / support / read_only — the role switcher in the topbar enforces gating). Mock data in `admin/data.js` mirrors the Django models (`WhatsAppLink`, `ConversationState`, `WaMessageLog`, `Broadcast`, `AuditLog`, `SystemSetting`, plus loans/savings/cards/webhooks).
>
> Production stats used across copy: **5,000+ users, ₦1bn+ processed**. Currency corridors: NGN→USD/GBP/CAD settle, NGN→CNY quotes only.

## Overview
Zitch is a Nigerian fintech / utility‑payments + wallet app. This package documents a full revamp covering onboarding & biometric auth, a home dashboard, five primary tabs (Home, Wallet, Loans, Cards, Me), and a complete set of service flows (Airtime, Data, Cable TV, Betting, Exams/JAMB‑WAEC, Electricity, Send money/Transfer, Get Loan, Fixed Save, Add money). It supports light **and** dark themes and three device classes: **phone, foldable (unfolded), and tablet**.

The revamp adopts proven fintech UX patterns (quick‑amount chips, saved beneficiaries, bottom‑sheet confirmation, PIN/biometric authorization, animated success receipts, smart paste‑to‑pay) while keeping Zitch's own brand (teal + cyan ribbon‑Z logo, Manrope type).

## About the Design Files
The files in this bundle are **design references built in HTML/React (Babel‑in‑browser)** — interactive prototypes that show the intended look, layout, and behavior. **They are not production code to copy directly.**

The target codebase is an **Expo (React Native) app using `expo-router` and NativeWind** (see the existing `zitch-systems/zitch` repo: Expo SDK 51, expo-router v3, nativewind v2, `@expo/vector-icons`). The task is to **recreate these designs in that environment** using its established patterns — React Native primitives (`View`, `Text`, `Pressable`, `TextInput`, `FlatList`), NativeWind classes, `expo-router` file‑based routing, `react-native-reanimated` for animation, `expo-local-authentication` for biometrics, and a real bottom‑sheet lib (e.g. `@gorhom/bottom-sheet`). Do **not** ship the HTML.

## Fidelity
**High‑fidelity.** Colors, typography, spacing, radii, shadows, and interactions are final. Recreate pixel‑faithfully using the exact tokens in **Design Tokens** below. The HTML prototype is the source of truth for layout and behavior; `assets/tokens.css` holds the canonical token values.

---

## Design Tokens

### Brand
| Token | Hex | Use |
| --- | --- | --- |
| Teal 400 | `#23B1A8` | brand (figma primary) |
| Teal 500 | `#0FA295` | **primary brand** (buttons, active, icons) |
| Teal 600 | `#00847B` | deep brand (pressed, gradients) |
| Teal 800 | `#0C5249` | hero gradient start |
| Cyan | `#5CF5EB` | electric accent (logo, highlights, toast check) |
| Ink | `#04201C` | near‑black green‑tinted |
| Navy | `#02344A` | logo badge background |
| Lime (success) | `#00B51D` | success states, positive amounts |
| Amber (warning) | `#F5A623` | badges (e.g. "6% off"), tier pill |
| Red (danger) | `#FF3B3B` | errors, "Hot" badge, negative emphasis |

**Hero gradient (light):** `linear-gradient(135deg, #0C5249 0%, #00847B 52%, #0FA295 100%)`
**Hero gradient (dark):** `linear-gradient(135deg, #073A34 0%, #00847B 60%, #12B7AA 100%)`

### Semantic theme variables
Define these per theme (light/dark). Values from `assets/tokens.css`:

**Light:** `--bg #EFF7F5` · bg‑grad `radial-gradient(120% 60% at 50% -8%, #DDF3EF, #EFF7F5 46%, #F5FAF9)` · `--surface #FFFFFF` · `--surface-2 #F4F9F8` · `--surface-3 #EAF3F1` · `--line #E2EEEB` · `--ink-1 #06231F` · `--ink-2 #3D5B56` · `--ink-3 #6E8B86` · brand `#0FA295` · brand‑deep `#00847B`.

**Dark:** `--bg #05201C` · bg‑grad `radial-gradient(120% 60% at 50% -10%, #0A3A33, #06251F 48%, #041714)` · `--surface #0B2A24` · `--surface-2 #0F332C` · `--surface-3 #143C34` · `--line #1B463C` · `--ink-1 #EAFBF7` · `--ink-2 #A6C9C1` · `--ink-3 #6F9189` · brand `#23B1A8` · brand‑deep `#0FA295`.

### Network / biller brand colors
MTN `#FFCC00` · Airtel `#E40000` · Glo `#2BB24C` · 9mobile `#0A8A3D` · DSTV `#0A66C2` · GOtv `#92C020` · StarTimes `#F47B20`. (Real logos provided in `assets/logos/`.)

### Typography
- **Family:** `Manrope` (weights 400/500/600/700/800). Money/numerals use tabular figures (`font-variant-numeric: tabular-nums; letter-spacing:-.02em`).
- **Scale:** page title (tab roots) **26/800**; screen header title 18/700; greeting 18/800; section label 17/700; balance numerals 30–34/800; list‑row title 15.5/600, subtitle 13/ink‑3; body 14; small/eyebrow 12–12.5; nav label 11.

### Spacing / radius / shadow
- Spacing grid: 4 / 8 / 12 / 14 / 16 / 18 / 20 / 24px.
- Radius: inputs 13–14 · cards 18–22 · sheets 28 (top) · pills/avatars 999 · service icon tiles 16 · hero 22.
- Card shadow (light): `0 1px 2px rgba(6,35,31,.04), 0 8px 24px -12px rgba(6,55,49,.18)`. Pop/sheet: `0 12px 40px -12px rgba(4,55,49,.30)`.
- Motion: 150ms color, 200ms transform, ~270–300ms sheet; spring `cubic-bezier(0.34,1.56,0.64,1)`; honor reduced‑motion.

> **Animation caveat:** the prototype avoids CSS keyframe entrance animations that start at `opacity:0` (the preview froze them). In RN use `react-native-reanimated` entering animations — this caveat does not apply.

---

## Iconography
Line icons, 1.75 stroke, 22px default (12/16/20/22 sizes), inherit color. Map to your icon set (`@expo/vector-icons` Feather/Lucide). Names used in the prototype: bell, eye/eyeoff, scan, qr, deposit, withdraw, send, smartphone(airtime), wifi(data), zap(bills), hand‑coins(loan), clapperboard(movie), shield(insurance), file‑text(remita), graduation(jamb/exams), fixed(lock‑box/save), repeat(convert), grid(more), search, home, wallet, bar‑chart(loans), credit‑card(cards), user, plus, chevrons, ticket/dice(betting), tv, bank, check, spark, gift, share, download, copy, history, settings, lock, fingerprint, faceid, help(headset), x.

---

## Screens / Views

### Auth & launch
- **Splash** — deep‑teal gradient, circular badge logo + "ZITCH" wordmark + "Pay. Send. Grow." Auto‑advances after ~1.8s. **Routing:** if `onboarded` flag set → **Lock**; else → **Onboarding**.
- **Onboarding** (first‑time only) — 3 swipe slides (icon tile + title + body), dot indicator, "Skip" + "Next/Get Started", and "Already have an account? Sign in". "Get Started" → **Register** (sign up). The intro never appears again after onboarding.
- **Sign in** — logo, "Welcome back", a prominent **Instant sign‑in** card (Face ID/fingerprint), "or use password" divider, phone + password fields, "Sign in", "Create account" link.
- **Register** — full name, phone, email (optional), Terms note, Continue → OTP.
- **OTP** — 5‑box code, custom numeric keypad, resend timer; auto‑verify on 5 digits → Set PIN.
- **Set PIN** — create 4‑digit PIN then confirm (mismatch shakes/clears), keypad → Biometric.
- **Biometric (enrol)** — "Enable biometrics" triggers a **scan prompt** (enrollment) → on success enables + enters app; "Maybe later" skips.
- **Lock** (returning user) — "Welcome back, William", badge logo, **auto‑opens the biometric scan immediately**; "Use PIN instead" → Sign in.

### Tabs
- **Home** — header (avatar, "Hi, William", help/scan/bell‑with‑badge); **balance hero** (teal gradient: ✓ Available Balance · "Transaction History ›"; ₦ amount + eye toggle; "Acct: 9012 345 678" + copy; white "+ Add Money" pill; ribbon watermark); daily‑interest strip; **quick actions** Transfer/Airtime/Withdraw (soft‑teal circles); **service grid** 4×2 (Airtime[6% off], Data, Betting, Cable TV, Save, Loan[Hot], Exams, More) with badges; Fixed‑Save promo; **Recent activity** list.
- **Wallet** — title, balance card with Add money/Send, Money‑in/Money‑out stat cards, full transaction list (tap → detail).
- **Loans** — available‑credit hero with usage bar, "Get a new loan", active‑loan card with "Repay now".
- **Cards** — virtual card (freeze toggle dims/labels "FROZEN"), Freeze/Details/Settings actions, "Create a virtual card" row.
- **Me** — header (avatar, Tier 3 pill, settings gear), balance, "5 Safety Tips" banner, two grouped row lists (Transaction History, Account Limits, Bank Card/Account, My BizPayment, Zitch Junior[New], Buy Now Pay Later[Enjoy ₦0]; Security Center, Customer Service, Invitation, Zitch USSD), **Face ID/Fingerprint toggle** (enabling prompts enrollment scan), **Dark mode toggle**, Log out.

### Service flows (shared engine)
All purchase flows follow: **form → "Continue" → Confirm sheet → Biometric (or PIN fallback) → animated Success receipt → Back to Dashboard**. Each form has a balance hint under the amount; **Continue is disabled when amount > balance** with an inline "Insufficient balance · + Add money" link.
- **Airtime & Data** — segmented Airtime/Data; network grid (real logos, selected ring); phone field prefilled with user's number (editable, "Me" reset); Airtime = quick‑amount chips + amount; Data = plan list.
- **Cable TV** — provider grid (DSTV/GOtv/StarTimes/Showmax); smartcard/IUC field (resolves a name chip); bouquet list.
- **Electricity** — disco grid (3‑col); Prepaid/Postpaid segmented; meter field; amount chips; success returns a token.
- **Betting** — platform grid; user‑ID field; amount.
- **Exams** — exam list (WAEC/NECO/JAMB/NABTEB) with prices; quantity stepper; phone for PIN delivery.
- **Transfer (Send money)** — bank transfer flow (no destination toggle); **searchable saved beneficiaries** (avatar rail, tap to select / Change to clear); **account number entered first → bank auto‑detected** from the leading digit, with a manual bank picker via bottom sheet; resolved‑name confirmation pill; quick‑amount chips + amount field + balance hint; optional narration. On success, a new beneficiary is auto‑saved. sheet if needed) → resolved name chip; amount + balance hint; narration. **Every transfer to a new recipient auto‑saves as a beneficiary.**
- **Get Loan** — eligibility hero; amount slider; tenure (15/30/60 days); live interest + total repayment summary → confirm → success "Loan disbursed" (credits wallet).
- **Fixed Save** — earnings hero; amount + chips; lock period 30/90/180/365 days with tiered rates (12/15/18/22% p.a); live "You get back" → success "Savings locked".
- **Add money** — Bank Transfer card (big Zitch account number + Copy Number/Share Details), "OR", method rows (Cash Deposit, Top‑up with Card/Account → amount sheet, Bank USSD, Scan my QR).

### Utility screens
- **Transaction History** — **working filter chips** (All / Money in / Money out / Airtime / Bills / Transfers) backed by component state; the list filters live (Money in = `amt > 0`, Money out = `amt < 0`, Airtime = `cat ∈ {airtime,data}`, Bills = `cat ∈ {tv,electricity,betting,exams}`, Transfers = `cat ∈ {transfer,fund}`), with an empty‑state message per filter. Rows tap → detail.
- **Transaction detail** — monogram, amount, status pill, ref/date/channel rows, Share receipt.
- **Notifications** — list with colored icon, title, subtitle.
- **Coming‑soon** — generic placeholder (icon, title, note) for Insurance/Remita/Movie/Convert/Invite/Support etc.

### Confirm sheet (shared, OPay‑aligned)
Bottom sheet: ✕ close · small label · **large amount** · detail rows (key/value, consistent type) · **"Pay with"** block showing the **sender** (Zitch Wallet · William A. · 9012 345 678) + Available balance · "Pay ₦…" button.

### Smart paste‑to‑pay
On app open, read clipboard; if a 10‑digit (account) or 11‑digit (phone) number is found, show a sheet: "Number detected" → **Send money** / **Buy airtime** (phone only) / Not now. Routes into the matching flow prefilled. **When sending money to a detected phone, strip the leading `0`** (e.g. `08166938327` → `8166938327`).

---

## Interactions & Behavior
- **Navigation:** stack push/pop per flow; tabs reset the stack. Pushed flow screens slide in. Bottom nav on phone; **left sidebar** on fold/tablet (logo, nav items with active highlight, profile footer).
- **Bottom sheets:** confirm, PIN, biometric, option pickers, smart‑paste, more‑services, add‑money card top‑up. Backdrop dim + spring slide‑up; tap backdrop or ✕ to close.
- **PIN pad:** 4 dots fill on entry; auto‑submits at 4; bottom‑left key is a fingerprint shortcut to switch to biometric.
- **Biometric scan:** animated ring + fingerprint/faceid icon; auto‑authenticates after ~1.5s or on tap → green ✓ "Approved"; "Use PIN instead" fallback. Used for sign‑in, payment authorization, and enrollment.
- **Success receipt:** spring‑in green check, receipt card, Save/Share/Copy ref, "Back to Dashboard".
- **Toasts:** top pill (icon + message), auto‑dismiss ~2.3s (copy, share, beneficiary saved, biometrics on/off, errors).
- **Balance guard:** spend flows disable Continue and warn when amount > balance.
- **Theme:** light/dark toggle (Me + dev control), persisted.
- **Responsive:** phone (bottom nav) / fold & tablet (sidebar + centered content column, flows max‑width ~564).

## State Management
Global (app‑level context / store): `theme`, `balance`, `transactions[]`, `beneficiaries[]` (grows on transfer), `showBalance`, `biometricsEnabled`, `device` (phone/fold/tablet), `toast`, `detectedClipboardNumber`, `onboarded` (persisted). Actions: `pay(amount, txn)` (debit + prepend txn), `fund(amount)`, `addTxn`, `addBeneficiary` (dedupe by account), `showToast`, `setTheme`, `setBiometrics`. Per‑flow local state: selected provider/plan, identifier fields, amount, sheet step (`confirm` | `bio` | `pin`). In production replace mocked resolution (account‑name lookup, OTP, PIN) with real services (Paystack, ATS billers, `expo-local-authentication`).

## Assets
- **Logo:** `assets/brand/zitch-mark.png` (transparent ribbon Z, 549×743 — inline mark) and `assets/brand/zitch-badge.png` (navy circle badge, 1254×1254 — app icon / splash). User‑provided.
- **Network/biller logos:** `assets/logos/{mtn,airtel,glo,9mobile,dstv,gotv,startimes}.{jpg,svg,png}` — user‑provided. Showmax falls back to a colored monogram.
- **Fonts:** Manrope (Google Fonts) — bundle via `expo-font`.
- Monograms (2‑letter on tinted square) stand in for bank/contact avatars.

## Files (design references in this bundle)
- `Zitch Prototype.html` — entry; loads React + Babel + the modules below; device frame, router, theme, toast, smart‑paste.
- `assets/tokens.css` — **canonical design tokens** (colors, type, radii, shadows, light/dark vars).
- `shared.jsx` — icon set (inline SVG), `ZMark` logo (image‑based), `StatusBar`, `HomeBar`, `Avatar`, `ZWordmark`.
- `app/data.js` — mock data (networks, data/cable plans, discos, betting, exams, banks, beneficiaries, quick amounts, transactions).
- `app/ui.jsx` — reusable kit: `AppHeader`, `PrimaryButton`, `BottomBar`, `Field`, `Segmented`, `QuickAmounts`, `ProviderGrid`, `PlanList`, `ListRow`, `Toggle`, `Monogram`, `Sheet`, `ConfirmSheet`, `PinSheet`, `BiometricScan`, `OptionSheet`, `SuccessReceipt`, `Row2`, `BalanceHint`, app context.
- `app/flows.jsx` — `Screen` layout + all service flows + checkout sheet wiring.
- `app/tabs.jsx` — Home, Wallet, Loans, Cards, Me, History, TxnDetail, Notifications, ComingSoon, BottomNav, SmartPaste.
- `app/auth.jsx` — Splash, Onboarding, SignIn, Register, Otp, SetPin, Biometric, Lock.
- `app/App.jsx` — root: state, stack router, device switcher (phone/fold/tablet), sidebar, toast, smart‑paste detection.

## Suggested expo-router structure
```
app/
  _layout.tsx                 # providers (theme, store), font load, splash gate
  index.tsx                   # splash → redirect to (auth) or (tabs) by `onboarded`
  (auth)/_layout.tsx
  (auth)/onboarding.tsx  signin.tsx  register.tsx  otp.tsx  set-pin.tsx  biometric.tsx  lock.tsx
  (tabs)/_layout.tsx          # Tabs on phone; Drawer/sidebar on fold+tablet (useDeviceClass)
  (tabs)/index.tsx (home)  wallet.tsx  loans.tsx  cards.tsx  me.tsx
  pay/airtime.tsx  cable.tsx  electricity.tsx  betting.tsx  exams.tsx  transfer.tsx  loan.tsx  fixed-save.tsx  add-money.tsx
  history.tsx  txn/[id].tsx  notifications.tsx
components/                   # PrimaryButton, Field, Segmented, QuickAmounts, ProviderGrid, PlanList, ListRow, Toggle, Monogram, BalanceHint, AppHeader
components/sheets/            # ConfirmSheet, PinSheet, BiometricScan, OptionSheet, SmartPaste  (@gorhom/bottom-sheet)
components/SuccessReceipt.tsx
lib/store.ts                  # global state (zustand or context)
theme/tokens.ts               # tokens from assets/tokens.css; wire into tailwind.config + dark mode
```
Map tokens into `tailwind.config.js` (extend colors/spacing/borderRadius) and drive light/dark via NativeWind. Use `expo-local-authentication` for the biometric scans, `react-native-reanimated` for entrances/success, and `expo-clipboard` for smart‑paste.
