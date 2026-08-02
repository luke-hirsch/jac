# [infra] Wildcard subdomain hosting — DNS, TLS, nginx, cookies, dev

**Branch:** `infra/subdomain-hosting` (off `main`).
**Phase:** portfolio-multiuser (guide 2 of 4). Depends on guide 1's `BASE_DOMAIN` +
`owner_for_host`.

## Context / goal

Guide 1 made the backend resolve the owner from `<handle>.<BASE_DOMAIN>`. This guide stands
up the hosting so those hosts actually reach Django: wildcard DNS + TLS on `*.luke-hirsch.de`,
an nginx host split (apex = Django landing, `app.` = authed tool, `<handle>.` = SPA), the
Django host/CSRF settings that trust the wildcard, and a `*.localhost` dev setup so the
subdomain survives the Vite proxy. **Moving to a neutral domain later reuses this recipe
verbatim** — only the domain string changes.

**Cookie decision (important, keeps this simple):** the authed session lives **only on
`app.<domain>`**; the public portfolio subdomains are always anonymous. So there is **no
cross-subdomain cookie sharing** — session/CSRF cookies stay host-scoped to `app.`, and the
anonymous public POSTs (`rank`/`intro`) don't need CSRF (DRF's `SessionAuthentication` only
enforces CSRF for authenticated requests). The one cost: the owner previewing their own
portfolio isn't recognized across the host boundary, so their preview bumps the visit
counter. Acceptable. (If you ever want owner-preview recognition, set
`SESSION_COOKIE_DOMAIN=".<domain>"` — it shares the auth cookie with every subdomain; fine
because our SPA is the only thing rendering there, but strictly wider. Not doing it now.)

## Affected files

| File                               | Change                                                                                                 |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `backend/lukehirsch/settings.py`   | `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS` widen to the wildcard base domain.                     |
| `config/nginx.conf`                | Full prod server blocks: apex / `app.` / `*.` (documentation, you apply it).                           |
| `frontend/vite.config.ts`          | Dev server accepts `*.localhost` and the proxy **preserves the Host** so the subdomain reaches Django. |
| `frontend/.env` (+ `.env.example`) | `VITE_BASE_DOMAIN` (used by guide 3's host parsing).                                                   |
| deploy notes                       | wildcard DNS record + wildcard TLS cert (certbot DNS-01).                                              |

---

## The code

### 1. `settings.py` — trust the wildcard

Replace `ALLOWED_HOSTS` (`:19`):

```python
# A leading dot matches the base domain AND every subdomain (app., <handle>.). Dev:
# ".localhost" covers localhost + jane.localhost. Prod: ".luke-hirsch.de".
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", [f".{os.getenv('BASE_DOMAIN', 'localhost')}"])
```

> `BASE_DOMAIN` is read again here rather than importing it, because `ALLOWED_HOSTS` sits at
> the top of the file (`:19`) and `BASE_DOMAIN` is defined near the bottom. Alternatively
> move the `BASE_DOMAIN =` line up above `ALLOWED_HOSTS` and use it directly — cleaner; do
> that if you don't mind the reorder.

Widen CSRF + CORS (`:209-214`). Django 4+ accepts a wildcard in `CSRF_TRUSTED_ORIGINS`; CORS
uses a regex for the dev subdomains:

```python
# CORS — the React dev server (same-origin in prod via nginx; cross-origin only in dev).
CORS_ALLOWED_ORIGINS = [FRONTEND_URL]
CORS_ALLOWED_ORIGIN_REGEXES = [r"^http://[a-z0-9-]+\.localhost:5173$"]
CORS_ALLOW_CREDENTIALS = True

# CSRF — trust the app host (where auth happens) across the wildcard. Anonymous public
# POSTs (rank/intro) are exempt (DRF enforces CSRF only for authenticated requests).
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    [FRONTEND_URL, f"https://*.{os.getenv('BASE_DOMAIN', 'localhost')}"],
)
```

### 2. `nginx.conf` — the host split (prod)

Replace the commented notes in `config/nginx.conf` with three server blocks. `upstream app`
is the Django/daphne process. Wildcard `*.luke-hirsch.de` is matched **after** the exact
`app.` block, so `app.` never falls into the handle block.

```nginx
upstream app { server 127.0.0.1:8000; }

# ── apex: the Django-rendered SEO landing + legacy QR 301 ───────────────────────
server {
    listen 443 ssl;
    server_name luke-hirsch.de www.luke-hirsch.de;
    ssl_certificate     /etc/letsencrypt/live/luke-hirsch.de/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/luke-hirsch.de/privkey.pem;

    location = /            { proxy_pass http://app; include proxy_params; }
    location = /health/     { proxy_pass http://app; include proxy_params; }
    location /portfolio/    { proxy_pass http://app; include proxy_params; }  # legacy 301
    location /api/          { proxy_pass http://app; include proxy_params; }
    location /admin/        { proxy_pass http://app; include proxy_params; }
    location /_allauth/     { proxy_pass http://app; include proxy_params; }
    location /media/        { alias /path/to/backend/media/; }
    location / { return 302 https://app.luke-hirsch.de$request_uri; }  # no SPA on apex
}

# ── app: the authed SPA + its same-origin API ──────────────────────────────────
server {
    listen 443 ssl;
    server_name app.luke-hirsch.de;
    ssl_certificate     /etc/letsencrypt/live/luke-hirsch.de/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/luke-hirsch.de/privkey.pem;
    root /path/to/frontend/dist;

    location /api/      { proxy_pass http://app; include proxy_params; }
    location /_allauth/ { proxy_pass http://app; include proxy_params; }
    location /admin/    { proxy_pass http://app; include proxy_params; }
    location /ws/       { proxy_pass http://app; include proxy_params;
                          proxy_http_version 1.1;
                          proxy_set_header Upgrade $http_upgrade;
                          proxy_set_header Connection "upgrade"; }
    location /media/    { alias /path/to/backend/media/; }
    location / { try_files $uri /index.html; }
}

# ── handles: <user>.luke-hirsch.de — the public portfolios (noindex) ────────────
server {
    listen 443 ssl;
    server_name *.luke-hirsch.de;   # wildcard: matched AFTER app. above
    ssl_certificate     /etc/letsencrypt/live/luke-hirsch.de/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/luke-hirsch.de/privkey.pem;
    root /path/to/frontend/dist;
    add_header X-Robots-Tag "noindex" always;

    # Same-origin API: the SPA on <handle>.host calls /api on the SAME host, so Django's
    # resolve_owner() reads the handle from the Host header. proxy_params must forward Host.
    location /api/   { proxy_pass http://app; include proxy_params; }
    location /media/ { alias /path/to/backend/media/; }
    location / { try_files $uri /index.html; }
}
```

`proxy_params` must forward the real Host (standard Debian/nginx `proxy_params` does:
`proxy_set_header Host $http_host;`). Verify it — the whole design hinges on Django seeing
`<handle>.luke-hirsch.de` in the Host header. Add a `:80 → :443` redirect server block as
usual (omitted for brevity).

### 3. Wildcard DNS + TLS

- **DNS:** add a wildcard A/AAAA record `*.luke-hirsch.de` → the server IP (alongside the
  existing apex + `app` records, or just the wildcard + apex).
- **TLS:** a wildcard cert needs DNS-01 (HTTP-01 can't validate `*`). With certbot + your
  DNS provider's plugin (registrar-specific; Strato/most providers have one or support
  manual DNS-01):

  ```bash
  certbot certonly --preferred-challenges dns \
    -d luke-hirsch.de -d '*.luke-hirsch.de'
  ```

  One cert covers apex + all subdomains. Renewal is the same DNS-01 flow (automate with the
  provider plugin; manual DNS-01 won't auto-renew).

### 4. Dev: `*.localhost` + Host-preserving proxy

`frontend/vite.config.ts` — accept subdomain hosts and **keep the Host header** through the
proxy (this is the part that makes owner-by-host work in dev):

```ts
  server: {
    host: true,               // listen on 0.0.0.0 so jane.localhost:5173 resolves
    allowedHosts: [".localhost"],
    proxy: {
      // changeOrigin:false (the default) forwards the ORIGINAL Host to Django, so
      // jane.localhost:5173 -> Django sees Host: jane.localhost:5173 -> owner = jane.
      "/api":       { target: BACKEND, changeOrigin: false },
      "/_allauth":  { target: BACKEND, changeOrigin: false },
      "/admin":     { target: BACKEND, changeOrigin: false },
      "/media":     { target: BACKEND, changeOrigin: false },
      "/static":    { target: BACKEND, changeOrigin: false },
      "/ws":        { target: BACKEND, ws: true, changeOrigin: false },
    },
  },
```

`frontend/.env` (and `.env.example`):

```
VITE_BASE_DOMAIN=localhost
```

(prod build sets `VITE_BASE_DOMAIN=luke-hirsch.de`.) Guide 3 reads this to parse the handle
from `window.location.hostname`.

Dev hosts you'll use:

- `localhost:5173` — apex (guide 3 redirects it to the app in dev).
- `app.localhost:5173` — the authed tool (log in here).
- `lukas.localhost:5173`, `jane.localhost:5173` — public portfolios.

Modern browsers resolve `*.localhost` to loopback automatically; if yours doesn't, add
`127.0.0.1 app.localhost lukas.localhost jane.localhost` to `/etc/hosts`.

Backend dev env: set `BASE_DOMAIN=localhost` (the default) and run
`python manage.py runserver 0.0.0.0:8000`.

---

## Tests

No new unit tests: this guide is DNS/TLS/nginx/dev config, and its only Python logic
(`owner_for_host`) is already covered red→green by guide 1's `OwnerForHostTests`. Flagged per
the skill — infra is verified by the steps below, not a suite.

## Verification

1. **Dev, backend host resolution through the proxy:** `runserver` + `npm run dev`; open
   `http://lukas.localhost:5173` and `http://jane.localhost:5173` (after creating a `jane`
   user with content in guide 1) — the network tab shows `/api/spa/portfolio/native/meta/`
   returning **different** domain lists per host. This proves the Host survives the proxy.
2. `curl -H 'Host: jane.localhost' localhost:8000/api/spa/portfolio/native/meta/` → jane's
   domains; `-H 'Host: app.localhost'` → 404 (not a portfolio host).
3. **Dev app host:** log in at `http://app.localhost:5173` — session cookie is set on
   `app.localhost`; the authed routes work; reload keeps you logged in.
4. **Prod:** after DNS + cert + nginx reload — `https://app.luke-hirsch.de` serves the SPA;
   `https://luke-hirsch.de` serves the Django landing; `https://<yourhandle>.luke-hirsch.de`
   serves the portfolio; `https://nobody.luke-hirsch.de` → the SPA renders a "not found"
   (the API 404s). TLS is valid on all (one wildcard cert).
5. `curl -I https://<handle>.luke-hirsch.de` shows `X-Robots-Tag: noindex`.

## Results

_(human fills after testing)_
can
