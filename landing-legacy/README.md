# Archived landing — pre-VTU marketing site

These are the **previous** Zitch marketing landing pages, kept for reference. They
were replaced by the VTU / bill-payment landing now served from `../landing/`.

- `index.html` — old marketing landing (banking / transfers / FX / WhatsApp positioning)
- `terms.html` — old Terms of Use
- `privacy.html` — old Privacy Policy

They are intentionally **outside** the Cloudflare Pages output directory
(`landing/`), so they are preserved in git but not deployed. Their relative asset
references (`assets/…`, `prototype.html`, `app/…`) point at files that still live
under `../landing/` — open them from a checkout with those assets alongside if you
need to view the old design.

To restore the old landing, move `index.html`/`terms.html`/`privacy.html` back into
`../landing/` (replacing the VTU pages).
