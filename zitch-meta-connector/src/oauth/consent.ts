/**
 * The sign-in / consent page shown at GET /authorize.
 *
 * There is no user database here, and adding one would be the wrong shape for
 * what this connector is: a single-operator maintenance tool. "Signing in"
 * therefore means proving you hold the operator passphrase
 * (OAUTH_LOGIN_PASSWORD, falling back to CONNECTOR_API_KEY) — the same secret
 * that already gates every other route on this service. OAuth adds the parts
 * Claude's connector UI needs (a browser redirect flow, per-client
 * credentials, revocable expiring tokens) on top of that one fact.
 *
 * The pending request is round-tripped through a SIGNED blob in a hidden
 * field rather than a session cookie. Two consequences, both wanted: the
 * parameters cannot be tampered with between the GET and the POST (the
 * signature covers them), and the signature doubles as a CSRF token, since an
 * attacker's forged form cannot produce one.
 */

/** Escapes text for interpolation into HTML. Everything rendered below that
 * came from the request goes through this — the client name in particular is
 * attacker-chosen, because client registration is unauthenticated. */
function esc(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const STYLE = `
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#f4f7f6; color:#12201f; padding:24px;
         font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }
  .card { width:100%; max-width:420px; background:#fff; border:1px solid #e4ecea; border-radius:14px;
          padding:28px; box-shadow:0 8px 28px rgba(18,32,31,.08); }
  h1 { margin:0 0 4px; font-size:19px; }
  p.sub { margin:0 0 20px; color:#5f7370; font-size:13px; }
  dl { margin:0 0 20px; padding:14px; background:#f4f7f6; border-radius:9px; font-size:13px; }
  dt { color:#5f7370; font-size:11px; text-transform:uppercase; letter-spacing:.07em; }
  dd { margin:2px 0 10px; word-break:break-all; font-weight:600; }
  dd:last-child { margin-bottom:0; }
  label { display:block; font-size:13px; font-weight:600; margin-bottom:6px; }
  input[type=password] { width:100%; padding:11px 12px; font-size:15px; border:1px solid #cdd9d7;
                         border-radius:9px; background:#fff; color:#12201f; }
  input[type=password]:focus { outline:2px solid #0fa295; outline-offset:1px; border-color:#0fa295; }
  button { width:100%; margin-top:16px; padding:12px; font-size:15px; font-weight:600; color:#fff;
           background:#0fa295; border:0; border-radius:9px; cursor:pointer; }
  button:hover { background:#0c8b80; }
  .err { margin:0 0 16px; padding:10px 12px; border-radius:9px; background:#fdecea; color:#a32c1c;
         font-size:13px; border:1px solid #f5c6c0; }
  .scope { margin:16px 0 0; font-size:12px; color:#5f7370; }
  @media (prefers-color-scheme: dark) {
    body { background:#0e1615; color:#e8f0ef; }
    .card { background:#16211f; border-color:#243330; box-shadow:none; }
    dl { background:#101a19; }
    input[type=password] { background:#101a19; border-color:#2c3d3a; color:#e8f0ef; }
    .err { background:#3a1a16; border-color:#6b2b22; color:#ffb4a8; }
  }
`;

export function renderConsentPage(opts: {
  clientName: string;
  redirectUri: string;
  scope: string;
  request: string;
  error?: string | undefined;
}): string {
  const error = opts.error
    ? `<p class="err">${esc(opts.error)}</p>`
    : '';
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Authorize · Zitch Meta Connector</title>
<style>${STYLE}</style></head>
<body><div class="card">
  <h1>Authorize access</h1>
  <p class="sub">An application is asking to use the Zitch Meta Connector.</p>
  ${error}
  <dl>
    <dt>Application</dt><dd>${esc(opts.clientName)}</dd>
    <dt>Redirects to</dt><dd>${esc(opts.redirectUri)}</dd>
    <dt>Access</dt><dd>${esc(opts.scope)} — read-only</dd>
  </dl>
  <form method="post" action="">
    <input type="hidden" name="request" value="${esc(opts.request)}">
    <label for="password">Operator passphrase</label>
    <input type="password" id="password" name="password" autocomplete="current-password"
           autofocus required>
    <button type="submit">Approve</button>
  </form>
  <p class="scope">This grants read-only access to WhatsApp/Meta configuration
  diagnostics. It never exposes the Meta access token, and no customer data is
  reachable through this connector.</p>
</div></body></html>`;
}

export function renderErrorPage(title: string, detail: string): string {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>${esc(title)} · Zitch Meta Connector</title>
<style>${STYLE}</style></head>
<body><div class="card">
  <h1>${esc(title)}</h1>
  <p class="err">${esc(detail)}</p>
  <p class="scope">Nothing was authorized. Close this window and start again from
  the application that sent you here.</p>
</div></body></html>`;
}
