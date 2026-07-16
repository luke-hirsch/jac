"""Cached HirschAI reachability probe.

One cheap GET against Ollama's /api/tags with a short timeout, cached for
PROBE_MAX_AGE_S so the executors endpoint / auto-run checks don't hammer the tower.
`refresh=True` busts the cache (the live prompt tests use it)."""

import time
from urllib import request

from .conf import hirschai_row

PROBE_TIMEOUT_S = 2.0
PROBE_MAX_AGE_S = 30.0

_CACHE = {"ts": 0.0, "ok": False}


def hirschai_reachable(*, refresh: bool = False) -> bool:
    now = time.monotonic()
    if not refresh and now - _CACHE["ts"] < PROBE_MAX_AGE_S:
        return _CACHE["ok"]
    try:
        base = hirschai_row().url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")].rstrip("/")
        with request.urlopen(base + "/api/tags", timeout=PROBE_TIMEOUT_S) as resp:
            ok = resp.status == 200
    except Exception:  # noqa: BLE001 — any failure = not reachable
        ok = False
    _CACHE["ts"] = now
    _CACHE["ok"] = ok
    return ok
