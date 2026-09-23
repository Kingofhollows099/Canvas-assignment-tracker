# Security checklist & review

This is a self-hosted tool that can be exposed to the internet, holds a login, a
Canvas API token (in Canvas mode) or a sync token (in browser-sync mode), and
accepts data pushed from a browser userscript. This document enumerates the
threats that matter for that shape of app and records how each is handled.

Status legend: **PASS** (handled), **FIXED** (addressed in this review),
**ACCEPTED** (residual risk noted, reasonable for a single-user self-hosted tool),
**OPERATOR** (depends on how the user deploys it).

## 1. Authentication & sessions

- [x] **PASS** Passwords hashed with scrypt (memory-hard KDF), never stored or logged in plaintext; `APP_PASSWORD_HASH` lets you avoid plaintext at rest entirely.
- [x] **PASS** Password and username compared in constant time (`hmac.compare_digest`), so no timing oracle for either.
- [x] **PASS** Session tokens are 256-bit random (`secrets.token_urlsafe(32)`), stored server-side, opaque to the client.
- [x] **PASS** No session fixation: a fresh token is minted on login; there is no pre-auth session.
- [x] **PASS** Session cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` (default; `COOKIE_SECURE=0` only for local HTTP).
- [x] **PASS** Logout destroys the server-side session and expires the cookie.
- [x] **PASS** Sessions expire (`SESSION_TTL_SECONDS`, default 7 days).
- [x] **FIXED** Brute-force throttle no longer trusts a spoofable `X-Forwarded-For` by default — it keys on the socket peer unless `TRUST_PROXY_XFF=1` is set for a known proxy, closing the "reset the throttle by forging XFF" bypass.
- [x] **PASS** Failed logins reveal nothing distinguishing (same generic message for wrong user or wrong password).
- [x] **ACCEPTED** No multi-factor auth. Documented; a second factor can be added at the reverse proxy (NPM access list) if wanted.

## 2. Authorization / access control

- [x] **PASS** The web UI (`/`, `/app.js`, `/app.css`, `/index.html`) and `/api/assignments` require a valid session when auth is enabled.
- [x] **PASS** The app refuses to bind a public interface with no login configured (`--allow-no-auth` required to override).
- [x] **PASS** `/api/sync` is authorized by a separate bearer token (a browser userscript cannot do a cookie login); the token is compared in constant time.
- [x] **PASS** `/login` and `/login.js` are the only pre-auth resources, and both are static and non-sensitive.

## 3. CSRF

- [x] **PASS** `/api/sync` uses a bearer token in a header, not an ambient cookie, so it is not CSRF-able.
- [x] **PASS** `SameSite=Lax` on the session cookie blocks cross-site state-changing POSTs (`/logout`), so cross-site logout/login CSRF is mitigated.
- [x] **ACCEPTED** No per-form CSRF token on `/login`. Login CSRF (logging a victim into the attacker's own account) is low-impact for a single-user tool and further limited by SameSite.

## 4. Injection & untrusted input

- [x] **PASS** No database and no shell execution: no SQL/command injection surface. The only subprocess call is `openssl` with a fixed argument list (no shell, no user input).
- [x] **PASS** Pushed assignment data is validated and bounded: type whitelist, per-string length cap (500), max items per sync (2000), numeric coercion for points.
- [x] **FIXED** Pushed `url` values are restricted to `http(s)://` on the server, and the frontend only turns a value into a clickable link when it is `http(s)://` — closing a `javascript:`-URL click-to-XSS vector.
- [x] **PASS** All dynamic text in the UI is inserted via `textContent` (never `innerHTML`); assignment titles/courses cannot inject markup.
- [x] **PASS** The login page never echoes user-supplied query content; it maps `?error=…` to fixed strings.

## 5. XSS / content security

- [x] **FIXED** A strict Content-Security-Policy is sent on every response (`default-src 'self'`, `script-src 'self'`, `frame-ancestors 'none'`, `base-uri 'none'`, `form-action 'self'`), so injected inline script cannot run and the page cannot be framed.
- [x] **FIXED** The login page's inline script was moved to `/login.js` so the CSP can forbid inline scripts without breaking the page.
- [x] **FIXED** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer` are sent on every response.

## 6. Transport security

- [x] **PASS** Browser-sync mode serves HTTPS (self-signed localhost cert); behind a proxy it runs HTTP and the proxy terminates TLS.
- [x] **PASS** Canvas mode refuses a non-`https://` `CANVAS_BASE_URL`, so the Canvas token is never sent in cleartext.
- [x] **PASS** The Canvas client refuses to follow a pagination link to any other host, so the token only ever goes to the configured Canvas instance.
- [x] **OPERATOR** When exposed, TLS is provided by the reverse proxy (e.g. NPM/Cloudflare). Enable "Force SSL" so the app is never reached over plain HTTP.

## 7. Secrets management

- [x] **PASS** `.env`, `.sync_token`, and `certs/` are git-ignored; no secret is committed (verified).
- [x] **PASS** The Canvas token stays server-side; it is never sent to the browser.
- [x] **PASS** `--hash-password` lets the operator keep only a hash in `.env`.
- [x] **PASS** Secrets are read from env/files, never hard-coded.
- [x] **ACCEPTED** In the userscript, the sync token is embedded in the script and `@connect *` allows posting to any host; it is only ever sent to the operator-set `trackerBase`. Tightening `@connect` to your own domain is documented as optional hardening.

## 8. SSRF / outbound requests

- [x] **PASS** The only outbound request is to the configured Canvas base URL (Canvas mode). Users cannot supply arbitrary URLs for the server to fetch.
- [x] **PASS** Pagination is constrained to the same host as the configured Canvas instance.

## 9. Denial of service / resource limits

- [x] **PASS** Request bodies are size-capped before reading (`/api/sync` 4 MiB, `/login` 16 KiB).
- [x] **PASS** Canvas responses are cached (default 5 min) to avoid hammering Canvas and to bound work per page load.
- [x] **FIXED** Malformed/hostile JSON on `/api/sync` (e.g. deeply nested) returns 400 instead of raising, so it can't crash a request thread.
- [x] **PASS** Login throttling limits password-guess volume.
- [x] **OPERATOR** Connection-level flooding is mitigated by the reverse proxy / Cloudflare in front of the app.

## 10. Information disclosure

- [x] **PASS** Access logs skip successful requests and never include the password (POST body) or tokens; query strings carry no secrets.
- [x] **PASS** Error responses are generic and don't leak stack traces.
- [x] **FIXED** The `Server` header no longer advertises the Python/BaseHTTP version.
- [x] **PASS** `Cache-Control: no-store` on responses keeps assignment data out of shared caches.

## 11. Open redirect / header injection

- [x] **PASS** All redirects use fixed, internal, relative paths — no user-controlled redirect target.
- [x] **PASS** `Set-Cookie` and `Location` values are server-generated; no user input is reflected into headers.

## 12. Path traversal / file exposure

- [x] **PASS** Static serving uses an exact-match allowlist of paths → filenames; no user-controlled path is ever joined to the filesystem, so `../` traversal is impossible.

## 13. Deployment / container hardening

- [x] **PASS** The container runs as a non-root user (uid 10001) with a dedicated writable data dir.
- [x] **PASS** Zero third-party Python dependencies (standard library only), minimizing supply-chain exposure.
- [x] **PASS** The port is published to host loopback by default in the compose file; exposure is via the reverse proxy.
- [x] **OPERATOR** Keep the base image (`python:3.12-slim`) updated for OS CVEs; rebuild periodically.

## 14. Data sensitivity

- [x] **PASS** Stored data is assignment metadata (titles, due dates, courses) — not credentials. The push store contains no secrets.
- [x] **PASS** The persisted store is written atomically (temp file + replace) so a crash can't corrupt it.

## Residual risks (accepted, by design for a single-user self-hosted tool)

- No MFA on the app login (add at the proxy if desired).
- No CSRF token on the login form (SameSite + single-user scope make this low-impact).
- The userscript embeds the sync token and uses `@connect *`; safe as long as `trackerBase` points only at your own tracker.
