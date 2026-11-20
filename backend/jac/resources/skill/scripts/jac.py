#!/usr/bin/env python3
"""Talk to jac from the Claude skill. Standard library only: it runs in Claude's sandbox.

    python3 scripts/jac.py career              the career DB as JSON
    python3 scripts/jac.py applications        the latest applications
    python3 scripts/jac.py show ID             one application, in the shape `save` takes
    python3 scripts/jac.py save FILE           create the application in FILE, or update it
                                               when FILE carries an "id" (written on create)

Where jac is and the token: JAC_URL / JAC_TOKEN from the environment (Claude Code), else
config.json next to SKILL.md (the zip jac hands out). When jac can't be reached from here,
`career` falls back to career.json, the snapshot in the zip, and `save` says how to import
the file in jac instead. Exit codes: 0 ok, 1 jac refused (the message says why), 2 offline.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TIMEOUT_S = 30
OFFLINE = 2


class Offline(Exception):
    """jac isn't reachable from this sandbox: no network, or the egress proxy said no."""


class Refused(Exception):
    """jac answered and said no: a bad token, a validation error, a missing application."""


def config() -> tuple[str, str]:
    url, token = os.environ.get("JAC_URL", ""), os.environ.get("JAC_TOKEN", "")
    path = SKILL_DIR / "config.json"
    if (not url or not token) and path.exists():
        data = json.loads(path.read_text())
        url, token = url or data.get("url", ""), token or data.get("token", "")
    if not url or not token:
        raise Refused(
            "No jac address or token: set JAC_URL and JAC_TOKEN, or download the skill "
            "again from jac (Account → Claude)."
        )
    return url.rstrip("/"), token


def call(method: str, path: str, body: dict | None = None):
    url, token = config()
    request = urllib.request.Request(
        f"{url}/api/jac/skill/{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "jac-claude-skill/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            detail = json.loads(raw)
        except ValueError:
            # Not jac's JSON. Claude's egress allowlist answers 403, a gateway in front of a
            # jac that's down answers 5xx: both mean "not reachable right now".
            if err.code == 403 or err.code >= 500:
                raise Offline(f"HTTP {err.code} from something that isn't jac") from err
            raise Refused(
                f"HTTP {err.code} from {request.full_url}, which isn't jac's API: "
                "check the address in config.json or JAC_URL."
            ) from err
        if err.code == 401:
            raise Refused(
                "jac doesn't accept the token (revoked or wrong). Download the skill again "
                "from jac (Account → Claude)."
            ) from err
        raise Refused(
            f"jac answered {err.code}: {json.dumps(detail, ensure_ascii=False)}"
        ) from err
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise Offline(str(getattr(err, "reason", err))) from err


def show(data) -> None:
    print(json.dumps(data, indent=1, ensure_ascii=False))


def offline_note(reason: str) -> str:
    try:
        url = config()[0]
    except Refused:
        url = "jac"
    return (
        f"OFFLINE: {url} can't be reached from here ({reason}). In the Claude app, allow "
        "code execution to reach that host (Settings → Capabilities, network access)."
    )


def cmd_career(_args) -> int:
    try:
        show(call("GET", "career/"))
        return 0
    except Offline as err:
        snapshot = SKILL_DIR / "career.json"
        if not snapshot.exists():
            print(offline_note(str(err)), file=sys.stderr)
            return OFFLINE
        data = json.loads(snapshot.read_text())
        print(
            f"{offline_note(str(err))}\nUsing the snapshot in the skill, taken "
            f"{data.get('generated_at', '?')}. Saving works by import: see `save`.",
            file=sys.stderr,
        )
        show(data)
        return 0


def cmd_applications(_args) -> int:
    show(call("GET", "applications/"))
    return 0


def cmd_show(args) -> int:
    if len(args) != 1 or not args[0].isdigit():
        raise Refused("Usage: jac.py show ID")
    show(call("GET", f"applications/{args[0]}/"))
    return 0


def cmd_save(args) -> int:
    if len(args) != 1:
        raise Refused("Usage: jac.py save FILE")
    path = Path(args[0])
    data = json.loads(path.read_text())
    app_id = data.pop("id", None)
    try:
        if app_id:
            result = call("PATCH", f"applications/{int(app_id)}/", data)
        else:
            result = call("POST", "applications/", data)
    except Offline as err:
        print(
            f"{offline_note(str(err))}\nNothing was saved. Give the user {path.name} to "
            "download and import in jac: Applications → Import from Claude.",
            file=sys.stderr,
        )
        return OFFLINE
    # The next save of this file updates the same application instead of making another.
    path.write_text(
        json.dumps({"id": result["id"], **data}, indent=1, ensure_ascii=False)
    )
    show(
        {
            "id": result["id"],
            "url": result["url"],
            "title": result.get("title", ""),
            "warnings": result.get("warnings", []),
        }
    )
    return 0


COMMANDS = {
    "career": cmd_career,
    "applications": cmd_applications,
    "show": cmd_show,
    "save": cmd_save,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        return 1
    try:
        return COMMANDS[argv[0]](argv[1:])
    except Refused as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    except Offline as err:
        print(offline_note(str(err)), file=sys.stderr)
        return OFFLINE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
