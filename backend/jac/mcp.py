"""The jac connector: jac as MCP tools, on a personal token. Claude's custom connector today.

The Claude app can't reach jac from its code sandbox (Pro and Max can't allow a custom domain),
but a custom connector is called from Anthropic's servers, not from the sandbox. So the app on the
web, the desktop and the phone talks to jac directly: Claude reads the career, writes the CV and
the letter on the user's own subscription, and saves the application here, where jac renders and
exports it. jac calls no model on this path.

The transport is MCP's Streamable HTTP, kept to its stateless core: every message is one POST,
answered with plain JSON (no SSE stream, no session id). It is the only way an outside model reaches
jac: `get_career` and `get_career_review` hand over the rules (jac/resources/rules/), so they are
always the deployed ones. The skill the user uploads only points here (jac/resources/skill/).

Tool failures Claude can fix (a field the import refuses, a stale read, an id that isn't the
user's) are tool results with `isError`, so Claude reads them and tries again. Protocol mistakes
(an unknown method or tool, arguments that aren't an object) are JSON-RPC errors.

The PDF preview is an MCP App (the `io.modelcontextprotocol/ui` extension): `save_application`
and `show_application` name the page `ui://jac/preview`, and the Claude app (web, desktop, phone)
renders it beside the tool call. The page is the frontend's own renderer, built into one
self-contained file (`frontend/vite.connector.config.ts`). It runs in Claude's sandbox with no
session and no network: its only way to jac is `application_preview`, a tool only the page can
call, through this same connector and token. It reads no more than `get_career` already gives
Claude (privacy §8): no signature image, no attachments. Clients without MCP Apps ignore the
`_meta`.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path

from django.conf import settings

from jac.career_changes import (
    ChangeError,
    StaleError,
    UndoRefused,
    apply_changes,
    batch_payload,
    entry_labels,
    undo_batch,
)
from jac.career_review import review_career
from jac.cv import CV
from jac.models import CareerChange, CvAttachment, JobApplication
from jac.serializers import (
    CvSerializer,
    JobApplicationSerializer,
    SkillApplicationSerializer,
    SkillCareerChangesSerializer,
)
from jac.skill import (
    RULES_DIR,
    application_payload,
    application_summary,
    application_url,
    career_payload,
    save_application,
)
from spa.models import PersonalityProfile, UserProfile
from spa.serializers import UserProfileSerializer

log = logging.getLogger(__name__)

# Newest first: an unknown version from the client gets the newest one jac speaks.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
RESOURCE_NOT_FOUND = -32002  # MCP's own code for resources/read

# The preview page (an MCP App). `npm run build` writes it here; it isn't tracked.
PREVIEW_URI = "ui://jac/preview"
PREVIEW_MIME = "text/html;profile=mcp-app"
PREVIEW_HTML = (
    Path(__file__).resolve().parent / "resources" / "connector" / "preview.html"
)
# What a checkout without the frontend build serves instead: the page says so, not a blank box.
PREVIEW_MISSING = (
    '<!doctype html><meta charset="utf-8"><p style="font: 14px sans-serif">'
    "jac's preview isn't built on this server. Run <code>npm run build</code> in "
    "<code>frontend/</code>, or open the application in jac.</p>"
)
_PREVIEW_UI = {"ui": {"resourceUri": PREVIEW_URI}, "ui/resourceUri": PREVIEW_URI}

# What the list tools show.
APPLICATIONS_SHOWN = 50
CHANGES_SHOWN = 20

INSTRUCTIONS = (
    "jac holds the user's career (jobs, projects, education, certifications, skills, "
    "languages) and renders and exports their job applications as PDFs. When the user pastes "
    "a job posting, or asks for a CV, Lebenslauf, cover letter, Anschreiben or Bewerbung, call "
    "`get_career` first, once per conversation: it returns the career and the rules for "
    "using it, and you follow those rules. To check or improve the career data, call "
    "`get_career_review`, which brings its own rules. Nothing is saved until the user says so; "
    "after `save_application`, give them the link it returns: they look at the result and "
    "export the PDF in jac. In the Claude app, `save_application` and `show_application` "
    "also show the PDF preview in the chat."
)


class ToolError(Exception):
    """A refusal Claude can act on: goes back as the tool's result, with `isError`."""

    def __init__(self, payload: dict):
        super().__init__(payload)
        self.payload = payload


class InvalidParams(Exception):
    pass


class ResourceNotFound(Exception):
    pass


@cache
def rules(name: str) -> str:
    """One of the connector's rulebooks (`applications`, `review`)."""
    return (RULES_DIR / f"{name}.md").read_text().strip()


def _pk(args: dict, what: str) -> int:
    value = args.get("id")
    if isinstance(value, bool):
        value = None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int):
        raise ToolError({"detail": f"`id` must be the {what}'s number."})
    return value


def _application(request, pk: int) -> JobApplication:
    application = (
        JobApplication.objects.select_related("posting__address")
        .filter(pk=pk, user=request.user)
        .first()
    )
    if application is None:
        # Someone else's id reads like a missing one.
        raise ToolError(
            {
                "detail": f"There is no application {pk}. `list_applications` shows yours."
            }
        )
    return application


# --- the tools ----------------------------------------------------------------------------------


def get_career(request, args):
    return {"rules": rules("applications"), "career": career_payload(request.user)}


def get_career_review(request, args):
    return {"rules": rules("review"), "review": review_career(request.user)}


def list_applications(request, args):
    rows = (
        JobApplication.objects.filter(user=request.user)
        .select_related("posting__address")
        .order_by("-created_at")[:APPLICATIONS_SHOWN]
    )
    return [application_summary(a) for a in rows]


def get_application(request, args):
    return application_payload(_application(request, _pk(args, "application")))


def save_application_tool(request, args):
    data = dict(args)
    application = None
    if "id" in data:
        application = _application(request, _pk(data, "application"))
        del data["id"]
    serializer = SkillApplicationSerializer(
        data=data, partial=application is not None, context={"request": request}
    )
    if not serializer.is_valid():
        raise ToolError({"errors": serializer.errors})
    application, warnings = save_application(
        request.user, serializer.validated_data, application
    )
    return {**application_payload(application), "warnings": warnings}


def show_application(request, args):
    # The preview does the showing (its page reads `application_preview`); Claude gets the
    # summary, so it can still name the application and give the link.
    return application_summary(_application(request, _pk(args, "application")))


def _layout_template(application: JobApplication):
    """The layout's JSON spec as stored (the frontend parses and falls back), None without one."""
    template = application.layout.template
    if not template:
        return None
    try:
        with template.open("rb") as fh:
            return json.loads(fh.read())
    except (OSError, ValueError) as err:
        log.warning("connector preview: layout %s unreadable: %s", template.name, err)
        raise ToolError(
            {"detail": "jac can't read this application's layout."}
        ) from err


# What the renderer reads of the profile: the sender block, the bio, two display settings. No
# more goes through Anthropic than `get_career` already sends (privacy §8); the signature image
# stays in jac, and the page says the export adds it.
PREVIEW_PROFILE = (
    "name",
    "email",
    "phone",
    "street",
    "address_line2",
    "zip",
    "city",
    "country",
    "website",
    "linkedin_url",
    "github_url",
    "bio",
    "show_socials",
    "accent_color",
)


def application_preview(request, args):
    """What the export card renders from, in the shapes the SPA's own endpoints answer
    (applications/<id>/, cv/entries/, profile/), so the page reuses the export's renderer as is.
    Attachments and the signature stay in jac: the page says the export adds them."""
    application = _application(request, _pk(args, "application"))
    context = {"request": request}
    profile = UserProfile.objects.select_related("user").get(user=request.user)
    shown = UserProfileSerializer(profile, context=context).data
    return {
        "application": JobApplicationSerializer(application, context=context).data,
        "layout": _layout_template(application),
        "career": CvSerializer(CV(request.user.pk).entries, context=context).data,
        "profile": {field: shown[field] for field in PREVIEW_PROFILE},
        "signature": bool(profile.signature),
        # The export skips ids whose file is gone; so does the count.
        "attachments": CvAttachment.objects.filter(
            user=request.user, pk__in=application.attachments or []
        ).count(),
        "url": application_url(application),
    }


def _stale(request, err: StaleError, redo: str) -> ToolError:
    return ToolError(
        {
            "detail": f"{err.detail} {redo}".strip(),
            "stale": err.ids,
            "labels": entry_labels(request.user, err.ids),
        }
    )


def apply_career_changes(request, args):
    serializer = SkillCareerChangesSerializer(data=args)
    if not serializer.is_valid():
        raise ToolError({"errors": serializer.errors})
    try:
        return apply_changes(
            request,
            serializer.validated_data["changes"],
            source=CareerChange.Source.claude,
            dry_run=serializer.validated_data["dry_run"],
        )
    except ChangeError as err:
        raise ToolError({"changes": err.errors}) from err
    except StaleError as err:
        raise _stale(
            request,
            err,
            "Call `get_career` again and redo these changes from what it says now.",
        ) from err


def list_career_changes(request, args):
    rows = CareerChange.objects.filter(user=request.user)[:CHANGES_SHOWN]
    return [batch_payload(b) for b in rows]


def undo_career_changes(request, args):
    pk = _pk(args, "batch")
    if not CareerChange.objects.filter(pk=pk, user=request.user).exists():
        raise ToolError(
            {"detail": f"There is no batch {pk}. `list_career_changes` shows yours."}
        )
    try:
        return undo_batch(request, pk)
    except UndoRefused as err:
        raise ToolError({"detail": str(err)}) from err
    except StaleError as err:
        raise _stale(request, err, "") from err
    except ChangeError as err:
        raise ToolError(
            {"detail": "jac can't put these back as they were.", "changes": err.errors}
        ) from err


# --- what tools/list shows ----------------------------------------------------------------------

_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}
_ID = {"type": "integer", "minimum": 1}
_IDS = {"type": "array", "items": {"type": "string", "pattern": r"^[a-z]+:\d+$"}}
_TEXT = {"type": "string"}

_POSTING = {
    "type": "object",
    "description": "The job ad: its full text, plus what you read out of it. Leave out what "
    "the ad doesn't give.",
    "properties": {
        "text": {**_TEXT, "description": "The full posting text, as the user gave it."},
        "title": {**_TEXT, "description": "The role, e.g. Senior Backend Engineer."},
        "language": {**_TEXT, "pattern": "^[a-z]{2}$", "description": "de, en, …"},
        "source_url": {**_TEXT, "description": "The ad's link, if there is one."},
        "company": _TEXT,
        "contact_name": {
            **_TEXT,
            "description": "The contact person, e.g. Frau Muster.",
        },
        "street": _TEXT,
        "address_line2": _TEXT,
        "zip": _TEXT,
        "city": _TEXT,
        "country": _TEXT,
        "email": _TEXT,
        "phone": _TEXT,
    },
}

_LETTER = {
    "type": "object",
    "description": "The letter body only: no date, address, subject, salutation, closing or "
    "signature. jac adds the frame.",
    "properties": {
        "body": {
            **_TEXT,
            "description": "170 to 230 words in three or four paragraphs, in the posting's "
            "language. No markdown, no placeholders.",
        },
        "tone": {"type": "string", "enum": list(PersonalityProfile.Tone.values)},
        "subject": {**_TEXT, "description": "Only to override jac's own."},
        "salutation": {**_TEXT, "description": "Only to override jac's own."},
        "closing": {**_TEXT, "description": "Only to override jac's own."},
    },
}

_APPLICATION = {
    "type": "object",
    "properties": {
        "id": {
            **_ID,
            "description": "An application's id, to update it: then only what you send "
            "changes. Leave it out to create a new one.",
        },
        "posting": _POSTING,
        "entries": {
            **_IDS,
            "description": "The CV: ids from `get_career` (job:3, skill:12, …), most "
            "relevant first within each section. Only ids the career shows.",
        },
        "pinned": {**_IDS, "description": "Entries the page fit must never drop."},
        "sections_off": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": sorted(JobApplicationSerializer._SECTIONS),
            },
            "description": "Whole sections to leave off the CV.",
        },
        "letter": _LETTER,
        "notes": {**_TEXT, "description": "The user's own notes on the application."},
    },
    "additionalProperties": False,
}

_CHANGES = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "items": {"type": "object"},
            "minItems": 1,
            "maxItems": SkillCareerChangesSerializer.MAX_CHANGES,
            "description": "The batch, each change as the review rules describe it: "
            '{"id", "seen", "why", "set", "add", "remove"}, or {"new": "skill", "ref", …}.',
        },
        "dry_run": {
            "type": "boolean",
            "default": False,
            "description": "Only check the batch and show what it would do.",
        },
    },
    "required": ["changes"],
    "additionalProperties": False,
}


def _hints(*, read_only: bool, destructive: bool = False, idempotent: bool = False):
    return {
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": read_only or idempotent,
        "openWorldHint": False,
    }


TOOLS = {
    "get_career": (
        get_career,
        {
            "title": "Read the career",
            "description": "The user's career from jac (every entry with its id, contact "
            "details, letter defaults, personality answers, writing sample) and the rules "
            "for writing a CV and a letter from it. Call it first, once per conversation, "
            "and follow the rules it returns.",
            "inputSchema": _NO_ARGS,
            "annotations": _hints(read_only=True),
        },
    ),
    "get_career_review": (
        get_career_review,
        {
            "title": "Review the career",
            "description": "jac's machine review of the career (findings by entry id) and "
            "the rules for reviewing and improving it with the user. Use it when the user "
            "wants their career data checked or improved; read `get_career` too.",
            "inputSchema": _NO_ARGS,
            "annotations": _hints(read_only=True),
        },
    ),
    "list_applications": (
        list_applications,
        {
            "title": "List applications",
            "description": f"The user's latest {APPLICATIONS_SHOWN} applications in jac, "
            "newest first: id, title, company, status, link.",
            "inputSchema": _NO_ARGS,
            "annotations": _hints(read_only=True),
        },
    ),
    "get_application": (
        get_application,
        {
            "title": "Show an application",
            "description": "One application in the shape `save_application` takes, to "
            "continue working on it.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": _ID},
                "required": ["id"],
                "additionalProperties": False,
            },
            "annotations": _hints(read_only=True),
        },
    ),
    "save_application": (
        save_application_tool,
        {
            "title": "Save the application",
            "description": "Save the CV and the letter to jac, only when the user says so. "
            "To create one, `posting.text`, `entries` and `letter.body` are required. With "
            "an `id`, it updates that application instead. Answers with the application, "
            "its `url` (give it to the user: they check it and export the PDF in jac) and "
            "`warnings` worth fixing (fix them, then save again with the id). In the Claude "
            "app the user sees the PDF preview below the call.",
            "inputSchema": _APPLICATION,
            "annotations": _hints(read_only=False),
            "_meta": _PREVIEW_UI,
        },
    ),
    "show_application": (
        show_application,
        {
            "title": "Show the PDF preview",
            "description": "Show a saved application's PDF preview to the user, as jac "
            "renders it: when they ask to see it without changing it. In the Claude app the "
            "preview appears below the call; elsewhere give them the `url`.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": _ID},
                "required": ["id"],
                "additionalProperties": False,
            },
            "annotations": _hints(read_only=True),
            "_meta": _PREVIEW_UI,
        },
    ),
    "application_preview": (
        application_preview,
        {
            "title": "Preview data",
            "description": "What jac's PDF preview renders from. Only the preview itself "
            "calls it; to show the user a preview, call `show_application`.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": _ID},
                "required": ["id"],
                "additionalProperties": False,
            },
            "annotations": _hints(read_only=True),
            "_meta": {"ui": {"resourceUri": PREVIEW_URI, "visibility": ["app"]}},
        },
    ),
    "apply_career_changes": (
        apply_career_changes,
        {
            "title": "Change career entries",
            "description": "Apply a batch of career edits the user agreed to, all or "
            "nothing; with `dry_run` only check it. Every batch can be undone until the "
            "user keeps it in jac. Follow the rules from `get_career_review`.",
            "inputSchema": _CHANGES,
            "annotations": _hints(read_only=False, destructive=True),
        },
    ),
    "list_career_changes": (
        list_career_changes,
        {
            "title": "List career changes",
            "description": f"The latest {CHANGES_SHOWN} batches of career edits, each with "
            "what it replaced.",
            "inputSchema": _NO_ARGS,
            "annotations": _hints(read_only=True),
        },
    ),
    "undo_career_changes": (
        undo_career_changes,
        {
            "title": "Undo career changes",
            "description": "Put a batch's entries back as they were and delete the skills "
            "it created. Refused once an entry in it has been edited again.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": _ID},
                "required": ["id"],
                "additionalProperties": False,
            },
            "annotations": _hints(read_only=False, destructive=True),
        },
    ),
}


# --- JSON-RPC -----------------------------------------------------------------------------------


def _text(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _initialize(request, params):
    asked = params.get("protocolVersion")
    return {
        "protocolVersion": asked
        if asked in PROTOCOL_VERSIONS
        else PROTOCOL_VERSIONS[0],
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"listChanged": False},
        },
        "serverInfo": {"name": "jac", "title": "jac", "version": settings.RELEASE},
        "instructions": INSTRUCTIONS,
    }


def _tools_list(request, params):
    return {"tools": [{"name": name, **spec} for name, (_, spec) in TOOLS.items()]}


def _tools_call(request, params):
    name = params.get("name")
    if name not in TOOLS:
        raise InvalidParams(f"Unknown tool: {name}")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise InvalidParams("`arguments` must be an object.")
    tool, _ = TOOLS[name]
    try:
        payload, is_error = tool(request, args), False
    except ToolError as err:
        payload, is_error = err.payload, True
    return {"content": [{"type": "text", "text": _text(payload)}], "isError": is_error}


_PREVIEW_RESOURCE = {
    "uri": PREVIEW_URI,
    "name": "preview",
    "title": "PDF preview",
    "description": "The application as jac renders the PDF: CV and letter, page by page.",
    "mimeType": PREVIEW_MIME,
}


def _resources_list(request, params):
    return {"resources": [_PREVIEW_RESOURCE]}


def _resources_read(request, params):
    if params.get("uri") != PREVIEW_URI:
        raise ResourceNotFound(f"Resource not found: {params.get('uri')}")
    try:
        html = PREVIEW_HTML.read_text()
    except FileNotFoundError:
        log.warning("connector: %s isn't built", PREVIEW_HTML)
        html = PREVIEW_MISSING
    return {
        "contents": [
            {
                "uri": PREVIEW_URI,
                "mimeType": PREVIEW_MIME,
                "text": html,
                # No origins: the page is one file and reaches jac only through the host.
                "_meta": {"ui": {"csp": {}, "prefersBorder": True}},
            }
        ]
    }


METHODS = {
    "initialize": _initialize,
    "ping": lambda request, params: {},
    "tools/list": _tools_list,
    "tools/call": _tools_call,
    "resources/list": _resources_list,
    "resources/templates/list": lambda request, params: {"resourceTemplates": []},
    "resources/read": _resources_read,
}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _answer(request, message) -> dict | None:
    """One message's answer, or None when it needs none (a notification, a response)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        mid = message.get("id") if isinstance(message, dict) else None
        return _error(mid, INVALID_REQUEST, "Invalid Request")
    if "method" not in message or "id" not in message:
        # Notifications (initialized, cancelled) change nothing on a stateless server, and
        # jac never sends a request a client could answer.
        return None
    mid = message["id"]
    handler = METHODS.get(message["method"])
    if handler is None:
        return _error(mid, METHOD_NOT_FOUND, f"Method not found: {message['method']}")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(mid, INVALID_PARAMS, "`params` must be an object.")
    try:
        return {"jsonrpc": "2.0", "id": mid, "result": handler(request, params)}
    except InvalidParams as err:
        return _error(mid, INVALID_PARAMS, str(err))
    except ResourceNotFound as err:
        return _error(mid, RESOURCE_NOT_FOUND, str(err))
    except Exception:
        log.exception("connector: %s failed", message["method"])
        return _error(mid, INTERNAL_ERROR, "jac failed on this request.")


def respond(request, raw: bytes) -> tuple[int, object | None]:
    """The HTTP status and the JSON body for one POST; a None body is a 202 without one."""
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return 400, _error(None, PARSE_ERROR, "Parse error")
    if isinstance(message, list):  # a batch: 2025-03-26 still allows them
        if not message:
            return 400, _error(None, INVALID_REQUEST, "Empty batch")
        answers = [a for a in (_answer(request, m) for m in message) if a is not None]
        return (200, answers) if answers else (202, None)
    answer = _answer(request, message)
    if answer is None:
        return 202, None
    bad = answer.get("error", {}).get("code") in (PARSE_ERROR, INVALID_REQUEST)
    return (400 if bad else 200), answer
