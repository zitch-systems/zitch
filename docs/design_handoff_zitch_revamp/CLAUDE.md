# CLAUDE.md — Zitch design handoff bundle

This folder is a **design handoff package**, not production code. The HTML/JSX files are
interactive, high-fidelity design references. Recreate them in the target stack; do not ship them.

## Surfaces & sources of truth
| Surface | Reference | Target stack |
| --- | --- | --- |
| Mobile app | `Zitch Prototype.html` + `app/` + `shared.jsx` | Expo SDK 51 / expo-router v3 / NativeWind v2 (`zitch-systems/zitch` repo) |
| Landing page | `Zitch Landing v3.html` | Web (Next.js or similar) |
| Admin portal | `Zitch Admin Portal.html` + `admin/` | Web app talking to the Django backend (`backend/` in the repo; staff endpoints + `require_staff`) |

## Ground rules
- `README.md` documents tokens, type, spacing, and every screen — read it first.
- The landing page must ship **responsive** (breakpoints ≤1020 / ≤760 / ≤480 / ≤340 with hamburger nav) and **theme-aware** (light/dark via `data-theme` on body, persisted) — both are implemented in the reference; port them, don't drop them.
- Canonical design tokens: `assets/tokens.css`. Brand: teal `#23B1A8`, deep teal `#00847B`,
  cyan `#5CF5EB`, navy `#02344A`. Logo files in `assets/brand/` (use `zitch-ribbon2.png`).
- Money correctness rules in the admin/WhatsApp designs (PIN gates, single-use FX quotes,
  CNY settlement block, opt-in-only marketing broadcasts, append-only audit) come from
  `backend/BUILD.md` in the repo — they are requirements, not suggestions.
- The landing's WhatsApp demo and the admin inbox mirror `backend/whatsapp/router.py`
  (deterministic flows first, AI intent layer behind kill switches).
- Open any HTML file directly in a browser to interact with the reference design.
