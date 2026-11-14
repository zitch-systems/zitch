# Zitch landing page — static deploy

Self-contained static site for Zitch: **WhatsApp banking first** (send money, airtime and data,
bills — by chat), with the Zitch app as the second half of the page. No build step, no server
code — deployable to any static host.

Pages: `index.html` (landing) · `terms.html` · `privacy.html` · `pay.html`. Type loads from
Google Fonts; everything else `index.html` needs is in `assets/` (`brand/`, `logos/`,
`landing-v1/`).

What is on the landing page:

- **Hero 3D mockup** — two screens mapped onto a product render (`landing-v1/phones-3d.webp`)
  that lift out into phones you can use. Script only writes transforms and opacities, so it
  animates on the compositor. The live app (`prototype.html?embed=1`) boots only when a visitor
  reaches for the app phone.
- **Chat demo** — a scripted copy of the real WhatsApp flow: "Hi" → numbered menu → reply with a
  number or type it → private form → name check → PIN → receipt. Every name, account and price
  in it is a sample; nothing is sent anywhere.
- **Animated mark** — `landing-v1/zitch-coil.v2.js`, the same renderer as
  `assets/zitch-loader.v1.js` plus `decorative`, `maxDensity`, `fps` and adaptive segment size
  for small and background coils.

> `/assets/*` is cached as immutable for a year (`_headers`). Never edit a file under
> `assets/landing-v1/` in place: add `landing-v2/` (or a new filename) and point `index.html` at it.

> The previous VTU / bill-payment landing was replaced by this page. It is in git history:
> `git show d07a767:landing/index.html`. The older banking landing is still in `../landing-legacy/`.

> **Before relying on the page commercially:** the App Store / Google Play badges and the
> "Chat on WhatsApp" buttons point at on-page anchors until real store listings and the bot's
> `wa.me` link are supplied; partner names (Wema Bank, NDIC, Prembly) are text until official
> logo files are supplied.

> **⚠️ Legal placeholders:** the Terms and Privacy pages carry `class="ph"` placeholders
> (legal entity, RC number, registered address, support/phone). Grep for `class="ph"` and
> replace every one with the real business details before shipping.

`prototype.html` + `app/` + `shared.jsx` remain the interactive app prototype (served at
`/prototype.html`), independent of the marketing pages.

`assets/zitch-loader.v1.js` is the branded coil loader (`ZitchLoader.mount(el, { height })`) — the
prototype's boot splash and its "Authorizing payment…" state use it. It is also the source the
mobile app's loader sprite is baked from (`scripts/bake-loader-sprite.cjs`), so web and app play
the same animation. The filename is versioned because `/assets/*` is cached as immutable: bump it
(`v2`) when the animation changes.

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
- External CDNs used: Google Fonts (landing + prototype) and unpkg React/Babel (prototype only). The landing no longer loads Lucide.
- `_headers` sets security headers on Cloudflare Pages; other hosts (Vercel etc.) ignore the file.
