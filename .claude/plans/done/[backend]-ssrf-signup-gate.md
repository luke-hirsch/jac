# [backend] Close SSRF via user LLM URLs + gate open signup

> **Branch:** `backend/bugfixes` (shared). Tests land red first.

## Context / goal

> **Posture (corrected 2026-07-08):** this is **not** a private tool. The portfolio is public, it
> renders from the same career-DB entries, and the plan is to eventually open the CV tool itself as
> a public showcase ("create your own CV here!"). That *sharpens* both halves of this guide rather
> than softening them: open signup is a **feature on the roadmap**, so every authenticated-user
> attack surface must be treated as internet-facing — SSRF validation is mandatory, not prudent.
> The signup gate is a **launch toggle** (closed until the multi-tenant showcase is actually ready),
> not a privacy stance.

Two issues that compound each other:

1. **Ungated signup.** `ACCOUNT_SIGNUP_FIELDS` + allauth headless means *anyone* can create an
   account **today**, before the public showcase is built and before multi-tenant hardening (rate
   limits, LLM cost caps, SSRF fixes) has landed. There's no `is_open_for_signup` gate to hold
   registration closed until launch.
2. **SSRF through user-controlled LLM URLs.** Any authenticated user can create an `LLMConfig` with
   `provider=custom` or `ollama` and point `url` at anything. The server then POSTs
   attacker-controlled JSON to that URL and returns the response body through the completion
   (`custom.py::_request`, `ollama.py::_request`). That's a server-side-request-forgery read
   primitive against `localhost`, the Redis/Valkey port, cloud metadata endpoints (`169.254.169.254`),
   and other internal services. Since open signup is the *destination*, "an authenticated user"
   **means** "the internet" — this must hold at showcase scale, not just for one trusted user.

Goal: (a) make signup opt-in via a settings flag, defaulting **closed** — a launch toggle flipped
when the showcase ships; (b) validate the `url` on `LLMConfig` writes — reject non-`http(s)` schemes
and hosts that resolve into private / loopback / link-local / metadata ranges — at the serializer
boundary and on the model, so the admin form is covered too.

> Residual risk noted, not solved here: a hostname that passes validation but re-resolves to a
> private IP at request time (DNS rebinding) still slips through. While signup stays closed the
> config-time host check is an acceptable bar; a request-time pinned-IP check graduates to a
> **pre-launch blocker** the moment `ACCOUNT_ALLOW_SIGNUPS` is flipped for the public showcase —
> alongside per-user rate limits and LLM spend caps (neither is in this guide's scope; recorded in
> the "before opening signup" checklist at the bottom so flipping the flag is never a one-liner).

## Affected files

| path | why |
| --- | --- |
| `backend/lukehirsch/settings.py` | add `ACCOUNT_ALLOW_SIGNUPS = env_bool("ACCOUNT_ALLOW_SIGNUPS", False)` |
| `backend/lukehirsch/adapter.py` | override `is_open_for_signup` on the existing custom adapter |
| `backend/llm_connector/validators.py` | **new** — `validate_safe_llm_url(url)` (scheme + resolved-IP range check) |
| `backend/llm_connector/serializers.py` | call the validator in `LLMConfigSerializer.validate` (only for url-bearing providers) |
| `backend/llm_connector/models.py` | call the validator in `LLMConfig.clean()` so admin writes are covered |
| `backend/llm_connector/tests/test_validators.py` | **new (test)** — URL validator unit tests |
| `backend/llm_connector/tests/test_api.py` | **(test)** — serializer rejects an internal URL on create |
| `backend/spa/tests/test_auth.py` | **(test)** — `is_open_for_signup` reflects the flag |

## The code

### 1. `backend/lukehirsch/settings.py`

Under the allauth account settings block (near line 231), add:

```python
# Launch toggle: signup stays closed until the public CV-showcase (open registration) actually
# ships WITH its multi-tenant hardening — see the "before opening signup" checklist in
# .claude/plans/to-do/[backend]-ssrf-signup-gate.md. Flip via env when that day comes.
ACCOUNT_ALLOW_SIGNUPS = env_bool("ACCOUNT_ALLOW_SIGNUPS", False)
```

(`env_bool` comes from the settings-hardening guide — landed as `lukehirsch/prod.py`, so the import
is `from lukehirsch.prod import env_bool`, already present in `settings.py`.)

### 2. `backend/lukehirsch/adapter.py`

Add the override to `HarassmentResistantAccountAdapter`:

```python
from django.conf import settings
```

```python
    def is_open_for_signup(self, request) -> bool:
        """Gate registration behind ACCOUNT_ALLOW_SIGNUPS (default False). This is a launch
        toggle, not a privacy stance: the flag opens when the public CV showcase ships. allauth
        calls this on both the classic and headless signup paths, so one override closes both."""
        return bool(getattr(settings, "ACCOUNT_ALLOW_SIGNUPS", False))
```

### 3. New file — `backend/llm_connector/validators.py`

```python
"""Validate user-supplied LLM endpoint URLs to blunt SSRF.

`custom` / `ollama` configs let a user set an arbitrary `url` the server will POST to. Without
this, that's a server-side-request-forgery primitive against internal services (localhost, the
Redis port, cloud metadata at 169.254.169.254, …). We allow only http(s) to hosts that do NOT
resolve into private / loopback / link-local / reserved ranges.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from django.core.exceptions import ValidationError


def _is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable -> treat as unsafe
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_safe_llm_url(url: str) -> None:
    """Raise ValidationError unless `url` is http(s) to a public host.

    Resolves the hostname and rejects if ANY resolved address is in a blocked range (so a name
    that maps to 127.0.0.1 or a metadata IP is caught). Empty url is allowed — callers only pass
    url-bearing providers here.
    """
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("LLM url must use http or https.")
    host = parsed.hostname
    if not host:
        raise ValidationError("LLM url must include a host.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValidationError(f"LLM url host does not resolve: {host}") from exc
    for info in infos:
        ip = info[4][0]
        if _is_blocked_ip(ip):
            raise ValidationError(
                f"LLM url resolves to a non-public address ({ip}); refusing to store it."
            )
```

### 4. `backend/llm_connector/serializers.py`

Providers whose adapter dials a user URL are `custom` and `ollama`. Add a `validate` to
`LLMConfigSerializer`:

```python
from llm_connector.validators import validate_safe_llm_url
```

```python
    # providers whose adapter POSTs to a user-supplied url (SSRF surface)
    _URL_PROVIDERS = {"custom", "ollama"}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        provider = attrs.get("provider") or getattr(self.instance, "provider", None)
        url = attrs.get("url", getattr(self.instance, "url", ""))
        if provider in self._URL_PROVIDERS and url:
            try:
                validate_safe_llm_url(url)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"url": list(exc.messages)})
        return attrs
```

Add the import for the Django exception alias at the top:

```python
from django.core.exceptions import ValidationError as DjangoValidationError
```

### 5. `backend/llm_connector/models.py`

Belt-and-braces so the admin form and any direct `full_clean()` are covered:

```python
from django.core.exceptions import ValidationError
from .validators import validate_safe_llm_url
```

```python
    def clean(self):
        super().clean()
        if self.provider in ("custom", "ollama") and self.url:
            validate_safe_llm_url(self.url)  # raises ValidationError on an internal host
```

> DRF doesn't call `full_clean`, which is why the serializer check (step 4) is the real API gate;
> `clean()` covers the admin. The admin's `ModelForm` calls `full_clean`, so no extra admin wiring.

## Tests

- `backend/llm_connector/tests/test_validators.py` (**new**): `validate_safe_llm_url` passes a public
  `https://api.example.com`; raises for `http://localhost`, `http://127.0.0.1`,
  `http://169.254.169.254`, `http://10.0.0.5`, a `file://` scheme, and a bare host. Loopback/private
  literals need no DNS; for the hostname→internal case, patch `socket.getaddrinfo` to return
  `127.0.0.1` so the test is offline and deterministic.
- `backend/llm_connector/tests/test_api.py` (**append**): authenticated `POST /api/llm/configs/` with
  `provider=ollama, url=http://localhost:11434/v1` returns `400` with a `url` error; a public URL
  (patch `getaddrinfo` to a public IP) succeeds.
- `backend/spa/tests/test_auth.py` (**append**): `HarassmentResistantAccountAdapter().is_open_for_signup(req)`
  is `False` by default and `True` under `override_settings(ACCOUNT_ALLOW_SIGNUPS=True)`.

Run:

```bash
cd backend && python manage.py test llm_connector.tests.test_validators \
  llm_connector.tests.test_api spa.tests.test_auth
```

## Verification

```bash
cd backend
python manage.py test llm_connector spa      # green
```

- As a logged-in user, `POST /api/llm/configs/` with `{"alias":"x","provider":"ollama",
  "model":"llama3","url":"http://127.0.0.1:11434/v1"}` → `400`, body `{"url": [...]}`.
- Same with a real public endpoint → `201`.
- With `ACCOUNT_ALLOW_SIGNUPS` unset, the headless signup endpoint refuses new registrations; set
  `ACCOUNT_ALLOW_SIGNUPS=true` and signup works again.
- Existing legit local dev: a developer running against a local Ollama sets
  `ACCOUNT_ALLOW_SIGNUPS`/keeps their own config; note that `url=http://localhost` is now rejected by
  the API. **If local-Ollama-via-API is a needed dev workflow, add an escape hatch** (e.g. allow
  loopback when `DEBUG`) — call it out to the human as a decision. The server's own zero-cost
  default is unaffected either way: it lives in `settings.LLM["default"]` (a settings dict the
  resolver falls back to), never as an `LLMConfig` row, so the validator never sees it.

**Done looks like:** signup is closed unless explicitly opened, and no `LLMConfig` pointing at an
internal address can be stored through the API or admin.

## Before opening signup (pre-launch checklist — NOT in this guide's scope)

Flipping `ACCOUNT_ALLOW_SIGNUPS` exposes every authenticated-user surface to the internet. These
must land first; recorded here so the flip is never treated as a one-line change:

- **DNS-rebinding closure** — request-time pinned-IP check (resolve once, connect to the vetted IP)
  in `custom.py`/`ollama.py::_request`; the config-time check in this guide is bypassable by a
  re-resolving hostname.
- **Per-user rate limits + LLM spend caps** — public users driving the generation pipeline burn
  server-side LLM budget (`LLMRequestLog` already records usage; nothing enforces a ceiling). The
  showcase thesis (self-hosted small models, [[project-purpose-cv-showcase]]) helps here — `light`
  runs on the server's own Ollama are near-zero marginal cost — but commercial-provider aliases and
  the WS/Celery queue still need throttles.
- **Tenant review of "system" rows** — `Domain`/`ApplicationLayout` system defaults are shared
  read-only rows; confirm nothing else leaks across users at showcase scale.
