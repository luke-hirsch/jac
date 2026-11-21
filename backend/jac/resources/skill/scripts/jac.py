#!/usr/bin/env python3
"""Work with jac from the Claude skill. Standard library only: it runs in Claude's sandbox.

Two modes, chosen by the environment:

- **File mode** (the Claude app, whose sandbox can't reach jac): the career comes from the career
  file the user attached to the chat (jac: Account → LLM → Claude → Download career for Claude).
  `save` and `apply` check the file Claude wrote against it and hand it over for the user to
  import in jac.
- **Online mode** (Claude Code): JAC_URL and JAC_TOKEN are set (jac: Account → LLM → Claude),
  and every command talks to jac.

    python3 scripts/jac.py career              the career DB as JSON
    python3 scripts/jac.py review              the machine review of the career, as JSON
    python3 scripts/jac.py save FILE           file: check the application in FILE for the import
                                               online: create it, or update it when FILE carries
                                               an "id" (written on create)
    python3 scripts/jac.py apply FILE [--check]
                                               file: check the career changes in FILE for the
                                               import; online: apply them (--check only
                                               validates them and shows the result)
    python3 scripts/jac.py applications        online only: the latest applications
    python3 scripts/jac.py show ID             online only: one application, in the shape `save`
                                               takes
    python3 scripts/jac.py changes             online only: the latest batches of career changes
    python3 scripts/jac.py undo ID             online only: put a batch's entries back

The career file is JAC_CAREER_FILE if that's set, else the newest JSON file marked
"format": "jac-career-for-claude" among the chat's files (/mnt/user-data) or in the current
folder: the name doesn't matter. Exit codes: 0 ok, 1 refused (the message says why), 2 not
possible here (file mode without jac, or jac unreachable).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT_S = 30
NOT_HERE = 2

CAREER_FORMAT = "jac-career-for-claude"
# Where the Claude app keeps a chat's files; what lands in outputs/ the user can download.
USER_DATA = Path("/mnt/user-data")
OUTPUTS = USER_DATA / "outputs"
MAX_SCAN_BYTES = 20 * 1024 * 1024

# jac's own rules, checked here before the user imports: the letter band, the batch size and the
# names a change may use.
LETTER_WORDS = (170, 230)
MAX_CHANGES = 100
SECTIONS = ("jobs", "educations", "projects", "skills", "certifications", "languages")
# Per type, the fields `set` takes and the lists `add` / `remove` take. The rest of what `career`
# shows is read-only.
SET_FIELDS = {
    "job": {
        "title",
        "company",
        "job_type",
        "started",
        "ended",
        "url",
        "description",
        "favourite",
    },
    "project": {"name", "started", "ended", "url", "description", "favourite", "job"},
    "education": {
        "institution",
        "field_of_study",
        "started",
        "ended",
        "degree",
        "degree_level",
        "grade",
        "description",
        "favourite",
    },
    "certification": {
        "name",
        "issuer",
        "issued_on",
        "expires_on",
        "credential_id",
        "url",
        "description",
        "favourite",
    },
    "skill": {
        "name",
        "proficiency",
        "category",
        "first_used",
        "description",
        "favourite",
        "certification",
    },
    "language": {"name", "fluency", "description", "favourite", "certification"},
}
LIST_FIELDS = {
    "job": {"skills", "domains"},
    "project": {"skills", "domains"},
    "education": {"skills", "domains"},
    "certification": {"skills", "domains"},
    "skill": {"domains", "related_skills", "builds_on"},
    "language": set(),
}


class NotHere(Exception):
    """What was asked needs jac itself: unreachable, or file mode."""


class Refused(Exception):
    """jac, or the check against the career file, said no. The message says why."""


def online() -> bool:
    return bool(os.environ.get("JAC_URL") and os.environ.get("JAC_TOKEN"))


def show(data) -> None:
    print(json.dumps(data, indent=1, ensure_ascii=False))


def note(text: str) -> None:
    print(text, file=sys.stderr)


# ---- online --------------------------------------------------------------------------------


def call(method: str, path: str, body: dict | None = None):
    url = os.environ["JAC_URL"].rstrip("/")
    request = urllib.request.Request(
        f"{url}/api/jac/skill/{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['JAC_TOKEN']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "jac-claude-skill/2",
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
            # Not jac's JSON. An egress proxy answers 403, a gateway in front of a jac that's
            # down answers 5xx: both mean "not reachable right now".
            if err.code == 403 or err.code >= 500:
                raise NotHere(
                    unreachable(f"HTTP {err.code} from something that isn't jac")
                ) from err
            raise Refused(
                f"HTTP {err.code} from {request.full_url}, which isn't jac's API: check "
                "JAC_URL."
            ) from err
        if err.code == 401:
            raise Refused(
                "jac doesn't accept the token (revoked or wrong). Create a new one in jac: "
                "Account → LLM → Claude."
            ) from err
        raise Refused(
            f"jac answered {err.code}: {json.dumps(detail, ensure_ascii=False)}"
        ) from err
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise NotHere(unreachable(str(getattr(err, "reason", err)))) from err


def unreachable(reason: str) -> str:
    return (
        f"jac can't be reached at {os.environ.get('JAC_URL', '?')} ({reason}). Check "
        "JAC_URL, or unset JAC_URL and JAC_TOKEN to work from a career file."
    )


# ---- file mode -----------------------------------------------------------------------------


def is_career_file(path: Path) -> bool:
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("format") == CAREER_FORMAT


def find_career_file() -> Path:
    explicit = os.environ.get("JAC_CAREER_FILE")
    if explicit:
        path = Path(explicit).expanduser()
        if not is_career_file(path):
            raise Refused(
                f"JAC_CAREER_FILE names {path}, which isn't a career file from jac "
                "(Account → LLM → Claude → Download career for Claude)."
            )
        return path
    found: list[Path] = []
    if USER_DATA.is_dir():
        found += [p for p in USER_DATA.rglob("*.json") if is_career_file(p)]
    found += [p for p in Path.cwd().glob("*.json") if is_career_file(p)]
    if not found:
        raise Refused(
            "No career file in this chat. Ask the user to attach one: in jac, Account → "
            "Claude → Download career for Claude, then add the file to the chat. If it is "
            "attached already, run the command again with JAC_CAREER_FILE=<its path>."
        )
    return max(found, key=lambda p: p.stat().st_mtime)


_career: tuple[Path, dict] | None = None


def career_file() -> tuple[Path, dict]:
    global _career
    if _career is None:
        path = find_career_file()
        _career = (path, json.loads(path.read_text(encoding="utf-8")))
    return _career


def entries_by_id(career: dict) -> dict[str, dict]:
    return {
        entry["id"]: entry
        for rows in career.get("entries", {}).values()
        for entry in rows
    }


def hand_over(path: Path) -> Path:
    """Put the file where the user can download it: the chat's outputs in the Claude app."""
    if OUTPUTS.is_dir() and path.resolve().parent != OUTPUTS.resolve():
        target = OUTPUTS / path.name
        shutil.copyfile(path, target)
        return target
    return path


def check_application(data, career: dict) -> tuple[list[str], list[str]]:
    """What jac's import would refuse (problems), and what it would warn about."""
    problems: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["The file isn't a JSON object."], []
    posting = data.get("posting") or {}
    if not isinstance(posting, dict) or not str(posting.get("text", "")).strip():
        problems.append("posting.text is missing: it's the full posting text.")
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        problems.append("entries is missing: the CV's entry ids, most relevant first.")
        entries = []
    known = entries_by_id(career)
    unknown = [e for e in entries + list(data.get("pinned") or []) if e not in known]
    if unknown:
        problems.append(f"Not in the career file: {', '.join(map(str, unknown))}.")
    off = [s for s in data.get("sections_off") or [] if s not in SECTIONS]
    if off:
        problems.append(
            f"Unknown sections in sections_off: {', '.join(map(str, off))}."
        )
    letter = data.get("letter") or {}
    body = str(letter.get("body", "")) if isinstance(letter, dict) else ""
    if not body.strip():
        problems.append("letter.body is missing.")
    else:
        words = len(body.split())
        low, high = LETTER_WORDS
        if not low <= words <= high:
            warnings.append(
                f"The letter body has {words} words; jac aims for {low}–{high}."
            )
    highest = career.get("highest_degree")
    if highest and highest not in entries:
        warnings.append(
            f"jac adds the highest completed degree ({highest}) on import: it's always kept."
        )
    if data.get("id"):
        warnings.append(
            'The file carries an "id": an import always creates a new application.'
        )
    return problems, warnings


def name_problems(where: str, etype: str, change: dict) -> list[str]:
    """The names in a change the import doesn't take for its type: a read-only one `career`
    shows (`years`, `completed`), or one that isn't jac's at all."""
    fields, lists = SET_FIELDS.get(etype, set()), LIST_FIELDS.get(etype, set())
    problems = []
    for field in change.get("set") or {}:
        if field in lists:
            problems.append(f"{where}: {field} is a list: change it with add / remove.")
        elif field not in fields:
            problems.append(
                f"{where}: a {etype} can't set {field}; it sets "
                f"{', '.join(sorted(fields))}."
            )
    for verb in ("add", "remove"):
        for field in change.get(verb) or {}:
            if field not in lists:
                problems.append(
                    f"{where}: a {etype} has no list {field} to {verb} (its lists: "
                    f"{', '.join(sorted(lists)) or 'none'})."
                )
    return problems


def check_changes(data, career: dict) -> list[str]:
    """What jac's import would refuse, checked against the career file the batch was made from."""
    if not isinstance(data, dict) or not isinstance(data.get("changes"), list):
        return ['The file needs a "changes" list.']
    changes = data["changes"]
    if not 1 <= len(changes) <= MAX_CHANGES:
        return [f"A batch has 1 to {MAX_CHANGES} changes; this one has {len(changes)}."]
    known = entries_by_id(career)
    refs = {c.get("ref") for c in changes if isinstance(c, dict) and c.get("new")}
    problems: list[str] = []
    seen_ids: set[str] = set()
    for n, change in enumerate(changes):
        where = f"Change {n}"
        if not isinstance(change, dict):
            problems.append(f"{where} isn't an object.")
            continue
        if change.get("new"):
            if change["new"] != "skill":
                problems.append(f"{where}: only skills can be created.")
            if not change.get("ref"):
                problems.append(f"{where}: a new skill needs a ref.")
            etype = "skill"
        else:
            entry_id = change.get("id")
            if entry_id not in known:
                problems.append(f"{where}: {entry_id} isn't in the career file.")
                continue
            if entry_id in seen_ids:
                problems.append(f"{where}: a second change to {entry_id} in one batch.")
            seen_ids.add(entry_id)
            if change.get("seen") != known[entry_id].get("updated_at"):
                problems.append(
                    f"{where}: seen must be {entry_id}'s updated_at as the career file "
                    f"shows it ({known[entry_id].get('updated_at')})."
                )
            etype = entry_id.split(":")[0]
        problems += name_problems(where, etype, change)
        for field in ("job", "certification"):  # a single link, by id or null
            value = (change.get("set") or {}).get(field)
            if value is not None and value not in known:
                problems.append(f"{where}: {value} isn't in the career file.")
        for verb in ("add", "remove"):
            for field, values in (change.get(verb) or {}).items():
                if field == "domains":
                    continue
                for value in values or []:
                    if isinstance(value, str) and value.startswith("new:"):
                        if value[4:] not in refs:
                            problems.append(
                                f"{where}: no new skill with ref {value[4:]!r}."
                            )
                    elif value not in known:
                        problems.append(f"{where}: {value} isn't in the career file.")
    return problems


# ---- commands ------------------------------------------------------------------------------


def cmd_career(_args) -> int:
    if online():
        show(call("GET", "career/"))
        return 0
    path, data = career_file()
    note(
        f"FILE MODE: the career from {path.name}, taken {data.get('generated_at', '?')}. "
        "Saving goes by import: see `save` and `apply`."
    )
    show({k: v for k, v in data.items() if k not in ("format", "review")})
    return 0


def cmd_review(_args) -> int:
    if online():
        show(call("GET", "review/"))
        return 0
    path, data = career_file()
    note(
        f"FILE MODE: the review from {path.name}, taken {data.get('generated_at', '?')}."
    )
    show(data.get("review", {}))
    return 0


def cmd_save(args) -> int:
    if len(args) != 1:
        raise Refused("Usage: jac.py save FILE")
    path = Path(args[0])
    data = json.loads(path.read_text(encoding="utf-8"))
    if not online():
        problems, warnings = check_application(data, career_file()[1])
        if problems:
            raise Refused("The import would refuse it:\n- " + "\n- ".join(problems))
        show(
            {
                "file": str(hand_over(path)),
                "import": "jac: Applications → Import from Claude",
                "warnings": warnings,
            }
        )
        return 0
    app_id = data.pop("id", None)
    if app_id:
        result = call("PATCH", f"applications/{int(app_id)}/", data)
    else:
        result = call("POST", "applications/", data)
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


def cmd_apply(args) -> int:
    check = "--check" in args
    args = [a for a in args if a != "--check"]
    if len(args) != 1:
        raise Refused("Usage: jac.py apply FILE [--check]")
    path = Path(args[0])
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("applied_as"):
        raise Refused(
            f"{path.name} was applied already, as batch {data['applied_as']}. To take it "
            f"back: `undo {data['applied_as']}`. For more changes, write a new file."
        )
    if not online():
        problems = check_changes(data, career_file()[1])
        if problems:
            raise Refused(
                "The import would refuse the batch:\n- " + "\n- ".join(problems)
            )
        out = {"checked_only": check, "changes": len(data["changes"])}
        if not check:
            out["file"] = str(hand_over(path))
            out["import"] = "jac: Account → LLM → Claude → Import changes"
        show(out)
        return 0
    body = {"changes": data.get("changes", []), "dry_run": check}
    result = call("POST", "career/changes/", body)
    if not check:
        # A second apply of the same file would double up: mark it.
        path.write_text(
            json.dumps(
                {"applied_as": result["id"], **data}, indent=1, ensure_ascii=False
            )
        )
    show(
        {
            "id": result["id"],
            "checked_only": check,
            "changes": len(result["changes"]),
            "created": result.get("created", {}),
            "after": {c["id"]: c["after"] for c in result["changes"]},
        }
    )
    return 0


def online_only(where_in_jac: str):
    def decorate(command):
        def run(args) -> int:
            if not online():
                raise NotHere(
                    "This needs jac itself, and this sandbox can't reach it. "
                    f"In jac: {where_in_jac}."
                )
            return command(args)

        return run

    return decorate


@online_only("Applications lists them")
def cmd_applications(_args) -> int:
    show(call("GET", "applications/"))
    return 0


@online_only("open the application under Applications and edit it there")
def cmd_show(args) -> int:
    if len(args) != 1 or not args[0].isdigit():
        raise Refused("Usage: jac.py show ID")
    show(call("GET", f"applications/{args[0]}/"))
    return 0


@online_only("Account → LLM → Claude → Career changes lists the batches")
def cmd_changes(_args) -> int:
    batches = call("GET", "career/changes/")
    show(
        [
            {
                "id": b["id"],
                "created_at": b["created_at"],
                "source": b["source"],
                "undone_at": b["undone_at"],
                "changes": [
                    f"{c['id']} {c['label']}: {c['why']}" for c in b["changes"]
                ],
            }
            for b in batches
        ]
    )
    return 0


@online_only("Account → LLM → Claude → Career changes, Undo on the batch")
def cmd_undo(args) -> int:
    if len(args) != 1 or not args[0].isdigit():
        raise Refused("Usage: jac.py undo ID")
    result = call("POST", f"career/changes/{args[0]}/undo/")
    show({"id": result["id"], "undone_at": result["undone_at"]})
    return 0


COMMANDS = {
    "career": cmd_career,
    "review": cmd_review,
    "save": cmd_save,
    "apply": cmd_apply,
    "applications": cmd_applications,
    "show": cmd_show,
    "changes": cmd_changes,
    "undo": cmd_undo,
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
    except NotHere as err:
        print(f"NOT HERE: {err}", file=sys.stderr)
        return NOT_HERE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
