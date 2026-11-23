"""The machine half of a career review: what a script can find in the career DB without judgment.

The Claude skill reads these findings next to the career (`GET skill/review/`) and adds what needs
judgment: a vague description, a duty list without a result, a tool a text names that isn't a
skill yet. A finding names its entries by `type:pk` (`ids`, empty for the career as a whole), a
`check` slug, a `level` and one sentence (`say`):

- `fix`: the data contradicts itself (an end before its start);
- `improve`: something missing that costs the CV (no description, a skill a text names but doesn't
  link);
- `consider`: a judgment call (a gap, a skill nothing backs, a level the years don't match).
"""

from __future__ import annotations

import re
from collections import defaultdict

from django.utils import timezone

from jac.cover_letter import sender_block
from jac.cv import CV
from jac.models import Certification, Education, Job, Skill

LEVELS = ("fix", "improve", "consider")

# Fewer words than this reads as a placeholder: a job needs what was done and to what end.
MIN_WORDS = {"job": 25, "project": 15}
# About six months: the gap a recruiter asks about.
GAP_DAYS = 183
# What a letter's sender block can't do without. Name and email come from the account itself.
SENDER_FIELDS = ("street", "zip", "city", "phone")
# The profile fields the CV and the portfolio print, and what each one is for.
PROFILE_FIELDS = {
    "bio": "bio (the CV's summary and the portfolio's intro)",
    "website": "website (the contact line of the CV and the letters)",
}
# A status written where the degree's name goes ("Not completed", "Drop Out", "abgebrochen").
_STATUS = re.compile(
    r"not\s+completed|incomplete|unfinished|drop(?:ped)?[\s-]*out|abgebrochen|"
    r"ohne\s+abschluss|nicht\s+abgeschlossen",
    re.IGNORECASE,
)
# The entries that link skills, and so can back one.
_LINKING = ("jobs", "projects", "educations", "certifications")


def entry_id(obj) -> str:
    return f"{obj._meta.model_name}:{obj.pk}"


def entry_label(obj) -> str:
    """A short name for an entry, for findings and the change log."""
    if isinstance(obj, Job):
        return f"{obj.title} at {obj.company}"
    if isinstance(obj, Education):
        head = " ".join(p for p in (obj.degree, obj.field_of_study) if p)
        return f"{head} @ {obj.institution}" if head else obj.institution
    if isinstance(obj, Certification):
        return f"{obj.name} ({obj.issuer})" if obj.issuer else obj.name
    return obj.name


def _finding(level: str, check: str, ids: list[str], say: str) -> dict:
    return {"ids": ids, "check": check, "level": level, "say": say}


def _named(obj) -> str:
    return f"{entry_label(obj)} ({entry_id(obj)})"


def _skill_pattern(name: str) -> re.Pattern | None:
    """A skill's name as a whole word in free text, or None for a one-letter name ("C", "R"),
    which would match every stray letter. Names of up to three letters ("Go", "SQL") match
    case-sensitively: in lower case they're ordinary words."""
    name = name.strip()
    if len(name) < 2:
        return None
    flags = 0 if len(name) <= 3 else re.IGNORECASE
    return re.compile(rf"(?<!\w){re.escape(name)}(?![\w+#])", flags)


def _text(obj) -> str:
    """The free text of an entry that may name a skill: its title and its description."""
    if isinstance(obj, Job):
        parts = (obj.title, obj.description)
    elif isinstance(obj, Education):
        parts = (obj.degree, obj.field_of_study, obj.description)
    else:  # project, certification
        parts = (obj.name, obj.description)
    return "\n".join(p for p in parts if p)


def _check_dates(e: dict, today) -> list[dict]:
    out = []
    for obj in (*e["jobs"], *e["projects"], *e["educations"]):
        if obj.started and obj.ended and obj.ended < obj.started:
            out.append(
                _finding(
                    "fix",
                    "ended_before_started",
                    [entry_id(obj)],
                    f"{_named(obj)} ends ({obj.ended}) before it starts ({obj.started}).",
                )
            )
        if obj.started and obj.started > today:
            out.append(
                _finding(
                    "consider",
                    "starts_in_future",
                    [entry_id(obj)],
                    f"{_named(obj)} starts on {obj.started}, in the future. If it hasn't "
                    "begun, it may not belong on a CV yet.",
                )
            )
    for c in e["certifications"]:
        if c.issued_on and c.expires_on and c.expires_on < c.issued_on:
            out.append(
                _finding(
                    "fix",
                    "ended_before_started",
                    [entry_id(c)],
                    f"{_named(c)} expires ({c.expires_on}) before it was issued "
                    f"({c.issued_on}).",
                )
            )
    return out


def _check_descriptions(e: dict) -> list[dict]:
    out = []
    for kind, section in (("job", "jobs"), ("project", "projects")):
        for obj in e[section]:
            words = len(obj.description.split())
            if not words:
                out.append(
                    _finding(
                        "improve",
                        "no_description",
                        [entry_id(obj)],
                        f"{_named(obj)} has no description: the CV shows only its title "
                        "and dates.",
                    )
                )
            elif words < MIN_WORDS[kind]:
                out.append(
                    _finding(
                        "improve",
                        "thin_description",
                        [entry_id(obj)],
                        f"{_named(obj)} has a {words}-word description: say what you did "
                        "there, with what, and what came of it.",
                    )
                )
    for edu in e["educations"]:
        if edu.is_degree and not edu.description.strip():
            out.append(
                _finding(
                    "consider",
                    "no_description",
                    [entry_id(edu)],
                    f"{_named(edu)} has no description: a focus or the thesis topic would "
                    "go here.",
                )
            )
    return out


def _check_links(e: dict, today) -> list[dict]:
    """Skills a text names but the entry doesn't link, entries that link none, and projects
    that ran inside one job without being linked to it."""
    patterns = [(s, p) for s in e["skills"] if (p := _skill_pattern(s.name))]
    out = []
    for section in _LINKING:
        for obj in e[section]:
            linked = {s.pk for s in obj.skills.all()}
            text = _text(obj)
            named = [s for s, p in patterns if s.pk not in linked and p.search(text)]
            if named:
                names = ", ".join(_named(s) for s in named)
                out.append(
                    _finding(
                        "improve",
                        "skill_named_not_linked",
                        [entry_id(obj), *(entry_id(s) for s in named)],
                        f"{_named(obj)} names {names} but doesn't link "
                        f"{'it' if len(named) == 1 else 'them'}. Linked skills feed this "
                        "entry's ranking against a posting"
                        + (
                            "."
                            if section == "certifications"
                            else " and the skill's years of use."
                        ),
                    )
                )
            if linked:
                continue
            if section in ("jobs", "projects"):
                out.append(
                    _finding(
                        "improve",
                        "no_skills",
                        [entry_id(obj)],
                        f"{_named(obj)} links no skills: jac can't tell which skills it "
                        "backs, and they get no years of use from it.",
                    )
                )
            elif section == "certifications" or (
                obj.degree_level > Education.DegreeLevel.secondary
            ):
                # A school-leaving certificate (Abitur) taught no skill a CV names.
                out.append(
                    _finding(
                        "consider",
                        "no_skills",
                        [entry_id(obj)],
                        f"{_named(obj)} links no skills: link the ones it taught or "
                        "certifies.",
                    )
                )
    for p in e["projects"]:
        if p.job_id or not p.started:
            continue
        end = p.ended or today
        # Dates alone matched every project to a long-running side job (a co-founder role
        # since 2016): the project has to share a skill or a domain with the job too.
        hosts = [
            j
            for j in e["jobs"]
            if j.started <= p.started
            and end <= (j.ended or today)
            and _shares_skill_or_domain(p, j)
        ]
        if len(hosts) == 1:
            out.append(
                _finding(
                    "consider",
                    "project_during_job",
                    [entry_id(p), entry_id(hosts[0])],
                    f"{_named(p)} ran while you were at {_named(hosts[0])}. If it was "
                    "part of that job, link it (the project's `job`), so it reads as that "
                    "job's work.",
                )
            )
    return out


def _shares_skill_or_domain(a, b) -> bool:
    return bool(
        {s.pk for s in a.skills.all()} & {s.pk for s in b.skills.all()}
        or {d.pk for d in a.domains.all()} & {d.pk for d in b.domains.all()}
    )


def _years_source(skill) -> str:
    if skill.years_of_experience_override is not None:
        return "the number set in jac"
    return "the linked jobs, projects and educations, from first used on"


def _check_skills(e: dict) -> list[dict]:
    out = []
    backed = {
        s.pk for section in _LINKING for obj in e[section] for s in obj.skills.all()
    }
    unbacked = [s for s in e["skills"] if s.pk not in backed]
    if unbacked:
        out.append(
            _finding(
                "consider",
                "skill_unbacked",
                [entry_id(s) for s in unbacked],
                f"{len(unbacked)} skill(s) aren't linked to any job, project, education or "
                f"certification: {', '.join(s.name for s in unbacked)}. Nothing on the CV "
                "shows where they were used.",
            )
        )
    for s in e["skills"]:
        years = s.years_of_experience
        if years is None:
            continue
        if s.proficiency == Skill.Proficiency_Choices.expert and years < 2:
            out.append(
                _finding(
                    "consider",
                    "level_vs_years",
                    [entry_id(s)],
                    f"{s.name} says expert, but the record shows {years} year(s) of use "
                    f"({_years_source(s)}). Either the level is high or an earlier use is "
                    "missing.",
                )
            )
        elif s.proficiency == Skill.Proficiency_Choices.beginner and years >= 5:
            out.append(
                _finding(
                    "consider",
                    "level_vs_years",
                    [entry_id(s)],
                    f"{s.name} says beginner, but the record shows {years} years of use "
                    f"({_years_source(s)}). Either the level undersells it or a link is "
                    "wrong.",
                )
            )
    same = defaultdict(list)
    for s in e["skills"]:
        same[re.sub(r"[\s._-]+", "", s.name.lower())].append(s)
    for group in same.values():
        if len(group) > 1:
            out.append(
                _finding(
                    "improve",
                    "duplicate_skills",
                    [entry_id(s) for s in group],
                    f"{' and '.join(_named(s) for s in group)} look like the same skill. "
                    "Keep one, move the links over, and delete the other in jac.",
                )
            )
    return out


def _check_timeline(e: dict, today) -> list[dict]:
    out = []
    current = [j for j in e["jobs"] if j.ended is None and j.started <= today]
    if len(current) > 1:
        out.append(
            _finding(
                "consider",
                "several_current_jobs",
                [entry_id(j) for j in current],
                f"{len(current)} jobs have no end date, so the CV shows them all as current: "
                f"{', '.join(_named(j) for j in current)}. Add the end dates of those that "
                "are over.",
            )
        )
    spans = sorted(
        (o.started, o.ended or today)
        for o in (*e["jobs"], *e["educations"])
        if o.started and o.started <= today
    )
    covered = None
    for start, end in spans:
        if covered and (start - covered).days >= GAP_DAYS:
            months = round((start - covered).days / 30.4)
            out.append(
                _finding(
                    "consider",
                    "gap",
                    [],
                    f"Nothing between {covered:%Y-%m} and {start:%Y-%m} ({months} months): "
                    "no job or education covers it. If something filled it (a project, "
                    "freelance work, travel, care), it can go on the CV.",
                )
            )
        covered = end if covered is None else max(covered, end)
    if covered and (today - covered).days >= GAP_DAYS:
        out.append(
            _finding(
                "consider",
                "gap",
                [],
                f"Nothing on the CV since {covered:%Y-%m}. If something is running now, add "
                "it; if you're between jobs, a reader will ask what you're doing.",
            )
        )
    return out


def _check_credentials(e: dict, today) -> list[dict]:
    out = []
    for edu in e["educations"]:
        if not (edu.degree and not edu.is_degree and edu.ended and edu.ended <= today):
            continue
        if _STATUS.search(edu.degree):
            say = (
                f"{_named(edu)} has a status in its degree ({edu.degree}). The CV marks a "
                'study without a degree "(no degree)" by itself, so the degree is the '
                "place for what it aimed at (Master of Science), or leave it empty."
            )
        else:
            say = (
                f"{_named(edu)} names a degree ({edu.degree}) but is recorded as not "
                'completed (degree_level none), so the CV adds "(no degree)". If you '
                "finished it, set the level: jac puts the highest completed degree on every "
                "CV."
            )
        out.append(_finding("consider", "degree_not_completed", [entry_id(edu)], say))
    for c in e["certifications"]:
        if c.expires_on and c.expires_on < today:
            out.append(
                _finding(
                    "consider",
                    "expired",
                    [entry_id(c)],
                    f"{_named(c)} expired on {c.expires_on}. Renew it, or leave it off CVs "
                    "where a current one matters.",
                )
            )
        if not c.url and not c.credential_id:
            out.append(
                _finding(
                    "consider",
                    "unverifiable",
                    [entry_id(c)],
                    f"{_named(c)} has neither a link nor a credential ID, so a reader "
                    "can't check it.",
                )
            )
    return out


def _check_sender(user) -> list[dict]:
    block = sender_block(user)
    missing = [f for f in SENDER_FIELDS if not block[f].strip()]
    if not missing:
        return []
    return [
        _finding(
            "improve",
            "sender_incomplete",
            [],
            f"Your letters' sender block has no {', '.join(missing)}: fill it in under "
            "Account → Profile in jac.",
        )
    ]


def _check_profile(user) -> list[dict]:
    """The profile fields the CV and the portfolio print. Claude can't change them: the user
    fills them in jac."""
    p = getattr(user, "profile", None)
    missing = [
        why
        for field, why in PROFILE_FIELDS.items()
        if not (getattr(p, field, "") or "").strip()
    ]
    if not missing:
        return []
    return [
        _finding(
            "consider",
            "profile_incomplete",
            [],
            f"Your profile has no {' and no '.join(missing)}. Fill "
            f"{'it' if len(missing) == 1 else 'them'} in under Account → Profile in jac.",
        )
    ]


def review_career(user) -> dict:
    """Every finding for `user`'s career, the data errors first, then the gaps that cost the
    CV, then the judgment calls; within a level in the career's own order."""
    e = CV(user.pk).entries
    today = timezone.localdate()
    findings = [
        *_check_dates(e, today),
        *_check_descriptions(e),
        *_check_links(e, today),
        *_check_skills(e),
        *_check_timeline(e, today),
        *_check_credentials(e, today),
        *_check_sender(user),
        *_check_profile(user),
    ]
    findings.sort(key=lambda f: LEVELS.index(f["level"]))
    return {
        "generated_at": timezone.now().isoformat(timespec="seconds"),
        "counts": {
            level: sum(f["level"] == level for f in findings) for level in LEVELS
        },
        "findings": findings,
    }
