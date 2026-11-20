"""The Claude skill's side of jac: what it reads and what it writes back.

The skill (jac/resources/skill/) does the thinking on the user's own Claude subscription: it reads
the career DB, picks the CV entries, writes the letter and talks it through. jac never calls a
model here. What comes back is an ordinary application, shaped exactly like a generated one
(`cv_content` per section, the letter body + `letter_meta`), so the editor, the fit and the
export treat it the same. The highest completed degree is added if the skill left it out: the
same domain rule the pipeline force-keeps (`CV.highest_degree_id`).
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import prefetch_related_objects
from django.utils import timezone

from jac.career_review import review_career
from jac.cover_letter import (
    letter_closing,
    letter_salutation,
    letter_subject,
    recipient_block,
    sender_block,
)
from jac.cv import CV
from jac.generation_result import serialize_cv_selection
from jac.models import (
    Domain,
    Education,
    JobApplication,
    JobPostAddress,
    JobPosting,
)
from jac.register import register_leaks, resolve_address_form
from spa.models import PersonalityProfile, PersonalityQuestion

SKILL_DIR = Path(__file__).resolve().parent / "resources" / "skill"
SKILL_NAME = "jac"

# The writer's band (`CoverLetterWriter._TARGET_WORDS`): one DIN page holds about 230 words.
LETTER_WORDS = (170, 230)

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

# A body that carries its own frame prints it twice: jac adds salutation and closing itself.
_OPENS_WITH_SALUTATION = re.compile(
    r"^\s*(dear|hi|hello|sehr geehrte|guten tag|hallo|liebe[rs]?)\b", re.IGNORECASE
)
_ENDS_WITH_CLOSING = re.compile(
    r"(kind regards|best regards|best,|yours sincerely|yours faithfully|"
    r"mit freundlichen grüßen|beste grüße|viele grüße|herzliche grüße)\W*\S*\W*$",
    re.IGNORECASE,
)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _stamp(obj) -> str:
    # Full precision: a career change sends it back as `seen`, and jac compares it exactly.
    return obj.updated_at.isoformat()


def _names(manager) -> list[str]:
    return [o.name for o in manager.all()]


def _place(location) -> str:
    if location is None:
        return ""
    return ", ".join(p for p in (location.city, location.country) if p)


def _job(j) -> dict:
    return {
        "id": f"job:{j.pk}",
        "title": j.title,
        "company": j.company,
        "type": j.job_type,
        "location": _place(j.location),
        "started": _iso(j.started),
        "ended": _iso(j.ended),
        "url": j.url,
        "description": j.description,
        "skills": _names(j.skills),
        "domains": _names(j.domains),
        "projects": [f"project:{p.pk}" for p in j.projects.all()],
        "favourite": j.favourite,
        "updated_at": _stamp(j),
    }


def _project(p) -> dict:
    return {
        "id": f"project:{p.pk}",
        "name": p.name,
        "job": f"job:{p.job_id}" if p.job_id else None,
        "location": _place(p.location),
        "started": _iso(p.started),
        "ended": _iso(p.ended),
        "url": p.url,
        "description": p.description,
        "skills": _names(p.skills),
        "domains": _names(p.domains),
        "favourite": p.favourite,
        "updated_at": _stamp(p),
    }


def _education(e) -> dict:
    return {
        "id": f"education:{e.pk}",
        "institution": e.institution,
        "field_of_study": e.field_of_study,
        "degree": e.degree or "",
        # What the entry EARNED; "none" is a study period that ended without one.
        "degree_level": Education.DegreeLevel(e.degree_level).name,
        "completed": e.is_degree,
        "grade": e.grade or "",
        "location": _place(e.location),
        "started": _iso(e.started),
        "ended": _iso(e.ended),
        "description": e.description,
        "skills": _names(e.skills),
        "domains": _names(e.domains),
        "favourite": e.favourite,
        "updated_at": _stamp(e),
    }


def _certification(c) -> dict:
    return {
        "id": f"certification:{c.pk}",
        "name": c.name,
        "issuer": c.issuer,
        "issued_on": _iso(c.issued_on),
        "expires_on": _iso(c.expires_on),
        "credential_id": c.credential_id,
        "url": c.url,
        "description": c.description,
        "skills": _names(c.skills),
        "domains": _names(c.domains),
        "favourite": c.favourite,
        "updated_at": _stamp(c),
    }


def _skill(s) -> dict:
    return {
        "id": f"skill:{s.pk}",
        "name": s.name,
        "proficiency": s.proficiency,
        "category": s.category,
        "years": s.years_of_experience,
        "first_used": _iso(s.first_used),
        "description": s.description,
        "domains": _names(s.domains),
        # Skill-to-skill links, by id: what this one is related to, and what it builds on.
        "related_skills": [f"skill:{o.pk}" for o in s.related_skills.all()],
        "builds_on": [f"skill:{o.pk}" for o in s.builds_on.all()],
        "certification": (
            f"certification:{s.certification_id}" if s.certification_id else None
        ),
        "favourite": s.favourite,
        "updated_at": _stamp(s),
    }


def _language(la) -> dict:
    return {
        "id": f"language:{la.pk}",
        "name": la.name,
        "fluency": la.fluency,
        "certification": (
            f"certification:{la.certification_id}" if la.certification_id else None
        ),
        "description": la.description,
        "favourite": la.favourite,
        "updated_at": _stamp(la),
    }


_ENTRY_SHAPES = {
    "jobs": _job,
    "projects": _project,
    "educations": _education,
    "certifications": _certification,
    "skills": _skill,
    "languages": _language,
}


def career_payload(user) -> dict:
    """Everything the skill reads, in one document: who the candidate is, how they like their
    letters, and every career entry with its `type:pk` id. Names instead of foreign keys, so
    Claude reads it without a second lookup."""
    cv = CV(user.pk)
    # What the shapes read beyond CV's own prefetches.
    prefetch_related_objects(cv.entries["skills"], "related_skills", "builds_on")
    prefetch_related_objects(cv.entries["educations"], "domains")
    prefetch_related_objects(cv.entries["certifications"], "domains")
    profile = getattr(user, "profile", None)
    personality = PersonalityProfile.objects.filter(user=user).first()
    labels = {q.slug: q.prompt for q in PersonalityQuestion.objects.for_user(user)}
    answers = (personality.answers or {}) if personality else {}
    return {
        "generated_at": timezone.now().isoformat(timespec="seconds"),
        "candidate": {
            **sender_block(user),
            "github": (profile.github_url if profile else "") or "",
            "bio": (profile.bio if profile else "") or "",
        },
        "letter_defaults": {
            "tone": personality.letter_tone if personality else "neutral",
            "focus": personality.letter_focus if personality else "balanced",
            "words": list(LETTER_WORDS),
        },
        "personality": [
            {"question": labels.get(slug, slug), "answer": text}
            for slug, text in answers.items()
            if (text or "").strip()
        ],
        "writing_sample": (personality.writing_sample if personality else "") or "",
        "highest_degree": cv.highest_degree_id(),
        # The domains an entry may be tagged with, by name: jac's defaults and the user's own.
        "domains": sorted(
            set(Domain.objects.for_user(user).values_list("name", flat=True))
        ),
        "entries": {
            section: [shape(obj) for obj in cv.entries[section]]
            for section, shape in _ENTRY_SHAPES.items()
        },
    }


def application_url(application: JobApplication) -> str:
    return f"{settings.FRONTEND_URL}/applications/{application.pk}"


def application_summary(application: JobApplication) -> dict:
    posting = application.posting
    address = getattr(posting, "address", None)
    return {
        "id": application.pk,
        "title": posting.title,
        "company": address.company if address else "",
        "status": application.status,
        "created_at": application.created_at.isoformat(timespec="seconds"),
        "url": application_url(application),
    }


def application_payload(application: JobApplication) -> dict:
    """An application in the shape the skill saves it, so reading one back and saving it again
    round-trips."""
    posting = application.posting
    meta = application.letter_meta or {}
    return {
        **application_summary(application),
        "updated_at": application.updated_at.isoformat(timespec="seconds"),
        "posting": {
            "text": posting.posting_text,
            "title": posting.title,
            "language": posting.language,
            "source_url": posting.source_url,
            **recipient_block(getattr(posting, "address", None)),
        },
        # A fixed section order: Postgres hands JSON keys back sorted its own way.
        "entries": [
            entry["id"]
            for section in _ENTRY_SHAPES
            for entry in (application.cv_content or {}).get(section, [])
            if isinstance(entry, dict) and "id" in entry
        ],
        "pinned": application.pinned_entries,
        "sections_off": application.sections_off,
        "letter": {
            "body": application.cover_letter,
            "subject": meta.get("subject", ""),
            "salutation": meta.get("salutation", ""),
            "closing": meta.get("closing", ""),
        },
        "notes": application.notes,
    }


def _cv_content(
    user, entries: list[str], pinned: list[str]
) -> tuple[dict, list[str], str | None]:
    """`cv_content` from an ordered list of entry ids, the way a strong-rung run stores it: per
    section in the given order, no scores. Returns the content, the pins (the highest degree
    among them) and the highest degree's id if it had to be added."""
    cv = CV(user.pk)
    entries, pins = list(entries), list(pinned)
    top = cv.highest_degree_id()
    added = None
    if top:
        if top not in entries:
            entries.append(top)
            added = top
        if top not in pins:
            pins.append(top)
    selection: dict[str, list[dict]] = {}
    for entry_id in entries:
        etype = entry_id.split(":", 1)[0]
        selection.setdefault(etype, []).append(
            {"id": entry_id, "score": None, "pinned": entry_id in pins}
        )
    cv.apply_selection(selection)
    return serialize_cv_selection(cv), pins, added


def _letter_meta(user, posting: JobPosting, letter: dict) -> dict:
    """The frame around the body, built like a generated letter's (`cover_letter.letter_meta`).
    The skill may override subject, salutation and closing; the rest comes from the profile and
    the posting."""
    profile = PersonalityProfile.objects.filter(user=user).first()
    tone = letter.get("tone") or (profile.letter_tone if profile else "") or "neutral"
    language = (posting.language or "en").lower()[:2]
    recipient = recipient_block(getattr(posting, "address", None))
    return {
        "language": language,
        "subject": letter.get("subject") or letter_subject(language, posting.title),
        "salutation": letter.get("salutation")
        or letter_salutation(language, tone, recipient["contact_name"]),
        "date": timezone.localdate().isoformat(),
        "closing": letter.get("closing") or letter_closing(language, tone),
        "sender": sender_block(user),
        "recipient": recipient,
        "address_form": resolve_address_form(tone, posting.posting_text, language),
    }


def _letter_warnings(body: str, meta: dict) -> list[str]:
    warnings = []
    words = len(body.split())
    lo, hi = LETTER_WORDS
    if not lo <= words <= hi:
        warnings.append(
            f"The letter body has {words} words; jac aims for {lo}-{hi} so it fits one page."
        )
    if _OPENS_WITH_SALUTATION.match(body):
        warnings.append(
            "The body opens with a salutation; jac adds its own, so it would print twice."
        )
    if _ENDS_WITH_CLOSING.search(body):
        warnings.append(
            "The body ends with a closing; jac adds its own, so it would print twice."
        )
    leaks = register_leaks(body, meta["language"], meta["address_form"])
    if leaks:
        warnings.append(
            f"The letter addresses the reader as '{meta['address_form']}', "
            f"but also uses: {', '.join(leaks)}."
        )
    return warnings


@transaction.atomic
def save_application(
    user, data: dict, application: JobApplication | None = None
) -> tuple[JobApplication, list[str]]:
    """Create (`application` None) or update an application from the skill's validated payload
    (`SkillApplicationSerializer`). On update only what's sent changes; the letter's frame is
    rebuilt whenever a letter is sent. Returns the application and warnings worth showing."""
    warnings: list[str] = []
    posting_data = dict(data.get("posting") or {})
    if application is None:
        posting = JobPosting.objects.create(
            user=user, posting_text=posting_data.pop("text").strip()
        )
        application = JobApplication(user=user, posting=posting)
    else:
        posting = application.posting
        if "text" in posting_data:
            posting.posting_text = posting_data.pop("text").strip()

    for field in ("title", "language", "source_url"):
        if field in posting_data:
            setattr(posting, field, posting_data.pop(field))
    posting.save()
    address = {k: v for k, v in posting_data.items() if k in ADDRESS_FIELDS}
    if address:
        # Through the reverse accessor, so the letter frame below reads the new address.
        posting.address, _ = JobPostAddress.objects.update_or_create(
            job_posting=posting, defaults=address
        )

    if "entries" in data or "pinned" in data:
        entries = data.get("entries")
        if entries is None:
            entries = application_payload(application)["entries"]
        pinned = data.get("pinned", application.pinned_entries or [])
        content, pins, added = _cv_content(user, entries, pinned)
        if added:
            warnings.append(
                f"Added {added}: the highest completed degree is on every CV."
            )
        application.cv_content = content
        application.pinned_entries = pins
    if "sections_off" in data:
        application.sections_off = list(dict.fromkeys(data["sections_off"]))
    if "letter" in data:
        letter = data["letter"]
        if "body" in letter:
            application.cover_letter = letter["body"].strip()
        application.letter_meta = _letter_meta(user, posting, letter)
        warnings += _letter_warnings(application.cover_letter, application.letter_meta)
    if "notes" in data:
        application.notes = data["notes"]
    application.save()
    return application, warnings


def skill_bundle(user, key: str) -> bytes:
    """The skill as a zip Claude takes as an upload: the skill folder, a `config.json` with
    this site's address and the token, and two snapshots for when the sandbox can't reach jac:
    `career.json` (the career DB) and `review.json` (the machine review of it)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(SKILL_DIR.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                z.write(path, f"{SKILL_NAME}/{path.relative_to(SKILL_DIR)}")
        z.writestr(
            f"{SKILL_NAME}/config.json",
            json.dumps({"url": settings.FRONTEND_URL, "token": key}, indent=2),
        )
        z.writestr(
            f"{SKILL_NAME}/career.json",
            json.dumps(career_payload(user), indent=1, ensure_ascii=False),
        )
        z.writestr(
            f"{SKILL_NAME}/review.json",
            json.dumps(review_career(user), indent=1, ensure_ascii=False),
        )
    return buf.getvalue()
