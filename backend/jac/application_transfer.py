"""A user's applications as JSON, and back: the Applications page's download and import.

The file is `{"postings": [...], "applications": [...]}`. An application names its posting by
index into `postings`, so two applications for one posting stay on one posting.

What makes it portable is how it points at the career. In the database an application's CV
picks and pins are `type:pk` ids, which mean nothing on another server, or after a career import
on this one. In the file they're **natural keys**: a list of the type and the fields that name the
entry (a job by title, company and start; a skill by name; …), the career file's rule. The import
resolves them against the importing user's career; what isn't there is left out, with a warning.
Attachments go by label (the file itself stays behind), the layout by name.

Not in the file: the generation runs (history, not content) and the attachments' PDFs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction

from jac.career_import import has_career_entries
from jac.models import (
    ApplicationLayout,
    Certification,
    CvAttachment,
    Education,
    Job,
    JobApplication,
    JobPostAddress,
    JobPosting,
    Language,
    Project,
    Skill,
)
from jac.serializers import MAX_POSTING_CHARS, JobApplicationSerializer

POSTING_FIELDS = ("title", "posting_text", "language", "source_url", "active")
ADDRESS_FIELDS = (
    "company",
    "contact_name",
    "street",
    "address_line2",
    "zip",
    "city",
    "country",
    "email",
    "phone",
)
# What the API keeps read-only (it moves through transitions), the file restores as it was.
HISTORY_FIELDS = (
    "status",
    "delivery_method",
    "response_outcome",
    "sent_by_system",
    "approved_at",
    "sent_at",
    "responded_at",
)
EDITABLE_FIELDS = (
    "cover_letter",
    "letter_meta",
    "sections_off",
    "deadline",
    "notes",
    "include_portfolio_link",
    "fit",
    "apply_url",
    "apply_note",
    "email_body",
)
MAX_IMPORT_APPLICATIONS = 500


def _day(d) -> str | None:
    return d.isoformat() if d else None


# type → (model, the fields that name an entry). The career file names entries the same way.
_KEYS = {
    "job": (Job, lambda o: [o.title, o.company, _day(o.started)]),
    "project": (Project, lambda o: [o.name]),
    "education": (Education, lambda o: [o.institution, _day(o.started)]),
    "certification": (Certification, lambda o: [o.name]),
    "skill": (Skill, lambda o: [o.name]),
    "language": (Language, lambda o: [o.name]),
}


class ApplicationImportError(ValueError):
    """The file can't be imported. The message is meant for the user."""


class ApplicationsNotEmpty(Exception):
    """An import only fills an account without applications, so it never duplicates any."""


class CareerMissing(Exception):
    """The applications' CVs point at career entries: import the career first."""


def entry_refs(user: User) -> dict[str, list]:
    """`type:pk` → the entry's natural key, `[type, *fields]`, for all of `user`'s entries."""
    out: dict[str, list] = {}
    for kind, (model, key) in _KEYS.items():
        for obj in model.objects.filter(user=user).order_by("pk"):
            out[f"{kind}:{obj.pk}"] = [kind, *key(obj)]
    return out


def entry_ids(user: User) -> dict[tuple, str]:
    """The reverse: natural key → `type:pk`. Two entries with one key: the older wins, as the
    career import resolves a name to its first match."""
    out: dict[tuple, str] = {}
    for entry_id, ref in entry_refs(user).items():
        out.setdefault(tuple(ref), entry_id)
    return out


def _attachment_name(a: CvAttachment) -> str:
    return a.label or Path(a.file.name).name


# ---- export ------------------------------------------------------------------------------------


def export_applications(user: User) -> dict[str, list[dict]]:
    """Every application of `user`, oldest first, with the postings they hang off."""
    refs = entry_refs(user)
    names = {a.pk: _attachment_name(a) for a in CvAttachment.objects.filter(user=user)}
    apps = (
        JobApplication.objects.filter(user=user)
        .select_related("posting__address", "layout")
        .order_by("created_at", "pk")
    )
    postings: list[dict] = []
    index: dict[int, int] = {}
    out = []
    for app in apps:
        if app.posting_id not in index:
            index[app.posting_id] = len(postings)
            postings.append(_posting(app.posting))
        out.append(_application(app, index[app.posting_id], refs, names))
    return {"postings": postings, "applications": out}


def _posting(p: JobPosting) -> dict:
    address = getattr(p, "address", None)
    return {
        **{f: getattr(p, f) for f in POSTING_FIELDS},
        "created_at": p.created_at,
        "address": {f: getattr(address, f) for f in ADDRESS_FIELDS}
        if address
        else None,
    }


def _application(app: JobApplication, posting: int, refs: dict, names: dict) -> dict:
    cv_content = {
        section: [
            {**{k: v for k, v in row.items() if k != "id"}, "ref": refs[row["id"]]}
            for row in rows
            if isinstance(row, dict) and row.get("id") in refs
        ]
        for section, rows in (app.cv_content or {}).items()
        if isinstance(rows, list)
    }
    return {
        "posting": posting,
        **{f: getattr(app, f) for f in EDITABLE_FIELDS},
        "cv_content": cv_content,
        "pinned_entries": [refs[i] for i in app.pinned_entries if i in refs],
        "attachments": [names[pk] for pk in app.attachments if pk in names],
        "layout": app.layout.name,
        **{f: getattr(app, f) for f in HISTORY_FIELDS},
        "created_at": app.created_at,
    }


# ---- import ------------------------------------------------------------------------------------


def load_into_empty_applications(user: User, data: Any, *, request) -> dict:
    """Import the file into an account without applications. `request` is the API request: the
    applications go through `JobApplicationSerializer`, the editor's own checks, which read the
    user from it. Returns `{"created": {...}, "warnings": [...]}`.

    Raises ApplicationsNotEmpty, CareerMissing or ApplicationImportError; nothing is written then.
    Like the career import, the emptiness check runs again under the user's row lock.
    """
    if JobApplication.objects.filter(user=user).exists():
        raise ApplicationsNotEmpty
    if not has_career_entries(user):
        raise CareerMissing
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("applications"), list)
        or not isinstance(data.get("postings"), list)
    ):
        raise ApplicationImportError(
            "That isn't an applications file: pick the one Download applications wrote."
        )
    if len(data["applications"]) > MAX_IMPORT_APPLICATIONS:
        raise ApplicationImportError(
            f"The file has {len(data['applications'])} applications; an import takes at "
            f"most {MAX_IMPORT_APPLICATIONS}."
        )
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        if JobApplication.objects.filter(user=user).exists():
            raise ApplicationsNotEmpty
        try:
            return _import(user, data, request)
        except ApplicationImportError:
            raise
        except (KeyError, TypeError, AttributeError) as exc:
            raise ApplicationImportError(
                "The file doesn't have the shape Download applications writes."
            ) from exc


def _check(obj, what: str) -> None:
    """The model's own rules (choices, lengths, URL and email formats); also turns the file's
    ISO strings into dates and datetimes."""
    try:
        obj.full_clean()
    except ValidationError as exc:
        problems = "; ".join(
            f"{field}: {' '.join(messages)}"
            for field, messages in getattr(
                exc, "message_dict", {"": exc.messages}
            ).items()
        )
        raise ApplicationImportError(f"{what}: {problems}") from exc


def _moment(model, value):
    """The file's `created_at`, parsed; None keeps the import's own time."""
    if value is None:
        return None
    try:
        return model._meta.get_field("created_at").to_python(value)
    except ValidationError as exc:
        raise ApplicationImportError(f"Not a date and time: {value!r}") from exc


def _import(user: User, data: dict, request) -> dict:
    ids = entry_ids(user)
    layouts: dict[str, int] = {}
    for layout in ApplicationLayout.objects.for_user(user):
        if layout.user_id == user.pk or layout.name not in layouts:
            layouts[layout.name] = layout.pk  # the user's own over a system one
    attachments: dict[str, int] = {}
    for a in CvAttachment.objects.filter(user=user).order_by("pk"):
        attachments.setdefault(_attachment_name(a), a.pk)

    postings = [_create_posting(user, p) for p in data["postings"]]
    warnings: list[str] = []
    for n, item in enumerate(data["applications"], 1):
        index = item["posting"]
        if not isinstance(index, int) or not 0 <= index < len(postings):
            raise ApplicationImportError(
                f"Application {n} names no posting in the file."
            )
        posting = postings[index]
        title = posting.title or f"Application {n}"
        cv_content, dropped = _resolve_cv(item.get("cv_content") or {}, ids, title)
        wanted = item.get("attachments") or []
        payload = {
            "posting": posting.pk,
            **{f: item[f] for f in EDITABLE_FIELDS if f in item},
            "cv_content": cv_content,
            "pinned_entries": [
                ids[tuple(r)]
                for r in item.get("pinned_entries") or []
                if tuple(r) in ids
            ],
            "attachments": [attachments[a] for a in wanted if a in attachments],
        }
        layout = item.get("layout")
        if layout in layouts:
            payload["layout"] = layouts[layout]
        elif layout:
            warnings.append(f"{title}: no layout {layout!r} here, so the default one.")
        serializer = JobApplicationSerializer(
            data=payload, context={"request": request}
        )
        if not serializer.is_valid():
            problems = "; ".join(
                f"{field}: {' '.join(str(m) for m in messages)}"
                for field, messages in serializer.errors.items()
            )
            raise ApplicationImportError(f"{title}: {problems}")
        app = serializer.save()  # not the viewset's create: an import queues no run
        for f in HISTORY_FIELDS:
            if f in item:
                setattr(app, f, item[f])
        _check(app, title)
        app.save()
        created_at = _moment(JobApplication, item.get("created_at"))
        if created_at:
            JobApplication.objects.filter(pk=app.pk).update(created_at=created_at)
        if dropped:
            warnings.append(
                f"{title}: {dropped} CV entr{'y' if dropped == 1 else 'ies'} not in your "
                "career, left out."
            )
        missing = [a for a in wanted if a not in attachments]
        if missing:
            warnings.append(
                f"{title}: no attachment {', '.join(map(repr, missing))} in your library, "
                "left out."
            )
    return {
        "created": {
            "postings": len(postings),
            "applications": len(data["applications"]),
        },
        "warnings": warnings,
    }


def _create_posting(user: User, item: dict) -> JobPosting:
    text = item["posting_text"]
    if isinstance(text, str) and len(text) > MAX_POSTING_CHARS:
        raise ApplicationImportError(
            f"A posting is longer than {MAX_POSTING_CHARS} characters."
        )
    posting = JobPosting(
        user=user,
        **{f: item[f] for f in POSTING_FIELDS if f in item},
    )
    _check(posting, f"Posting {item.get('title') or ''!r}")
    posting.save()
    created_at = _moment(JobPosting, item.get("created_at"))
    if created_at:
        JobPosting.objects.filter(pk=posting.pk).update(created_at=created_at)
    address = item.get("address")
    if address:
        row = JobPostAddress(
            job_posting=posting,
            **{f: address.get(f) or "" for f in ADDRESS_FIELDS},
        )
        _check(row, f"The address of {posting.title or 'a posting'!r}")
        row.save()
    return posting


def _resolve_cv(content: dict, ids: dict, title: str) -> tuple[dict, int]:
    """The file's CV picks with their natural keys swapped for this career's ids. Returns the
    content and how many picks found no entry."""
    if not isinstance(content, dict):
        raise ApplicationImportError(f"{title}: the CV isn't an object.")
    unknown = sorted(set(content) - JobApplicationSerializer._SECTIONS)
    if unknown:
        raise ApplicationImportError(f"{title}: unknown CV sections {unknown}.")
    out: dict[str, list] = {}
    dropped = 0
    for section, rows in content.items():
        kept = []
        for row in rows:
            ref = row.get("ref")
            entry_id = ids.get(tuple(ref)) if isinstance(ref, list) else None
            if entry_id is None:
                dropped += 1
                continue
            kept.append(
                {**{k: v for k, v in row.items() if k != "ref"}, "id": entry_id}
            )
        out[section] = kept
    return out, dropped
