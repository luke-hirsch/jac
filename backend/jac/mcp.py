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

The public portfolio is read on any token (`get_portfolio`) and written only on a token the user
allowed (`AccessToken.may_edit_portfolio`, off by default). Writes go live at once, after the
user's yes in the chat, through the serializers jac's own editor uses. There are no drafts: every
write answers with what it replaced (`before`), and saving that back is the undo. Nothing is
deleted: a block is switched off, a link revoked. No files either: images and media are
uploaded in jac, never fetched from a URL, so a token can't pull outside content onto the page.
"""

from __future__ import annotations

import json
import logging
import re
from functools import cache
from pathlib import Path

from django.conf import settings
from django.db import transaction

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
from jac.models import CareerChange, CvAttachment, Domain, JobApplication
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
    career_index,
    career_payload,
    portfolio_block,
    portfolio_link,
    portfolio_payload,
    portfolio_standard,
    save_application,
)
from spa.models import PersonalityProfile, PortfolioBlock, PortfolioLink, UserProfile
from spa.portfolio import standard_page, standard_page_for, standard_page_url
from spa.serializers import (
    PortfolioBlockSerializer,
    PortfolioLinkSerializer,
    PortfolioStandardSerializer,
    UserProfileSerializer,
)

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
    "a job posting, asks for a CV, Lebenslauf, cover letter, Anschreiben or Bewerbung, or asks "
    "how well they fit a posting (jac stores that rating as `fit`, 1 to 5), call "
    "`get_career` first, once per conversation: it returns the career and the rules for "
    "using it, and you follow those rules. To check or improve the career data, call "
    "`get_career_review`, which brings its own rules. Nothing is saved until the user says so "
    "(asking for a fit rating counts as saying so); "
    "after `save_application`, give them the link it returns: they look at the result and "
    "export the PDF in jac. In the Claude app, `save_application` and `show_application` "
    "also show the PDF preview in the chat. For the user's public portfolio (its standard "
    "page, its links and its blocks), call `get_portfolio` first: it brings the portfolio and "
    "the rules for changing it. A change goes live at once, so make it only after the user's "
    "yes, and only a token the user allowed can make one."
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
    """One of the connector's rulebooks (`applications`, `review`, `portfolio`)."""
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


# What the renderer reads of the profile: the sender block, the bio, two display settings, and
# the full name the download is named with (lib/export.ts `exportStem`). Contact details, which
# privacy §8 lists; the signature image stays in jac, and the page says the export adds it.
PREVIEW_PROFILE = (
    "name",
    "first_name",
    "last_name",
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


# --- the public portfolio -----------------------------------------------------------------------

# What Claude can create. Image and media blocks need a file, and files are uploaded in jac.
BLOCK_KINDS = (
    PortfolioBlock.Kind.text,
    PortfolioBlock.Kind.quote,
    PortfolioBlock.Kind.figures,
    PortfolioBlock.Kind.cta,
    PortfolioBlock.Kind.linklist,
)
_BLOCK_FIELDS = (
    "kind",
    "title",
    "body",
    "data",
    "alt_text",
    "domains",
    "links",
    "favourite",
    "order",
    "is_active",
)
_BLOCK_ID = re.compile(r"^(?:block:)?(\d+)$")
_LINK_FIELDS = ("slug", "title", "intro", "is_default")
# A link's `content`, sent by its keys: what isn't sent keeps its stored value.
_LINK_CONTENT = ("featured", "domains", "hide_explore")
# What only a link takes: sent with `standard: true`, it's a mix-up worth saying so.
_LINK_ONLY = {"id", "slug", "domains", "hide_explore", "is_default", "revoke"}

UPLOADS_IN_JAC = (
    "Images and media are uploaded in jac, on the portfolio's blocks page: the connector "
    "carries no files, and jac fetches none from a URL. The user adds the block there; then "
    "you can change its title, caption and alt text here."
)


def _may_edit(request) -> bool:
    return bool(getattr(request.auth, "may_edit_portfolio", False))


def _check_may_edit(request) -> None:
    if not _may_edit(request):
        raise ToolError(
            {
                "detail": "This token may not change the portfolio. The user allows it in "
                "jac, Account → LLM, under Connectors: “May change your public portfolio” on "
                "this token. Until then, show them what you would change."
            }
        )


def _domains(request, names) -> list[Domain]:
    """Domains by name, case-insensitive against the user's visible taxonomy (jac's defaults
    and their own; their own wins a tie). jac creates none here: an unknown name is refused
    with the names it knows."""
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ToolError({"errors": {"domains": ["A list of domain names."]}})
    rows = sorted(
        Domain.objects.for_user(request.user),
        key=lambda d: d.user_id == request.user.pk,
    )
    by_name = {d.name.lower(): d for d in rows}
    unknown = [n for n in names if n.strip().lower() not in by_name]
    if unknown:
        raise ToolError(
            {
                "errors": {"domains": [f"No domain named {', '.join(unknown)}."]},
                "known_domains": sorted({d.name for d in rows}),
            }
        )
    return list(dict.fromkeys(by_name[n.strip().lower()] for n in names))


def _check_ids(request, field: str, ids) -> None:
    """Highlights and block links must be ids `get_portfolio` showed. jac's own editor takes
    any well-formed id (a dead one drops when the page renders), but one Claude made up would
    sit in the page unseen."""
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return  # the serializer names what's wrong with the shape
    known = {item["id"] for item in career_index(request.user)}
    known |= {
        f"block:{pk}"
        for pk in PortfolioBlock.objects.filter(user=request.user).values_list(
            "pk", flat=True
        )
    }
    unknown = [i for i in ids if i not in known]
    if unknown:
        message = f"Not in the portfolio: {', '.join(unknown)}. Use the ids it shows."
        raise ToolError({"errors": {field: [message]}})


def _block(request, args: dict) -> PortfolioBlock:
    match = _BLOCK_ID.match(str(args.get("id")))
    if match is None:
        raise ToolError(
            {
                "detail": "`id` is a block's id as `get_portfolio` shows it, e.g. block:12."
            }
        )
    block = PortfolioBlock.objects.filter(
        pk=int(match.group(1)), user=request.user
    ).first()
    if block is None:
        # Someone else's id reads like a missing one.
        raise ToolError(
            {
                "detail": f"There is no block block:{match.group(1)}. `get_portfolio` "
                "shows yours."
            }
        )
    return block


def _link(request, pk: int) -> PortfolioLink:
    link = (
        PortfolioLink.objects.filter(pk=pk, user=request.user)
        .exclude(kind=PortfolioLink.Kind.standard)
        .select_related("user__profile")
        .first()
    )
    if link is None:
        raise ToolError(
            {"detail": f"There is no link {pk}. `get_portfolio` shows yours."}
        )
    if link.kind == PortfolioLink.Kind.application:
        raise ToolError(
            {
                "detail": f"Link {pk} belongs to an application: it shows what that "
                "application sent. It changes in jac, with the application."
            }
        )
    return link


def get_portfolio(request, args):
    return {
        "rules": rules("portfolio"),
        "portfolio": portfolio_payload(request.user, may_edit=_may_edit(request)),
    }


def save_portfolio_block(request, args):
    _check_may_edit(request)
    if "image" in args or "media" in args:
        raise ToolError({"detail": UPLOADS_IN_JAC})
    data = {field: args[field] for field in _BLOCK_FIELDS if field in args}
    block = before = None
    if "id" in args:
        block = _block(request, args)
        before = portfolio_block(block)
        if data.get("kind", block.kind) != block.kind:
            raise ToolError(
                {
                    "detail": "A block keeps its kind. Create a new block of the other "
                    "kind, and switch this one off (`is_active: false`)."
                }
            )
    elif data.get("kind") in (PortfolioBlock.Kind.image, PortfolioBlock.Kind.media):
        raise ToolError({"detail": UPLOADS_IN_JAC})
    elif data.get("kind") not in BLOCK_KINDS:
        raise ToolError(
            {"detail": f"A new block needs a `kind`: one of {', '.join(BLOCK_KINDS)}."}
        )
    if "domains" in data:
        data["domains"] = [d.pk for d in _domains(request, data["domains"])]
    if "links" in data:
        _check_ids(request, "links", data["links"])
    serializer = PortfolioBlockSerializer(
        block, data=data, partial=block is not None, context={"request": request}
    )
    if not serializer.is_valid():
        raise ToolError({"errors": serializer.errors})
    block = serializer.save()
    return {
        **portfolio_block(block),
        "before": before,
        # Every active block shows on the standard page: in the Highlights or below them.
        "url": standard_page_url(request.user),
    }


def _save_standard(request, args: dict) -> dict:
    mixed = sorted(_LINK_ONLY & set(args))
    if mixed:
        raise ToolError(
            {
                "detail": "The standard page takes `title`, `intro` and `featured`, not "
                f"{', '.join(mixed)}. Those are a link's."
            }
        )
    data = {field: args[field] for field in ("title", "intro") if field in args}
    if "featured" in args:
        _check_ids(request, "featured", args["featured"])
        data["content"] = {"featured": args["featured"]}
    page = standard_page(request.user)
    before = portfolio_standard(request.user, page) if page else None
    with transaction.atomic():
        # The first write makes the row; a refused one leaves none behind.
        serializer = PortfolioStandardSerializer(
            standard_page_for(request.user),
            data=data,
            partial=True,
            context={"request": request},
        )
        if not serializer.is_valid():
            raise ToolError({"errors": serializer.errors})
        page = serializer.save()
    return {
        "standard": True,
        **portfolio_standard(request.user, page),
        "before": before,
    }


def save_portfolio_page(request, args):
    _check_may_edit(request)
    if args.get("standard"):
        return _save_standard(request, args)
    link = before = None
    if "id" in args:
        link = _link(request, _pk(args, "link"))
        before = portfolio_link(link)
    if args.get("revoke"):
        if link is None or set(args) - {"id", "revoke"}:
            raise ToolError(
                {"detail": "`revoke: true` takes the link's `id` and nothing else."}
            )
        link.revoke()
        return {**portfolio_link(link), "before": before}
    if link is not None and link.revoked_at is not None:
        raise ToolError(
            {
                "detail": f"Link {link.pk} is revoked, and its address stays dead. Create "
                "a new link instead."
            }
        )
    data = {field: args[field] for field in _LINK_FIELDS if field in args}
    sent = {key: args[key] for key in _LINK_CONTENT if key in args}
    if "domains" in sent:
        sent["domains"] = [d.name for d in _domains(request, sent["domains"])]
    if "featured" in sent:
        _check_ids(request, "featured", sent["featured"])
    if link is None or sent:
        data["content"] = {**((link.content if link else None) or {}), **sent}
    serializer = PortfolioLinkSerializer(
        link, data=data, partial=link is not None, context={"request": request}
    )
    if not serializer.is_valid():
        raise ToolError({"errors": serializer.errors})
    link = serializer.save()
    return {**portfolio_link(link), "before": before}


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
        "fit": {
            "type": ["integer", "null"],
            "minimum": 1,
            "maximum": 5,
            "description": "How well the user fits the posting, 1 to 5 (the rules' scale), "
            "or null to clear it. Rating an existing application: send only `id` and `fit`.",
        },
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


_DOMAIN_NAMES = {
    "type": "array",
    "items": _TEXT,
    "description": "Domain names, from `get_portfolio`'s `domains`.",
}

_BLOCK = {
    "type": "object",
    "properties": {
        "id": {
            "type": ["string", "integer"],
            "description": "A block's id (block:12), to change it: then only what you send "
            "changes. Leave it out to create a block.",
        },
        "kind": {
            "type": "string",
            "enum": list(BLOCK_KINDS),
            "description": "A new block's kind. A block keeps its kind; image and media "
            "blocks are made in jac.",
        },
        "title": _TEXT,
        "body": {
            **_TEXT,
            "description": "text: the markdown. quote: the quote. figures, linklist: one "
            "lead line. cta: the pitch. image, media: the caption.",
        },
        "data": {
            "type": "object",
            "description": "The kind's own fields, the whole set: quote {by, role}; figures "
            "{figures: [{value, label}], 2 to 4}; cta {label, url}; linklist {items: "
            "[{title, url, note}], 1 to 12}. Text, image and media take none.",
        },
        "alt_text": {
            **_TEXT,
            "description": "What an image or a poster shows, for screen readers.",
        },
        "domains": {
            **_DOMAIN_NAMES,
            "description": "The questionnaire topics the block shows under, by name from "
            "`get_portfolio`.",
        },
        "links": {
            **_IDS,
            "description": "Entries and blocks shown beneath this one, by the ids "
            "`get_portfolio` shows.",
        },
        "favourite": {
            "type": "boolean",
            "description": "A Highlight while the standard page has none of its own.",
        },
        "order": {"type": "integer", "minimum": 0},
        "is_active": {
            "type": "boolean",
            "description": "false takes the block off every page. Nothing is deleted.",
        },
    },
    "additionalProperties": False,
}

_PAGE = {
    "type": "object",
    "properties": {
        "standard": {
            "type": "boolean",
            "description": "true: write the standard page. It takes title, intro and "
            "featured only.",
        },
        "id": {
            **_ID,
            "description": "A link's id, to change or revoke it: then only what you send "
            "changes. Leave it out, and send a `slug`, to create a link.",
        },
        "slug": {
            **_TEXT,
            "description": "The link's address: <home_url><slug>/. Lower case, words "
            "joined by hyphens.",
        },
        "title": _TEXT,
        "intro": {**_TEXT, "description": "The paragraph under the title."},
        "featured": {
            **_IDS,
            "description": "The Highlights, in order, by the ids `get_portfolio` shows.",
        },
        "domains": {
            **_DOMAIN_NAMES,
            "description": "A link's explore-more part: what it shows below the "
            "Highlights. Empty: the favourites.",
        },
        "hide_explore": {
            "type": "boolean",
            "description": "A link with its Highlights only, no explore-more part.",
        },
        "is_default": {
            "type": "boolean",
            "description": "The page a visit falls back to when its own selection comes "
            "out empty. One link at a time.",
        },
        "revoke": {
            "type": "boolean",
            "description": "true: take the link down for good, its address stops working. "
            "With the `id` alone.",
        },
    },
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
            "newest first: id, title, company, status, fit, link.",
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
            "description": "Save the CV and the letter to jac, only when the user says so, "
            "or with an `id` and only `fit`, the rating the user asked for. "
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
    "get_portfolio": (
        get_portfolio,
        {
            "title": "Read the portfolio",
            "description": "The user's public portfolio in jac (the standard page, the "
            "links, every block, the domains and an index of the career entries) and the "
            "rules for changing it with them. Call it first when the user wants to see or "
            "change their portfolio, and follow the rules it returns. `may_edit` says "
            "whether this token may change it.",
            "inputSchema": _NO_ARGS,
            "annotations": _hints(read_only=True),
        },
    ),
    "save_portfolio_block": (
        save_portfolio_block,
        {
            "title": "Save a portfolio block",
            "description": "Create a block (text, quote, figures, cta, linklist), or "
            "change one by its `id`: only what you send changes. It goes live at once, so "
            "only after the user's yes. `is_active: false` takes a block off the "
            "portfolio; nothing is deleted. Answers with the block, `before` (what it "
            "replaced, null for a new block: saving it back undoes the change) and the "
            "`url` to give the user. Images and media are uploaded in jac.",
            "inputSchema": _BLOCK,
            "annotations": _hints(read_only=False, destructive=True),
        },
    ),
    "save_portfolio_page": (
        save_portfolio_page,
        {
            "title": "Save a portfolio page",
            "description": "With `standard: true`, write the standard page (title, intro, "
            "Highlights). Otherwise create a link (with a `slug`), change one by its `id` "
            "(only what you send changes) or revoke it (`id` and `revoke: true`). It goes "
            "live at once, so only after the user's yes. Answers with the page, `before` "
            "(what it replaced, null for a new one) and the `url` to give the user.",
            "inputSchema": _PAGE,
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
