# zitch-meta-connector

A remote, HTTPS-based [MCP](https://modelcontextprotocol.io) server that gives an AI assistant
(Claude, or later a ChatGPT GPT Action) **read-only** maintenance and configuration visibility
into the Zitch WhatsApp Cloud API and Meta Business API — webhook status, phone-number config,
message templates, delivery health, and credential validity.

It is a standalone service. It does not import, call, or modify anything in `backend/` — it
talks only to Meta's Graph API, over the network, using its own credentials. It cannot reach a
Zitch customer's PIN, biometric enrollment, balance, transaction history, or personal data,
because it has no code path into the Zitch database or API at all.

## What it exposes

Thirteen read-only tools, available both as MCP tools (`POST /mcp`) and as a plain REST mirror
(`GET /rest/*`, for a future ChatGPT GPT Action — see [openapi.yaml](./openapi.yaml)):

**Webhooks and credentials**

| Tool | What it answers |
|---|---|
| `check_whatsapp_webhook_status` | Which app(s) are subscribed to the WABA's webhook events, and which event fields. |
| `inspect_webhook_events` | Which webhook event *types* are currently configured (not a delivery log — see below). |
| `verify_meta_credentials` | Whether `META_ACCESS_TOKEN` can currently read the configured WABA and phone number. |

**Flows** — the interactive multi-screen forms (the PIN, signup and identity ladders), *not* to be
confused with message templates

| Tool | What it answers |
|---|---|
| `list_whatsapp_flows` | Every Flow on the WABA, with publish status and categories. |
| `inspect_whatsapp_flow` | One Flow's `validation_errors` — what Meta refuses a publish on — plus its preview URL. |
| `get_published_flow_screens` | The screens and routing of the Flow Meta *actually has published*, including routing roots. |

**Numbers, account and templates**

| Tool | What it answers |
|---|---|
| `check_whatsapp_phone_number_config` | One number's verification status, quality rating, messaging limit tier. |
| `list_whatsapp_phone_numbers` | The same, for every number on the WABA. |
| `get_waba_details` | Account review and business verification status — what silently gates Flows and templates. |
| `get_whatsapp_business_profile` | The public profile customers see on the business. |
| `list_whatsapp_message_templates` | Approved/pending/rejected message templates, with status and quality score. |

**Volume and delivery**

| Tool | What it answers |
|---|---|
| `inspect_failed_message_deliveries` | Aggregate sent/delivered counts per day for a bounded window. |
| `get_conversation_analytics` | Conversation volume and cost by category over a bounded window. |

### Why the Flow tools exist

The Flow JSON lives in this repo but is **published by hand** in WhatsApp Manager, so every screen
added in code is missing on Meta's side until someone pastes and publishes. The symptom is that
older screens keep working while the newest ones are rejected — which reads like "some features are
broken" rather than "the Flow is one publish behind", and is indistinguishable from a bad token, an
unverified business, or an expired session without exactly this reading.

`get_published_flow_screens` also computes **routing roots**: the screens with no incoming route,
which are the only ones a Flow may *open* on. Opening on any other screen is rejected with error
`131009` ("screen not allowed as first screen") — a message that names the screen but not the rule,
and the single most confusing Flow failure to debug. It flags dangling routes and unreachable
screens too. The full Flow JSON is deliberately **not** returned: it embeds the logo as base64 and
runs to hundreds of KB.

**Two of these are honestly limited, on purpose.** Meta's Graph API does not expose a queryable
history of past webhook deliveries or individual failed messages — webhooks are push-only and
Meta does not retain or replay them. `inspect_failed_message_deliveries` returns the closest real
signal (aggregate daily analytics), and `inspect_webhook_events` returns the *current subscription
configuration* (which event types are enabled), not a log of specific past events. Each tool's
response carries a `note` field spelling this out, rather than the connector silently pretending
to have a capability Meta doesn't provide. A true webhook-event log would have to come from
Zitch's own backend event storage — deliberately out of scope for this project (see "What this
project does not do" below).

## What this project does not do

- **No write, delete, token-rotation, or payment tools.** Only read tools exist. `readOnly`
  is an explicit, named field in `src/config.ts` — turning any of that on in the future is meant
  to be a deliberate, reviewable code change, not a side effect of adding a file.
- **Never touches Zitch's production webhook or banking code.** This is a new, isolated
  directory (`zitch-meta-connector/`) with its own `package.json`. Nothing in `backend/` was
  changed to build it.
- **Never returns the Meta access token.** `src/metaClient.ts` is the only file that reads
  `META_ACCESS_TOKEN`; it goes only into an outbound `Authorization` header, never a URL, a log
  line, or a tool response. `src/redact.ts` is a second, independent layer that strips any
  registered secret out of every outbound response and log line as a backstop.
- **Never touches customer data.** There is no code path from this server into the Zitch
  database, the Zitch REST API, or any customer's account. Everything it reads is Meta-side
  business configuration (Zitch's own WhatsApp number and templates) or aggregate, non-PII
  analytics.

## Authentication: OAuth 2.1, or a plain API key

Two credential types are accepted, because two very different callers need in:

| Caller | Credential |
|---|---|
| **Claude web custom connectors** | OAuth 2.1 access token — its UI can only sign in through a browser redirect flow |
| **Scripts, curl, health probes, a future GPT Action** | `CONNECTOR_API_KEY` directly — none of these can run a browser flow |

Both land on the same read-only tools, with the same rate limits and the same audit logging.
Neither ever exposes `META_ACCESS_TOKEN`: **an OAuth token authenticates the caller *to this
connector* and is never itself a Meta credential.**

### Connecting Claude

In Claude → Settings → Connectors → *Add custom connector*, give it:

```
https://zitch-meta-connector.onrender.com/mcp
```

That is all it needs. Claude discovers everything else on its own: the `401` from `/mcp` carries a
`WWW-Authenticate` header pointing at this server's protected-resource metadata, Claude reads the
metadata, registers itself as a client, and opens the sign-in page. You'll be asked for the
**operator passphrase** — `OAUTH_LOGIN_PASSWORD`, or `CONNECTOR_API_KEY` if you haven't set one.
Approve, and Claude holds a token from then on.

### The OAuth endpoints

| Endpoint | What it is |
|---|---|
| `GET /.well-known/oauth-authorization-server` | RFC 8414 authorization-server metadata |
| `GET /.well-known/oauth-protected-resource` | RFC 9728 protected-resource metadata |
| `POST /oauth/register` | RFC 7591 dynamic client registration |
| `GET /oauth/authorize` | sign-in + consent screen |
| `POST /oauth/authorize` | approve → redirects back with an authorization code |
| `POST /oauth/token` | `authorization_code` and `refresh_token` grants |

Design notes worth knowing before you change any of it:

- **PKCE is mandatory and S256-only.** `plain` is refused outright — it protects against nothing
  an attacker who can read the authorization request can't defeat, and OAuth 2.1 drops it.
- **Client registration is stateless.** The `client_id` *is* a signed token carrying that client's
  own metadata, and the `client_secret` is derived from it. Nothing is stored, so a client
  registered before a redeploy still works after one — an in-memory registry would silently force
  Claude to re-register on every deploy.
- **Authorization codes are the one stateful thing**, precisely because they must be single-use,
  and "already redeemed?" is not a question a self-contained token can answer. They live ~60 seconds.
- **Not a JWT, deliberately.** These tokens carry no client-supplied `alg` header, which is the
  root of the whole algorithm-confusion family of bugs. One algorithm, fixed in code.
- **Redirect URIs are allowlisted by host** (`claude.ai`, `claude.com`, loopback by default).
  Registration is unauthenticated — that's what makes it work for Claude — so without that
  allowlist anyone could register a client pointing at their own callback and turn this into an
  open redirector.
- **Revocation is key rotation.** Tokens are stateless and there is no denylist, so a refresh
  token stays valid until it expires (see the header comment in `src/oauth/tokens.ts` for the full
  trade-off). Rotating `CONNECTOR_API_KEY` or `OAUTH_SIGNING_KEY` invalidates *every* outstanding
  token instantly — that is the revocation mechanism.

## Security model

- **Authentication**: every request to `/mcp` and `/rest/*` — not just the first one on a
  connection — must present either an OAuth access token or `CONNECTOR_API_KEY` (as
  `Authorization: Bearer <key>` or `X-Connector-Api-Key: <key>`). Secrets are compared with
  `crypto.timingSafeEqual` (`src/auth.ts`) to avoid a timing side-channel. `/healthz` and the
  OAuth endpoints are the only unauthenticated routes — the latter necessarily so, since
  authenticating is what they're *for* — and they are still behind the per-IP rate limiter.
- **Rate limiting**: a fixed-window limiter (`src/rateLimit.ts`), 30 requests/minute per API-key
  fingerprint by default, configurable via `RATE_LIMIT_WINDOW_MS` / `RATE_LIMIT_MAX_REQUESTS`.
  In-memory and per-process — this is sized for a small admin/maintenance workload, not a public
  API; see the comment in that file for how to swap in a shared store if it's ever scaled out.
- **Input validation**: every tool argument is validated with a `zod` schema (`src/schemas.ts`)
  before it can reach an outbound HTTP call — Meta object IDs must be digit strings, lookback
  windows are capped at 30 days, pagination cursors and template-name filters are length-bounded,
  and every schema is `.strict()` (rejects unknown fields outright).
- **Timeouts**: every Graph API call has a hard timeout (`GRAPH_TIMEOUT_MS`, default 10s) via
  `AbortController` — a hung upstream call cannot hang a tool call indefinitely.
- **Safe error handling**: Graph API errors are normalized into messages safe to show a caller
  (Meta's own `error.message`, e.g. "Invalid OAuth access token") — never a raw stack trace or an
  internal path. A tool failure returns `isError: true` on the MCP path or a 4xx/5xx JSON body on
  the REST path; it never crashes the process.
- **Audit logging**: every tool/REST call is logged as one structured JSON line to stdout —
  timestamp, tool name, caller key *fingerprint* (first 6 chars only, never the full key),
  outcome, latency, and a redacted error message on failure. No request arguments or response
  payloads are logged, so even "safe" Meta config data doesn't accumulate in a log store forever.

## Local testing

Requires Node.js 20+.

```bash
cd zitch-meta-connector
npm install
cp .env.example .env
```

Fill in `.env`:

```bash
META_ACCESS_TOKEN=<a WhatsApp Business Management read-scoped access token>
META_WABA_ID=<your WhatsApp Business Account ID>
META_PHONE_NUMBER_ID=<your phone number ID>
CONNECTOR_API_KEY=$(openssl rand -hex 32)   # generate a real one
```

Run it:

```bash
npm run dev        # tsx watch — restarts on file changes
# or
npm run build && npm start
```

Check it's alive (no auth required):

```bash
curl http://localhost:8787/healthz
```

Call a tool over the REST mirror (fastest way to sanity-check credentials without an MCP client):

```bash
curl -H "Authorization: Bearer $CONNECTOR_API_KEY" \
  http://localhost:8787/rest/verify-meta-credentials
```

Call a tool over MCP directly with `curl` (what an MCP client does under the hood):

```bash
curl -s -X POST http://localhost:8787/mcp \
  -H "Authorization: Bearer $CONNECTOR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Run the unit test suite (auth, rate limiting, redaction, config validation, input schemas — all
pure logic, no real Meta calls):

```bash
npm test
```

Typecheck only:

```bash
npm run typecheck
```

### Connecting Claude to a local instance

Claude's remote-MCP-connector UI needs an HTTPS URL, so a purely local server needs a tunnel
(e.g. `ngrok http 8787`) to test against Claude directly. Point the connector at
`https://<tunnel-host>/mcp` and supply the `CONNECTOR_API_KEY` as its bearer token/API key. For
faster iteration without a tunnel, any MCP-compatible CLI client that speaks Streamable HTTP (or
the `curl` commands above) can exercise every tool locally.

## Production deployment

This is a standard containerized Node service — deploy it anywhere that runs a container or a
Node process. It does not need a database, Redis, or any persistent volume.

### Docker

```bash
docker build -t zitch-meta-connector .
docker run -p 8787:8787 \
  -e META_ACCESS_TOKEN=... \
  -e META_WABA_ID=... \
  -e META_PHONE_NUMBER_ID=... \
  -e CONNECTOR_API_KEY=... \
  zitch-meta-connector
```

The image is a multi-stage build (`Dockerfile`): TypeScript is compiled in a `node:22-slim`
build stage, and the runtime stage installs only production dependencies and runs as a
non-root user (`connector`, uid 10001). It ships a `HEALTHCHECK` against `/healthz`.

### Render

This project deliberately is **not** wired into the root `/render.yaml` — that file defines
Zitch's live banking services, and this connector has no reason to share a deploy pipeline,
scaling policy, or on-call rotation with them. It has its own Blueprint instead:
[`render.yaml`](./render.yaml), scoped to exactly one service.

**Deploy via the Blueprint (recommended — build/start config lives in source, not clicked
together in a dashboard):**

1. In Render: **New +** → **Blueprint** → select this repo.
2. When Render asks for the Blueprint file, it defaults to a root-level `render.yaml` — override
   the path to **`zitch-meta-connector/render.yaml`**. (If your Render account/plan doesn't
   support a non-root Blueprint path, use the manual setup below instead — the values are
   identical.)
3. Render creates one Web Service, `zitch-meta-connector`, reading `rootDir: zitch-meta-connector`,
   `runtime: node`, `buildCommand: npm ci && npm run build`, `startCommand: npm start`,
   `healthCheckPath: /healthz`, on the `starter` plan (not `free` — see the comment in
   `render.yaml` on why: a free-tier cold start would stall the first call after any quiet
   period).
4. Render prompts for the secrets marked `sync: false` in the Blueprint —
   `META_ACCESS_TOKEN`, `META_WABA_ID`, `META_PHONE_NUMBER_ID`, `CONNECTOR_API_KEY`
   (`openssl rand -hex 32` for the last one). Everything else already has a value in the
   Blueprint and is editable from the dashboard afterward without touching source.
5. Once deployed, the MCP endpoint is `https://<your-service>.onrender.com/mcp`.

**Manual setup (equivalent, no Blueprint):** New + → Web Service → this repo → **Root Directory**
`zitch-meta-connector` → **Runtime** Node → **Build Command** `npm ci && npm run build` →
**Start Command** `npm start` → **Health Check Path** `/healthz` → add the same four secrets
above as environment variables.

**Docker, on Render or elsewhere:** the same service also builds from the `Dockerfile` in this
directory if you'd rather run it as a container (on Render, choose **Runtime: Docker** instead of
Node when creating the Web Service — everything else above still applies). The same pattern works
on Fly.io, a bare VM behind a reverse proxy, or any other container host.

### Generating `META_ACCESS_TOKEN`

Use a Meta **System User** access token (Business Settings → Users → System Users), not a
personal user token — it won't expire when an employee's session does. Grant it
`whatsapp_business_management` with **read** access only; this connector never needs write
scope, and it never will unless a future write tool is deliberately added.

### Rotating `CONNECTOR_API_KEY`

Generate a new key (`openssl rand -hex 32`), set it as the new environment variable value, and
redeploy. There is no in-app rotation endpoint by design — token rotation is explicitly one of
the write-adjacent operations this connector does not implement (see "What this project does not
do").

## Adding a ChatGPT GPT Action later

[`openapi.yaml`](./openapi.yaml) describes the `GET /rest/*` mirror of every tool, ready to paste
into a GPT Action's "Import from URL"/"Schema" field once this service is deployed — replace the
placeholder `servers[0].url` with your real deployed host first. The REST endpoints share the
exact same auth, rate limiting, audit logging, input validation, and underlying tool
implementation as the MCP path (`src/tools/*.ts` is the single implementation both surfaces call
— see `src/rest.ts`), so there is nothing to keep in sync by hand.

## Project layout

```
src/
  config.ts        Environment loading + fail-fast validation (the only file reading process.env)
  auth.ts           Per-request CONNECTOR_API_KEY check (timing-safe)
  rateLimit.ts        In-memory fixed-window rate limiter
  audit.ts        Structured JSON audit logging
  redact.ts        Secret-redaction backstop for logs/responses
  metaClient.ts     The only file that reads META_ACCESS_TOKEN; outbound Graph API calls
  schemas.ts        zod input validation for every tool
  mcpServer.ts        Builds a stateless McpServer + registers all tools
  rest.ts        Plain REST mirror of the same tools (for a future GPT Action)
  index.ts        Express app: wires OAuth -> rate limit -> auth -> audit -> tool execution
  oauth/
    router.ts     The OAuth 2.1 endpoints (metadata, register, authorize, token)
    sign.ts     HMAC-signed tokens (no client-supplied `alg`, by design)
    clients.ts     Stateless dynamic client registration + redirect allowlist
    codes.ts     Single-use authorization codes + PKCE S256
    tokens.ts     Access/refresh tokens, audience binding, and their limits
    consent.ts     The sign-in / consent page
  tools/
    registry.ts     Single source of truth: name, schema, handler per tool
    webhookStatus.ts, phoneNumberConfig.ts, messageTemplates.ts,
    failedDeliveries.ts, webhookEvents.ts, verifyCredentials.ts,
    flows.ts (Flow list/inspect/published-screens), account.ts (WABA,
    numbers, business profile, conversation analytics)
test/            Unit tests for auth, rate limiting, redaction, schemas, config
openapi.yaml        REST API spec for a future ChatGPT GPT Action
Dockerfile        Multi-stage production image
render.yaml        Render Blueprint for this service alone (see "Production deployment")
.node-version        Pins the Node version Render (and any nvm/fnm-based local setup) builds with
```
