# QwamPay competitive audit — features we can copy

A study of **qwampay.com** (a WhatsApp-native Nigerian payments product, bot persona "Kwika", operated by Qwam Technologies) and what Zitch should copy, adapt, or beat. Source: the live landing page + the in-WhatsApp onboarding/transaction flow.

## TL;DR — our angle
QwamPay is **WhatsApp-only**: onboarding and every transaction happen inside WhatsApp; there is no app. That is their strength (zero download) and their ceiling (limited trust surface, capped limits, no rich features like cards/savings/multi-currency).

**Zitch's winning position: be channel-agnostic.** Onboard and transact on WhatsApp *or* the app. WhatsApp gets you going in seconds (capped limits); the app unlocks higher limits and the full toolkit — and **your higher app limits follow you back to WhatsApp.** That's the differentiator the revamped landing now leads with.

---

## 1. Features to copy

### ✅ Already in Zitch (keep, and message better)
- Transfers to any Nigerian bank, airtime/data, bills (electricity, cable), with name-check + PIN.
- Virtual dollar cards, fixed savings, multi-currency wallets, loans, exam pins/betting.
- WhatsApp banking with natural-language ("send 5k to John") + PIN/biometric approval.
- Bank-grade KYC (BVN, NIN, face), audit trail.

### ➕ Copied onto the landing in this revamp
- **WhatsApp-first hero** with a prominent **"Open on WhatsApp"** button (WhatsApp logo) that deep-links to the chat portal (`wa.me`), plus a floating WhatsApp action button (QwamPay has one bottom-right).
- **"Two ways to bank"** section = our dual-channel + daily-limit story (WhatsApp ₦1M transfer / ₦100k bills per day; app raises limits; limits carry back to WhatsApp).
- **Transparent pricing cards** ("Simple, fair fees") mirroring QwamPay's Transfer / Airtime-Data / Bills / Other layout, with bank/network/biller chips and the **"3 free transfers each month"** hook.
- **Upgraded chat demo**: QwamPay-style **"Choose Service"** menu (Transfer / Airtime / Data / Bills / Ajo) **plus AI paste-to-pay** — paste `send 5000 to 7066737466 opay`, the bot parses account + bank + amount, runs "Verifying beneficiary… → Beneficiary found", then PIN/biometric → receipt.
- Service additions on the grid: **Ajo group savings**, **group/family wallets**, **pay by QR**, **voice & local-language** banking.

### 🔜 Worth building next (product, not just landing)
- **Ajo / Esusu group savings** as a real product (automated rounds, payouts) — QwamPay markets it prominently.
- **Group/family shared wallets.**
- **Voice-note transactions** (speech-to-intent) in Pidgin/Yoruba/Igbo/Hausa.
- **Referral codes** at onboarding (QwamPay collects one in their flow).
- **In-WhatsApp Flows** (native form cards) for onboarding steps: Personal details (name, email, DOB, ID type+number, referral), Address, **Set Transaction PIN** (with "always require PIN" toggle), and a **Privacy Notice** consent screen before collecting data.
- **Dedicated virtual account** issued at onboarding for wallet funding (QwamPay issues a Rubies MFB account in chat).

---

## 2. Design / UX patterns copied
- Green (WhatsApp) + warm **orange/amber** accent over mint-tinted sections — we kept Zitch's teal/navy brand and used WhatsApp green for WA CTAs (brand-coherent with the app).
- Rounded cards, pill chips, soft decorative blobs, lifestyle hero imagery with floating feature tags.
- "How it works" with **WhatsApp chat phone mockups** (we already had a live interactive one — kept and upgraded).
- Pricing presented as **4 clear cards**, fees shown before confirm ("pay only what you see").
- Trust framing in the footer: regulator badge (QwamPay shows **NDPC**), "Powered by WhatsApp Cloud API", "Built for Africans, by Africans", and a plain-English "we don't hold customer funds / via licensed partners" disclaimer.

---

## 3. The WhatsApp flow we mirrored (for parity + AI)
QwamPay's transaction path: **Choose Service → Transfer Money → enter 10-digit account → Select Bank → "Verifying beneficiary account…" → "Beneficiary Found" (name/bank/acct) → enter amount → PIN.**

Zitch keeps that exact structured path **and** adds the AI shortcut: the user can paste everything in one line and the assistant aligns it to the same confirm → authorize → receipt flow. Both are demoed live on the landing.

---

## 4. Recommendations / open decisions
- **Pricing numbers** on the new section mirror QwamPay's structure (₦50 flat ≤₦5k then 0.5% capped ₦500; bills ₦50–₦100). Confirm Zitch's actual fees before launch.
- **WhatsApp number**: the landing deep-links to `wa.me/2348166938327`. Point this at the official Zitch WhatsApp Business / Cloud API number when live.
- **Daily limits** (₦1M transfer / ₦100k bills on WhatsApp-only) are the values you specified — wire them to the backend KYC-tier logic so the cap is enforced, not just advertised.
- Add an **NDPC / compliance badge** to the footer once registered, to match QwamPay's trust cues.
