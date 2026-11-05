"""What we know about the model server, without asking it.

Page loads never touch the model server: public pages get bots, and a server that starts on demand
would wake for every one of them. `hirschai_state` only reports what real AI calls found out, so
the panel can say "answered a minute ago" or "last call failed" at no cost.

`hirschai_reachable` is the one live check, for an operator who means it (`llm_check`, the live
prompt tests): one cheap GET against Ollama's /api/tags with a short timeout.
"""

from urllib import request

from django.core.cache import cache

PROBE_TIMEOUT_S = 2.0
# How long one real call's outcome is worth repeating. Longer than an idle model stays loaded.
STATE_TTL_S = 15 * 60

_STATE_KEY = "llm:hirschai:last-call-ok"


def note_hirschai_answered() -> None:
    _note(True)


def note_hirschai_failed() -> None:
    _note(False)


def _note(ok: bool) -> None:
    try:
        cache.set(_STATE_KEY, ok, STATE_TTL_S)
    except Exception:  # noqa: BLE001, S110 — a status note must never break an AI call
        pass


def hirschai_state() -> bool | None:
    """True: the last real call got an answer. False: it failed. None: no call lately, so nobody
    knows, and that's normal."""
    try:
        return cache.get(_STATE_KEY)
    except Exception:  # noqa: BLE001 — the state is a nicety, the page must still load
        return None


def hirschai_base_url() -> str:
    """The model server's address, with or without the /v1 it was configured with."""
    from .conf import resolve_config

    base = resolve_config("ollama")["url"].rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    return base


def hirschai_reachable() -> bool:
    """Ask the model server right now. Sends this process's key, like every other call."""
    from .conf import resolve_config

    config = resolve_config("ollama")
    headers = {}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config['api_key']}"
    req = request.Request(hirschai_base_url() + "/api/tags", headers=headers)
    try:
        with request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 — any failure = not reachable
        return False
