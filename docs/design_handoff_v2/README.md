# Handoff: Zitch — Mobile Banking App (complete)

## Overview
**Zitch** is a Nigerian fintech / mobile-banking app (OPay/Kuda-class): wallet, bank transfers, bill payments (airtime, data, cable, electricity, betting, exam pins), cards, savings & loans, **WhatsApp banking**, and identity verification (KYC). This package documents the **entire app** as designed in the prototype — every screen, component, token, and interaction — so a developer can rebuild it in a production codebase.

The prototype is a single device-framed mobile app (414 × 868 iPhone-class frame, with fold/tablet variants) driven by a small custom router. It boots into an auth flow for new users and a lock screen for returning users, then into a 5-tab app shell.

---

## About the design files
The files in this bundle are **design references built in HTML + React (in-browser Babel)** — a working, clickable prototype that demonstrates the intended look, motion, and behaviour. **They are not production code to ship.**

The production target is **React Native / Expo** (the real Zitch app). Recreate these screens with RN primitives (`View`, `Text`, `Pressable`, `ScrollView`/`FlatList`, `Modal`) and the app's established libraries — translate the CSS here into RN styles (flexbox is the same; `boxShadow` → `shadow*`/`elevation`; `position:fixed` bottom bars → a tab navigator; horizontal scroll → `ScrollView horizontal`). If you are instead building for web, the JSX in `app/*.jsx` is directly adaptable.

> **To preview the intended result:** open `Zitch Prototype (standalone).html` in any browser. It is fully self-contained (no server, no network). Use the top toolbar's **Restart** (new-user flow) and **Reopen** (returning-user lock) to replay entry states.

## ⚠️ Design system note — read this first
This project currently has the **RemoteJobs44 design system** (a blue/Sora *jobs-board* brand) bound to it. **It is unrelated to this banking product and must NOT be applied to Zitch.** Zitch has its own established visual language — teal brand, Manrope type, the tokens documented below and shipped in `tokens.css`. Build Zitch in **its own** system. Ignore RemoteJobs44 colors, fonts, and components for this app.

## Fidelity
**High-fidelity.** Colours, typography, spacing, radii, shadows, and motion below are final and exact — the values currently in the prototype after many rounds of refinement. Reproduce them faithfully. Both **light and dark themes** are first-class (a theme toggle lives in the prototype's dev toolbar; in production it follows system + a Me-screen setting).

---

## App architecture & navigation

### Device frame
- Canvas: **414 × 868**, corner radius 56 (phone). Variants: fold 730 × 880, tablet 880 × 1180. The frame is a prototype affordance; in RN this is just the device.
- Every screen sits on the **ambient background** (`--bg-grad`): a soft brand radial glow top-right + cyan glow bottom-left over a near-white (light) / navy-green (dark) base.

### Router / state model
A single React context (`AppCtx`, consumed via `useApp()`) holds global state and a `nav` object. There are two surfaces:
- **`mode: 'auth' | 'app'`** — auth stack (splash → onboarding → signin/register → otp → setpin → biometric) vs the tabbed app.
- **Tab + stack** — `tab` is the active bottom-tab; `stack` is a push/pop array of overlay screens (flows like transfer, addmoney, kyc) rendered above the tab.

`nav` API: `push(key, props)`, `pop()`, `replace(key, props)`, `tab(name)`, `home()`, `success(receipt)`, `reset(key)`. Screens are looked up in two registries:

- **TABS:** `home · wallet · loans · cards · me`
- **REG (overlay/flow screens):** `splash, onboarding, signin, register, otp, setpin, biometric, airtime, betting, cable, electricity, exams, transfer, loan, addmoney, history, txn, notifications, coming, lock, linkbank, linkwhatsapp, accountdetails, kyc`

### Bottom navigation (5 slots, raised centre)
Order: **Home · Wallet · [WhatsApp] · Cards · Me**. 68px tall.
- The **centre slot is a raised 58px circular WhatsApp button** in WhatsApp green (`#25D366`), lifted ~18px above the bar with a notch ring (surface-coloured halo) and elevation shadow; it scales down slightly on press. Tapping it opens **Link WhatsApp**.
- The four flanking tabs show a Lucide-style outline icon + label; the active tab gets a **brand-tinted pill** behind the icon and brand-coloured icon/label. All tabs use the shared 3D press (see `Tap`).
- **Loans** is reachable from the Home services grid (it moved off the bar to make room for the WhatsApp button). On wide (fold/tablet) layouts the bar becomes a sidebar.

---

## Screens

> Grouped Auth → App tabs → Flows → Identity. Measurements are exact. Copy strings are quoted.

### AUTH

**Splash** (`splash`) — full-bleed brand screen on the hero gradient. Centre: the **coin-flip loader** — the Zitch badge (navy circle + cyan ribbon-Z) flipping in 3D (`rotateY 0→180→0`, 1.9s, `cubic-bezier(.5,0,.5,1)`, with `perspective: 560px`). Wordmark "ZITCH" + tagline "Pay. Send. Grow." Footer trust line. Auto-advances (~1.8s) to onboarding (new) or lock (returning).

**Onboarding** (`onboarding`) — 3-slide carousel, dot indicators, "Skip" (top-right) + "Next"/"Get Started". Each slide is a 150px rounded-square hero icon tile over copy:
1. `send` icon · **"Send money instantly"** · "Free transfers to Zitch and any Nigerian bank, with saved beneficiaries."
2. **WhatsApp logo** on a `#25D366` tile · **"Bank on WhatsApp"** · "Check your balance, send money and pay bills right inside your WhatsApp chats."
3. `more` (grid) icon · **"Everything in one app"** · "Airtime, data, bills, cards, savings & loans — all in one place."

**Sign In** (`signin`) — "Welcome back" with the **badge logo** (56px) at top. Phone + password fields; **"Forgot password?"** (tappable → toast "Enter your email or phone — a reset link is on the way"). Primary "Log In". Link to Register.

**Register** (`register`) — "Create your account" / "Join 5,000,000+ Nigerians on Zitch". Fields, all validated inline (red helper text on bad input):
- **Full name** (`user` icon) — min 3 chars.
- **Phone number** (`airtime` icon) — must match `^0\d{10}$` (11-digit NG). Error: "Enter a valid 11-digit number (e.g. 0801 234 5678)".
- **Email (optional)** (`remita` icon) — if present must be a valid address.
- **Create password** (`lock` icon) — with a live **strength checklist** (each item green check / grey x): **8+ characters · 1 uppercase · 1 number**.
- **Confirm password** (`lock` icon) — must equal password; shows "Passwords do not match" / green "Passwords match".
- Terms & Privacy line. Primary button "Continue" (disabled until all valid) → shows **"Sending code…"** then advances to OTP, **passing the entered phone**.

**OTP** (`otp`) — "Verify your number" / "Enter the 6-digit code sent to **{the phone you entered}**" (formatted `0801 234 5678`). Six segmented boxes fed by an on-screen keypad; active box has a brand border. **Resend countdown** ticks `0:24 → 0:00`, then becomes a tappable "Resend code" (toast "A new code has been sent"). On the 6th digit shows a spinner + "Verifying your code…" then advances to Set PIN.

**Set PIN** (`setpin`) — create a 4–6 digit transaction PIN via keypad (confirm step). **Biometric** (`biometric`) — optional Face/Touch enable, then into the app.

### APP TABS

**Home** (`home`) — header (avatar + greeting + bell). **Balance hero card** (teal gradient, white text, Z watermark): "Total balance", masked toggle (eye), and a **two-line account chip** — `William Adeyemi` over `9012 345 678 · Providus` with a copy glyph. Tapping it copies **only the 10-digit account number** and pops a small local **"Account number copied"** bubble (check icon, ~1.3s) above the chip. Quick-action buttons (Add Money / Send / etc.). **Services grid** — each tile is a 48px rounded square in the service's **own colour at ~14% tint** with the matching icon (Airtime teal `#0FA295`, Data blue `#2D7FF9`, Betting amber `#F5A623`, Cable purple `#7A5CFF`, Loan orange `#E8590C`, …; full map in `SVC_COLOR`). **Linked banks** summary. **Recent activity** (see TxnRow). **Daily-interest promo strip** pinned at the very bottom.

**Wallet** (`wallet`) — primary wallet card (compact), **Connected accounts** (horizontal snap strip of `LinkedBankCard`s + connect tile), **Recent activity**. (Full per-component spec for this tab is in the earlier `design_handoff_wallet` bundle; values match.)

**Cards** (`cards`) — virtual/physical card art + a row of **three colour-coded action chips on 38px tinted tiles**: **Fund** (green `#16A34A`), **Freeze/Unfreeze** (blue `#2D7FF9`), **Details** (purple `#7A5CFF`). Each chip = column tile (icon in a `col+'22'` tinted rounded square) + label, on a `--surface` card with `--shadow-card`.

**Me** (`me`) — profile header; a **"Bank on WhatsApp"** card near the top → Link WhatsApp. Settings list incl. **Account Details**, **Identity Verification** ("BVN, NIN or selfie · raise limits", "Verify" badge), Transaction History, etc.

### FLOWS (pushed overlays)

**Add Money** (`addmoney`) — **virtual-account flow**. If no account yet: hero (bank icon) + "Get your Zitch account number", a **BVN field** (11 digits) + helper "Dial *565*0# …", primary "Get my account" → 1.6s "Creating your account…" → provisioned view: a card showing bank, **26px grouped account number**, account name, a full-width **Copy** button, a permanence note, then **Cash Deposit** and **Scan my QR Code** method rows. (The old debit-card top-up was removed.)

**Transfer / Send** (`transfer`) — recipient entry with **bank auto-detection**: on a 10-digit account number it resolves and shows the **account name** (with a brief resolving state). Saved beneficiaries. Amount entry with **balance enforcement** — when amount > balance the screen shows "Insufficient balance" + an inline "+ Add money", and Pay is disabled. PIN/biometric confirm → success receipt.

**Bill flows** (`airtime`/`betting`/`cable`/`electricity`/`exams`) — provider pick (logos shown **contained & centred** on white tiles), plan/amount, balance-checked Continue, PIN confirm, receipt.

**History / Txn detail / Notifications / Coming-soon / Lock** — supporting screens. **Lock** (`lock`) is the returning-user entry (PIN/biometric over the brand), and also routes through the coin-flip loader.

### IDENTITY

**Account Details** (`accountdetails`) — avatar with a "Change photo" affordance (brand + badge), and validated **first name / last name / email / phone** fields; "Save changes" disabled until valid (toast "Profile updated").

**Identity Verification (KYC)** (`kyc`) — a **method menu** of three colour-coded cards, then a sub-flow each. Every step ends in a branded toast; every screen carries the footer **"BVN/NIN are never stored in full."**
- **BVN** (teal `#0FA295`, "Recommended") — 2-step: 11-digit BVN → "Send verification code" (1.3s) → 6-digit OTP → "Confirm BVN".
- **NIN** (blue `#2D7FF9`) — 11-digit NIN + **upload a photo** of the slip/ID (dashed dropzone that flips to a green "NIN_slip.jpg / Tap to replace"); "Verify NIN" disabled until both present.
- **Selfie** (purple `#7A5CFF`) — a 180px circular camera target; "Start camera" runs a **liveness ring** (spinning arc, ~2.3s, "Checking liveness…") then passes. Labelled "Front camera · no Face ID needed".

**Link WhatsApp** (`linkwhatsapp`) — three states: **unlinked** (value prop + "Link WhatsApp"), **code generated** (a 6-digit code to send to the Zitch WhatsApp number, with copy + "waiting" affordance), **linked** (success state + manage/unlink). Reached from the raised centre nav button, the Me card, and the receipt ad.

---

## Key components

**`Tap`** (shared press wrapper) — wraps every interactive element. On press it applies a **3D push-in**: `transform: perspective(150px) translateZ(-12px) scale(.96)`, `opacity .92`, transition `.18s cubic-bezier(.34,1.56,.64,1)` (spring bounce-back). Recreate as the app's standard pressable feedback.

**`SuccessReceipt`** — the post-transaction receipt, used by every payment flow via `nav.success({ title, message, rows })`. On-screen: teal gradient header with the **Zitch badge logo**, success check, title/message, a **faint repeating "Zitch" watermark** behind the detail rows, the row list, a generated **reference number**, and three actions — **Save**, **Share**, **Copy ref** — plus a **"Bank on WhatsApp"** promo banner above "Back to Dashboard".
- **Save** and **Share** each open a format chooser sheet: **image (PNG)** or **PDF**.
- The receipt image/PDF is drawn on a **canvas** (`zReceiptCanvas`) — vector Zitch badge (navy circle + cyan ribbon-Z), gradient header, check, all rows, tiled "ZITCH" watermark, footer lockup with the same reference + timestamp. PDF is built by wrapping the canvas JPEG in a minimal single-page PDF (no external libs). Share uses the native share sheet (`navigator.share` with the file), falling back to download.
- In RN: replace canvas/PDF with `react-native-view-shot` + `react-native-share` / `expo-sharing`, and a PDF lib (e.g. `react-native-html-to-pdf`). Keep the layout, logo, watermark, and the image-or-PDF chooser.

**`LinkedBankCard`** (Wallet) — 280px snap card; the two **Fund** chips have **3D animated direction arrows**: the "Fund Zitch" (money-in) ↓ arrow bobs **down/in** and the "Fund {bank}" (money-out) ↑ arrow bobs **up/out**, each a perspective `rotateX` + `translateY` loop (`zArrowIn`/`zArrowOut`, 1.7s, desynced). Keep the label as one string (`'Fund ' + tag`) so flex ellipsis doesn't eat the space.

**Monograms** — banks/transactions render as a 2-letter code on a solid brand-colour tile (no raster logos). Provider bills use real logos (`assets/logos/*`) shown **`object-fit: contain`, centred** on a white tile.

**Sheets** — bottom sheets (`Sheet`, render-prop `children(close)`) slide up with the spring easing; used for confirm, save/share chooser, account actions, etc.

**Branded loader** — the coin-flip badge (above) is the app's full-screen loading animation (splash + lock). A standalone looping preview is in `Zitch Loader Options.html` (5 options) and `Zitch Loading Animation.html` (the chosen one).

---

## Interactions & behaviour
- **Balance visibility** — global `showBal`; all balances mask to `₦ ••••••` when off.
- **Copy account number** — copies the bare 10-digit string; local "copied" bubble, not a global toast.
- **Toasts** — pill at top, `background:var(--ink-1)`, white text, `check` in `--z-cyan` for success / `x` in `--z-red` for error; auto-dismiss. (Maps to the spec's `notify()`.)
- **Form validation** — Register/Account Details/KYC validate inline and gate their primary buttons; never submit invalid.
- **Balance enforcement** — pay/continue disabled when total > balance, with "Insufficient balance" + add-money shortcut.
- **Motion** — sheets/toasts use spring `cubic-bezier(.34,1.56,.64,1)`; standard transitions 150–300ms; spinners `zspin .8s linear infinite`; reduced-motion clamps animations. Slide content should show its end state for print/PDF/reduced-motion.

## State management
Global (context) state the app reads: `balance`, `showBal`, `linkedAccounts` (+`linkedLoading`), `txns` (newest first), `theme`, `biometrics`, plus the nav `tab`/`stack`/`mode`. Actions: `fund(amount)`, `pay(amount, txn)`, `addTxn(t)`, `refreshLinked()`, `refreshBank(id, patch)`, `toast(msg, type)`, and the `nav.*` methods above. Auth screens thread their own local state and pass `phone` forward into OTP.

### Data shapes
```js
// network (airtime/data)     { id, name, color, fg, logo }
// data plan                  { id, label:'6GB', sub:'30 days', price:2500 }
// linked account             { id, bank, tag, short, acct:'0124454821', balance, color, updated, status:'active'|'reauth' }
// transaction                { id, mono:'DS', t:'DSTV Compact Plus', cat:'tv', amt:-30000, time, col:'#0A66C2', status:'Successful'|'Pending' }
// beneficiary                { id, name, acct:'0123456789', bank, init:'CO', color }
```
- `acct` is a raw 10-digit string; display grouped 4-3-3. `amt` sign drives debit/credit colour and `-`/`+`. Currency: `₦` + `toLocaleString('en-NG')` (`fmtN` = whole naira).

## Design tokens
Full set in `tokens.css` (light + dark). Key values:

**Brand / accent**
- `--brand` `#0FA295` · `--brand-deep` `#00847B` · `--z-teal-200` `#8FDDD4` · `--z-teal-400` `#23B1A8`
- `--z-cyan` `#5CF5EB` · `--z-lime` `#00B51D` (credit/success) · `--z-amber` `#F5A623` (pending) · `--z-red` `#FF3B3B`
- Card gradient `135deg, #23B1A8 0% → #00847B 52% → #004D47 100%`
- Service-icon colours (`SVC_COLOR` in `tabs.jsx`): airtime `#0FA295`, data `#2D7FF9`, betting `#F5A623`, cable `#7A5CFF`, fixed `#1EA05E`, loan `#E8590C`, jamb `#F5760A`, bills `#F59E0B`, insurance `#16A34A`, remita `#2D7FF9`, movie `#D6336C`, convert `#0CA5B8`, invite `#7A5CFF`.
- Card action chips: Fund `#16A34A` · Freeze `#2D7FF9` · Details `#7A5CFF`. WhatsApp `#25D366`.

**Light surfaces / ink**
- `--surface` `#FFFFFF` · `--surface-2` `#F4F9F8` · `--surface-3` `#EAF3F1` · `--line` `#E2EEEB`
- `--ink-1` `#06231F` · `--ink-2` `#3C4F4C` · `--ink-3` `#6B7A77`
- `--bg-grad` = brand glow top-right + cyan glow bottom-left over `radial-gradient(120% 60% at 50% -8%, #DDF3EF, #EFF7F5 46%, #F5FAF9)`
- `--shadow-card` `0 1px 2px rgba(6,35,31,.04), 0 8px 24px -12px rgba(6,55,49,.18)`

**Dark** — navy-green ramp (`#041714 / #06251F / #0A3A33` backgrounds, card `#0E2A25`-class), borders darken, brand steps up one tone. See `.z-dark` in `tokens.css`.

**Type** — `--font: 'Manrope', system-ui, sans-serif`; **default body weight 500**; money uses `.z-num` (`tabular-nums`, `-.02em`). Weights 500/600/700/800. Common sizes: 26 (titles), 27 (balance), 17 (section), 15/14/13/12.5/11.5/11/10.5.

**Radii** — sm 12 · md 18 · lg 24 · pill 999; component radii 22 (hero card), 18 (bank card), 16/14 (buttons/tiles), 13 (icon buttons), 11/10 (chips/monograms).

**Motion** — spring `cubic-bezier(.34,1.56,.64,1)`; out `cubic-bezier(.22,1,.36,1)`; `zspin`, `zflip`, `zArrowIn`/`zArrowOut`, `zshimmer`, `ztoast` keyframes (in `Zitch Prototype.html`'s `<style>`).

## Assets
- **Logo** — `assets/brand/zitch-badge.png` (navy circle + cyan ribbon-Z); also `zitch-mark.png`, `zitch-ribbon*.png`. The badge is replicated **in vector** on the receipt canvas. `ZMark` component renders it in-app.
- **Provider logos** — `assets/logos/*` (MTN, Airtel, Glo, 9mobile, DSTV, GOtv, StarTimes, …). Show `contain`, centred, on white.
- **Icons** — a custom outline set (`ZIcon`/`I`, ~1.75 stroke) in `shared.jsx`. Map to Lucide in production (RefreshCw, Settings, Eye/EyeOff, Landmark, Copy, Plus, Send, ArrowDownToLine/ArrowUpFromLine, Check, X, User, Lock, …). **Monograms**, not raster logos, for banks/txns.
- **Font** — Manrope (Google Fonts), weights 500–800. No photographic imagery anywhere.

## Files
- `Zitch Prototype (standalone).html` — self-contained runnable reference. **Visual source of truth.**
- `app/App.jsx` — router, device frame, global state/context, theme + scaling, bottom nav / sidebar, screen registries.
- `app/auth.jsx` — splash (coin-flip loader), onboarding, sign-in, register (password + strength + confirm), OTP (phone continuity, resend countdown), set-PIN, biometric.
- `app/tabs.jsx` — Home, Wallet, Cards, Me, `BottomNav` (raised WhatsApp button), `TxnRow`, `SVC_COLOR` service-colour map.
- `app/flows.jsx` — AddMoney (BVN virtual account), Transfer (auto-detect + balance enforcement), bill flows, AccountDetails, **KYC**, Fixed Save, history/txn/coming.
- `app/banklink.jsx` — ConnectedAccounts, LinkedBankCard (3D arrows), MoveSheet, AccountSheet, **LinkWhatsApp**, LinkBank.
- `app/ui.jsx` — `Tap`, `SuccessReceipt` (+ `zReceiptCanvas`, PDF builder, save/share), `Sheet`, PIN pad, fields, provider grids, `Screen`/headers.
- `app/data.js` — networks, data plans, cable/billers, beneficiaries, seed txns, helpers.
- `shared.jsx` — `ZIcon`/`I` icon set, `ZMark` logo, `Avatar`.
- `tokens.css` — all design tokens (light + dark, ambient background).
- `Zitch Loading Animation.html`, `Zitch Loader Options.html` — branded loader (chosen coin-flip + the 5 explored options).

> The earlier `design_handoff_wallet` bundle drills the Wallet screen to the pixel; values are consistent with this document.
