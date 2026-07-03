# Zitch landing page — static deploy

Self-contained static site for Zitch's **VTU / bill-payment** offering — airtime, data,
recharge PINs, cable TV, electricity, internet, exam PINs and betting-wallet funding. Ported
from `design_handoff_zitch_vtu_landing/` (design handoff). No build step, no server code —
deployable to any static host.

Pages: `index.html` (landing) · `terms.html` · `privacy.html`. Shared: `_ds/` (base design-system
tokens + local font fallbacks), `uploads/zitch-logo.png` (brand mark), `assets/logos/` (provider
marks). Icons load from Lucide (unpkg) and type from Google Fonts.

> The previous marketing landing (banking/transfers/FX positioning) is archived in
> `../landing-legacy/` — preserved in git, not deployed.

> **⚠️ Legal placeholders:** the Terms and Privacy pages carry `class="ph"` placeholders
> (legal entity, RC number, registered address, support/phone). Grep for `class="ph"` and
> replace every one with the real business details before shipping.

`prototype.html` + `app/` + `shared.jsx` remain the interactive app prototype (served at
`/prototype.html`), independent of the marketing pages.

Why split it out of the backend: the Django app on Render's free plan serves this page at `/`,
but free Render services sleep after idle — the first visitor waits ~50s for a cold start.
A static edge host serves it instantly and free.

Why split it out of the backend: the Django app on Render's free plan serves this page at `/`,
but free Render services sleep after idle — the first visitor waits ~50s for a cold start.
A static edge host serves it instantly and free.

## Deploy on Cloudflare (recommended)

Dashboard → **Workers & Pages → Create → Pages → Connect to Git** → pick `zitch-systems/zitch`:

- Production branch: `main`
- Build command: *(leave empty)*
- Build output directory: `landing`

Every push to `main` that touches this folder redeploys. Add the custom domain under
the project's **Custom domains** tab. The free plan allows commercial use and has
unmetered static bandwidth.

## Deploy on Vercel (alternative)

Add New → Project → import `zitch-systems/zitch`:

- Root Directory: `landing`
- Framework Preset: **Other** (no build command, output `.`)

Note: Vercel's free Hobby plan is licensed for **non-commercial use only** — for a
commercial product you need Pro ($20/seat/mo).

## Notes

- The "Sign in" / "Open the web app" links and the hero demo iframe point at `prototype.html`
  (the static app prototype, same one Render serves at `/prototype/`).
- The footer's "Admin portal" link was deliberately removed from this public copy — the staff
  console stays on Render at `/portal/`. Restore the `<li>` in `index.html` if you want it back.
- External CDNs used: Google Fonts (landing + prototype) and unpkg React/Babel (prototype only).
- `_headers` sets security headers on Cloudflare Pages; other hosts (Vercel etc.) ignore the file.
