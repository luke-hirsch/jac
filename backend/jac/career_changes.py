"""Career edits from the Claude skill: a batch of changes, applied all or nothing and logged with
what each touched field held before, so the batch can be undone (`CareerChange`).

A change edits one entry, or creates one skill:

    {"id": "job:3", "seen": "<the entry's updated_at, from career>", "why": "…",
     "set": {"description": "…"},
     "add": {"skills": ["skill:12", "new:docker"]}, "remove": {"skills": ["skill:4"]}}
    {"new": "skill", "ref": "docker", "why": "…",
     "set": {"name": "Docker", "category": "technical"}, "add": {"related_skills": ["skill:3"]}}

Every write goes through the entry's own serializer (`JobSerializer`, …), so the editor's rules
hold here too: linked ids must be the user's own, the favourite caps, unique skill names. The skill
may create skills (a tool a description names), never jobs or degrees: those are facts only the
user can add. `seen` guards against a stale read: when an entry changed in jac after Claude read
it, the whole batch is refused.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from jac.career_review import entry_id, entry_label
from jac.models import CareerChange, Domain, Education, Skill
from jac.serializers import (
    ENTRY_ID_RE,
    ENTRY_MODELS,
    CertificationSerializer,
    EducationSerializer,
    JobSerializer,
    LanguageSerializer,
    ProjectSerializer,
    SkillSerializer,
)

WHY_CHARS = 300

# What `set` may change, per type, by the names `career` shows. `job` and `certification` are
# single links among them, written as `type:pk` or null; LINKS says which type each one names.
FIELDS = {
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
    },
    "language": {"name", "fluency", "description", "favourite", "certification"},
}
# A skill's certifications are set from the certification (its `skills`), the one link.
LINKS = {"job": "job", "certification": "certification"}
# The lists add / remove may touch, per type: skill ids, or domain names.
LISTS = {
    "job": {"skills": "skill", "domains": "domain"},
    "project": {"skills": "skill", "domains": "domain"},
    "education": {"skills": "skill", "domains": "domain"},
    "certification": {"skills": "skill", "domains": "domain"},
    "skill": {"domains": "domain", "related_skills": "skill", "builds_on": "skill"},
    "language": {},
}
SERIALIZERS = {
    "job": JobSerializer,
    "project": ProjectSerializer,
    "education": EducationSerializer,
    "certification": CertificationSerializer,
    "skill": SkillSerializer,
    "language": LanguageSerializer,
}
_KEYS = {"id", "new", "ref", "seen", "why", "set", "add", "remove"}


class ChangeError(Exception):
    """The batch doesn't validate. `errors` is keyed by the change's index (or entry id, on an
    undo)."""

    def __init__(self, errors: dict):
        super().__init__("invalid changes")
        self.errors = errors


class StaleError(Exception):
    """Entries changed in jac after the batch's view of them; `ids` names them."""

    def __init__(self, ids: list[str], detail: str):
        super().__init__(detail)
        self.ids = ids
        self.detail = detail


class UndoRefused(Exception):
    pass


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _touched(change: dict) -> set[str]:
    return {
        *(change.get("set") or {}),
        *(change.get("add") or {}),
        *(change.get("remove") or {}),
    }


def _value(obj, etype: str, field: str):
    """A field as the log keeps it: skill lists as `skill:pk` ids, domains by name, single links
    as `type:pk` or None, the degree level by name, dates as ISO strings."""
    kind = LISTS[etype].get(field)
    if kind == "domain":
        return sorted(getattr(obj, field).values_list("name", flat=True))
    if kind == "skill":
        pks = sorted(getattr(obj, field).values_list("pk", flat=True))
        return [f"skill:{pk}" for pk in pks]
    if field in LINKS:
        pk = getattr(obj, f"{field}_id")
        return f"{LINKS[field]}:{pk}" if pk else None
    if field == "degree_level":
        return Education.DegreeLevel(obj.degree_level).name
    value = getattr(obj, field)
    return value.isoformat() if hasattr(value, "isoformat") else value


def _snapshot(obj, etype: str, fields: set[str]) -> dict:
    return {field: _value(obj, etype, field) for field in sorted(fields)}


def _pk(value, etype: str, refs: dict[str, int]) -> int:
    """`skill:12` → 12; `new:docker` → the pk this batch gave the skill it created as `docker`."""
    if isinstance(value, str):
        kind, _, rest = value.partition(":")
        if kind == "new" and etype == "skill" and rest in refs:
            return refs[rest]
        if kind == etype and rest.isdigit():
            return int(rest)
    also = " or 'new:<ref>' for a skill this batch creates" if etype == "skill" else ""
    raise ValueError(f"{value!r} isn't an id like '{etype}:12'{also}.")


def _domains(user) -> dict[str, int]:
    return {d.name: d.pk for d in Domain.objects.for_user(user)}


def _data(
    etype: str, change: dict, obj, refs: dict[str, int], domains: dict[str, int]
) -> tuple[dict, dict]:
    """The serializer's input for one change, and what's wrong with it, by field. Its names are
    checked already (`_name_errors`). Lists become the whole new list: what the entry links now,
    minus `remove`, plus `add`."""
    data, errors = {}, {}
    for field, value in (change.get("set") or {}).items():
        try:
            if field in LINKS:
                value = None if value is None else _pk(value, LINKS[field], refs)
            elif field == "degree_level" and isinstance(value, str):
                if value not in Education.DegreeLevel.names:
                    raise ValueError(
                        f"one of {', '.join(Education.DegreeLevel.names)}."
                    )
                value = Education.DegreeLevel[value].value
            data[field] = value
        except ValueError as err:
            errors[field] = [str(err)]
    add, remove = change.get("add") or {}, change.get("remove") or {}
    for field in sorted({*add, *remove}):
        kind = LISTS[etype][field]
        plus, minus = add.get(field, []), remove.get(field, [])
        try:
            if not (isinstance(plus, list) and isinstance(minus, list)) or not all(
                isinstance(v, str) for v in (*plus, *minus)
            ):
                raise ValueError("add / remove take lists of ids or names.")
            current = _value(obj, etype, field)
            if kind == "domain":
                unknown = [n for n in plus if n not in domains]
                if unknown:
                    raise ValueError(
                        f"unknown domain(s) {unknown}; jac knows {sorted(domains)}."
                    )
                names = [n for n in current if n not in minus]
                names += [n for n in plus if n not in names]
                data[field] = [domains[n] for n in names if n in domains]
            else:
                drop = {_pk(v, "skill", refs) for v in minus}
                pks = [int(v.split(":")[1]) for v in current]
                pks = [pk for pk in pks if pk not in drop]
                pks += [pk for v in plus if (pk := _pk(v, "skill", refs)) not in pks]
                data[field] = pks
        except ValueError as err:
            errors[field] = [str(err)]
    return data, errors


def _shape_errors(change) -> dict:
    if not isinstance(change, dict):
        return {"non_field_errors": ["A change is an object."]}
    errors = {}
    unknown = sorted(set(change) - _KEYS)
    if unknown:
        errors["non_field_errors"] = [
            f"Unknown keys {unknown}; a change takes {', '.join(sorted(_KEYS))}."
        ]
    for key in ("set", "add", "remove"):
        if not isinstance(change.get(key) or {}, dict):
            errors[key] = ["An object of fields."]
    if not isinstance(change.get("why", ""), str):
        errors["why"] = ["One sentence: why this change."]
    if not errors and not _touched(change):
        errors["non_field_errors"] = ["Changes nothing: give set, add or remove."]
    return errors


def _name_errors(etype: str, change: dict) -> dict:
    """What a change names that an `etype` doesn't take, by field. Checked before the snapshot
    reads the entry: a name the model lacks (`type`, which `career` once showed for
    `job_type`) was an AttributeError there, a 500 with nothing to read."""
    fields, lists = FIELDS[etype], LISTS[etype]
    errors = {}
    for field in change.get("set") or {}:
        if field in lists:
            errors[field] = ["is a list: change it with add / remove."]
        elif field not in fields:
            errors[field] = [
                f"can't be changed here; a {etype} takes {', '.join(sorted(fields))}."
            ]
    for field in sorted({*(change.get("add") or {}), *(change.get("remove") or {})}):
        if field not in lists:
            has = ", ".join(sorted(lists)) or "none"
            errors[field] = [f"isn't a list a {etype} has (it has: {has})."]
    return errors


def entry_labels(user, ids) -> dict[str, str]:
    """Names for `type:pk` ids, so a reader sees "Docker" and not skill:12. Anything that isn't
    an id of the user's own entries is left out."""
    wanted: dict[str, set[int]] = defaultdict(set)
    for v in ids:
        if isinstance(v, str) and ENTRY_ID_RE.match(v):
            etype, pk = v.split(":")
            wanted[etype].add(int(pk))
    labels = {}
    for etype, pks in wanted.items():
        for obj in ENTRY_MODELS[etype].objects.filter(user=user, pk__in=pks):
            labels[entry_id(obj)] = entry_label(obj)
    return labels


def _labels(user, changes: list[dict]) -> dict[str, str]:
    """Names for the ids inside the changes' values."""
    values = [
        value
        for change in changes
        for side in (change["before"], change["after"])
        for value in (side or {}).values()
    ]
    return entry_labels(
        user,
        [
            v
            for value in values
            for v in (value if isinstance(value, list) else [value])
        ],
    )


def batch_payload(batch: CareerChange) -> dict:
    return {
        "id": batch.pk,
        "created_at": (batch.created_at or timezone.now()).isoformat(
            timespec="seconds"
        ),
        "source": batch.source,
        "undone_at": (
            batch.undone_at.isoformat(timespec="seconds") if batch.undone_at else None
        ),
        "changes": batch.changes,
        "stamps": batch.stamps,
        "labels": _labels(batch.user, batch.changes),
    }


def _apply(request, changes: list) -> tuple[list[dict], dict[str, str], dict[str, int]]:
    """Apply `changes` for `request.user` inside the caller's transaction. Returns the log, the
    stamps and the new skills' pks by ref; raises ChangeError or StaleError, and the caller's
    transaction rolls back."""
    user = request.user
    ctx = {"request": request}
    errors: dict[str, object] = {}
    # index -> (type, entry); the entry is None for a skill still to be created.
    targets: dict[int, tuple[str, object]] = {}
    ids, refs_declared, stale = set(), set(), []

    # 1. Every change's target, under a row lock, checked against what Claude read.
    for i, change in enumerate(changes):
        shape = _shape_errors(change)
        if shape:
            errors[str(i)] = shape
            continue
        if "new" in change:
            ref = change.get("ref")
            if change["new"] != "skill":
                errors[str(i)] = {
                    "new": [
                        (
                            "Only skills can be created here. A job, project or degree is "
                            "the user's to add in jac."
                        )
                    ]
                }
            elif not isinstance(ref, str) or not ref or ref in refs_declared:
                errors[str(i)] = {
                    "ref": ["A name, unique in this batch, that 'new:<ref>' links by."]
                }
            elif names := _name_errors("skill", change):
                errors[str(i)] = names
            else:
                refs_declared.add(ref)
                targets[i] = ("skill", None)
            continue
        cid = change.get("id")
        if not isinstance(cid, str) or not ENTRY_ID_RE.match(cid):
            errors[str(i)] = {"id": ["An entry id like 'job:3', from career."]}
            continue
        if cid in ids:
            errors[str(i)] = {"id": ["One change per entry: merge the two."]}
            continue
        ids.add(cid)
        etype, pk = cid.split(":")
        if names := _name_errors(etype, change):
            errors[str(i)] = names
            continue
        obj = (
            ENTRY_MODELS[etype]
            .objects.select_for_update()
            .filter(user=user, pk=int(pk))
            .first()
        )
        if obj is None:
            errors[str(i)] = {"id": ["Not found, or not yours."]}
            continue
        seen = _parse_time(change.get("seen"))
        if seen is None:
            errors[str(i)] = {
                "seen": ["The entry's updated_at, exactly as career shows it."]
            }
            continue
        if obj.updated_at != seen:
            stale.append(cid)
        targets[i] = (etype, obj)
    if errors:
        raise ChangeError(errors)
    if stale:
        raise StaleError(stale, "Changed in jac since Claude read them.")

    # 2. What every touched field held, before anything is written.
    befores = {
        i: _snapshot(obj, etype, _touched(changes[i]))
        for i, (etype, obj) in targets.items()
        if obj is not None
    }
    domains = _domains(user)

    # 3. The new skills, from their plain fields, so every change below can link them.
    refs: dict[str, int] = {}
    for i, (etype, obj) in list(targets.items()):
        if obj is not None:
            continue
        data, errs = _data(etype, {"set": changes[i].get("set")}, None, refs, domains)
        serializer = SkillSerializer(data=data, context=ctx)
        if errs or not serializer.is_valid():
            errors[str(i)] = errs or serializer.errors
            del targets[i]
            continue
        skill = serializer.save()
        refs[changes[i]["ref"]] = skill.pk
        targets[i] = (etype, skill)

    # 4. The edits' fields, and every change's lists.
    for i, (etype, obj) in targets.items():
        change = changes[i]
        if i not in befores:  # created above: only its lists are left
            change = {"add": change.get("add"), "remove": change.get("remove")}
            if not _touched(change):
                continue
        data, errs = _data(etype, change, obj, refs, domains)
        if errs:
            errors[str(i)] = errs
            continue
        serializer = SERIALIZERS[etype](obj, data=data, partial=True, context=ctx)
        if not serializer.is_valid():
            errors[str(i)] = serializer.errors
            continue
        serializer.save()
    if errors:
        raise ChangeError(errors)

    # 5. After, read back fresh, and the stamps an undo checks against.
    log, stamps = [], {}
    for i, (etype, obj) in sorted(targets.items()):
        obj = ENTRY_MODELS[etype].objects.get(pk=obj.pk)
        log.append(
            {
                "id": entry_id(obj),
                "label": entry_label(obj),
                "why": (changes[i].get("why") or "").strip()[:WHY_CHARS],
                "before": befores.get(i),
                "after": _snapshot(obj, etype, _touched(changes[i])),
            }
        )
        stamps[entry_id(obj)] = obj.updated_at.isoformat()
    return log, stamps, refs


def apply_changes(
    request, changes: list, *, source: str, dry_run: bool = False
) -> dict:
    """Apply a batch all or nothing and log it. A dry run validates and answers the same, then
    rolls everything back. Returns the batch as `batch_payload` shapes it, plus `created`: the
    ids the new skills got (`{"new:docker": "skill:44"}`).

    A dry run names a new skill `new:<ref>` throughout and creates nothing in `created`: the
    ids its rolled-back skills got are gone with them, and the real apply gets others (Postgres
    doesn't hand a rolled-back id out again)."""
    with transaction.atomic():
        log, stamps, refs = _apply(request, changes)
        batch = CareerChange(
            user=request.user, source=source, changes=log, stamps=stamps
        )
        if not dry_run:
            batch.save()
            old = CareerChange.objects.filter(user=request.user).values_list(
                "pk", flat=True
            )[CareerChange.KEEP :]
            CareerChange.objects.filter(pk__in=list(old)).delete()
        payload = {
            **batch_payload(batch),
            "dry_run": dry_run,
            "created": {f"new:{ref}": f"skill:{pk}" for ref, pk in refs.items()},
        }
        if dry_run:
            transaction.set_rollback(True)
            payload = _as_refs(
                {**payload, "created": {}},
                {f"skill:{pk}": f"new:{ref}" for ref, pk in refs.items()},
            )
    return payload


def _as_refs(value, names: dict[str, str]):
    """`value` with every id in `names` (keys and strings alike) put back as the `new:<ref>`
    the batch wrote."""
    if isinstance(value, dict):
        return {names.get(k, k): _as_refs(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [_as_refs(v, names) for v in value]
    if isinstance(value, str):
        return names.get(value, value)
    return value


def _restore(etype: str, before: dict, user, domains: dict[str, int]) -> dict:
    """A change's `before` as serializer input. What was deleted since drops out: a skill from
    a list, a link to a job that's gone."""
    data = {}
    for field, value in before.items():
        kind = LISTS[etype].get(field)
        if kind == "domain":
            data[field] = [domains[n] for n in value if n in domains]
        elif kind == "skill":
            pks = [int(v.split(":")[1]) for v in value]
            data[field] = list(
                Skill.objects.filter(user=user, pk__in=pks).values_list("pk", flat=True)
            )
        elif field in LINKS:
            pk = int(value.split(":")[1]) if value else None
            model = ENTRY_MODELS[LINKS[field]]
            exists = pk and model.objects.filter(user=user, pk=pk).exists()
            data[field] = pk if exists else None
        elif field == "degree_level":
            data[field] = Education.DegreeLevel[value].value
        else:
            data[field] = value
    return data


def undo_batch(request, pk: int) -> dict:
    """Put every entry the batch touched back as it was, and delete the skills it created
    (their links go with them). Refused when the batch is undone already, or when one of its
    entries was edited since: the undo would overwrite that edit."""
    user = request.user
    with transaction.atomic():
        batch = CareerChange.objects.select_for_update().get(pk=pk, user=user)
        if batch.undone_at:
            when = timezone.localtime(batch.undone_at)
            raise UndoRefused(f"Already undone, on {when:%Y-%m-%d %H:%M}.")
        entries, stale = {}, []
        for change in batch.changes:
            etype, epk = change["id"].split(":")
            obj = (
                ENTRY_MODELS[etype]
                .objects.select_for_update()
                .filter(user=user, pk=int(epk))
                .first()
            )
            entries[change["id"]] = obj
            stamp = _parse_time(batch.stamps.get(change["id"]))
            if obj is not None and obj.updated_at != stamp:
                stale.append(change["id"])
        if stale:
            raise StaleError(
                stale,
                "Edited in jac since this batch, so undoing it would overwrite that. "
                "Change these back by hand, or keep the batch.",
            )
        ctx = {"request": request}
        domains = _domains(user)
        errors = {}
        for change in reversed(batch.changes):
            obj = entries[change["id"]]
            if obj is None:  # deleted since: nothing to put back
                continue
            if change["before"] is None:
                obj.delete()
                continue
            etype = change["id"].split(":")[0]
            serializer = SERIALIZERS[etype](
                obj,
                data=_restore(etype, change["before"], user, domains),
                partial=True,
                context=ctx,
            )
            if not serializer.is_valid():
                errors[change["id"]] = serializer.errors
                continue
            serializer.save()
        if errors:
            raise ChangeError(errors)
        batch.undone_at = timezone.now()
        batch.save(update_fields=["undone_at"])
        return batch_payload(batch)
