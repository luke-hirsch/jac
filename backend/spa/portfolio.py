"""spa.portfolio — portfolio-link domain logic.

Slug minting and the application-link lifecycle (get-or-create + freeze-at-sent).
Guide 2 adds owner resolution, payload assembly, and the embed ranking here. Views
stay thin; everything in this module is unit-testable without HTTP.
"""

import random
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify
from jac.llm_prompts import Embed  # no spa import in jac.llm_prompts — cycle-safe

from spa.models import PortfolioBlock, PortfolioLink, PortfolioVisit

SLUG_SUFFIX_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
SLUG_SUFFIX_LEN = 4


def slug_suffix() -> str:
    return "".join(secrets.choice(SLUG_SUFFIX_ALPHABET) for _ in range(SLUG_SUFFIX_LEN))


def application_slug(application) -> str:
    """`<company>-<suffix>` — readable but not guessable.

    Company preference: the user-corrected letter recipient
    (`letter_meta["recipient"]["company"]`, jac/cover_letter.py:366-379) → the extracted
    posting address → the posting title → a plain fallback. `getattr` on the reverse
    one-to-one is safe: Django's RelatedObjectDoesNotExist subclasses AttributeError.
    """
    address = getattr(application.posting, "address", None)
    company = (
        ((application.letter_meta or {}).get("recipient") or {}).get("company")
        or (address.company if address else "")
        or application.posting.title
        or "application"
    )
    base = slugify(company)[:40].strip("-") or "application"
    return f"{base}-{slug_suffix()}"


def link_for_application(application) -> PortfolioLink:
    """The application's active portfolio link, minted on first request (idempotent).

    The loop re-checks + re-rolls: an IntegrityError is either a slug collision (re-roll
    the suffix) or a concurrent create tripping `one_active_link_per_application` (the
    re-check then returns the winner's row).
    """
    for _ in range(5):
        existing = application.portfolio_links.filter(revoked_at__isnull=True).first()
        if existing:
            return existing
        try:
            with transaction.atomic():
                return PortfolioLink.objects.create(
                    user=application.user,
                    kind=PortfolioLink.Kind.application,
                    slug=application_slug(application),
                    application=application,
                    title=application.posting.title,
                )
        except IntegrityError:
            continue
    raise IntegrityError("could not mint a unique portfolio slug after 5 attempts")


def freeze_link(application) -> None:
    """Snapshot the sent selection into the application's active link (idempotent).

    Called from the `sent` transition: `featured` = the application's kept entry ids in
    cv_content section order (rows the editor flagged `deselected` drop out), `domains`
    = the latest done run's scope (empty → guide 2's explore-more falls back to
    favourites + blocks). A link that already carries a featured list is left alone, so
    re-entering sent-era statuses never rewrites the page the recruiter saw.
    """
    link = application.portfolio_links.filter(revoked_at__isnull=True).first()
    if link is None or link.content.get("featured"):
        return
    featured = [
        row["id"]
        for rows in (application.cv_content or {}).values()
        for row in rows
        if row.get("id") and not row.get("deselected")
    ]
    run = (  # status string == GenerationRun.Status.done — no jac import needed
        application.runs.filter(status="done").order_by("-created_at").first()
    )
    link.content = {
        **link.content,
        "featured": featured,
        "domains": list(run.domains) if run and run.domains else [],
    }
    link.save(update_fields=["content", "updated_at"])


def get_owner() -> User | None:
    """The public portfolio's owner — an explicit setting, never request-derived and
    never the system sentinel. None (unset / unknown / inactive) disables the native
    flow: callers 404."""
    username = settings.PORTFOLIO_OWNER_USERNAME
    if not username or username == settings.SYSTEM_USER_USERNAME:
        return None
    return User.objects.filter(username=username, is_active=True).first()


# ── payload assembly ──────────────────────────────────────────────────────────
# Hand-built allowlist dicts, deliberately not ModelSerializers: a field absent from
# a builder cannot leak. Never add email / phone / street / zip / notes / status /
# letter fields here — entry `description` is owner-authored CV text, public by intent.

_SECTION_ORDER = ["job", "project", "skill", "education", "certification", "language"]


def _career_models() -> dict:
    from jac import models as jac_models  # runtime-only; spa models use string refs

    return {
        "skill": jac_models.Skill,
        "job": jac_models.Job,
        "education": jac_models.Education,
        "certification": jac_models.Certification,
        "project": jac_models.Project,
        "language": jac_models.Language,
    }


def _career_item(type_name: str, obj) -> dict:
    """One public item. Dates stay `date` objects — DRF's encoder ISO-formats them."""
    item = {
        "id": f"{type_name}:{obj.pk}",
        "type": type_name,
        "title": "",
        "subtitle": "",
        "description": obj.description or "",
        "started": None,
        "ended": None,
        "url": "",
        "domains": [],
    }
    if type_name != "language":
        item["domains"] = sorted(d.name for d in obj.domains.all())
    if type_name == "skill":
        item.update(title=obj.name, subtitle=obj.get_proficiency_display())
    elif type_name == "job":
        item.update(
            title=obj.title,
            subtitle=obj.company,
            started=obj.started,
            ended=obj.ended,
            url=obj.url,
        )
    elif type_name == "project":
        item.update(
            title=obj.name,
            subtitle=obj.job.company if obj.job else "",
            started=obj.started,
            ended=obj.ended,
            url=obj.url,
        )
    elif type_name == "education":
        item.update(
            title=obj.degree or obj.field_of_study,
            subtitle=obj.institution,
            started=obj.started,
            ended=obj.ended,
        )
    elif type_name == "certification":
        item.update(
            title=obj.name, subtitle=obj.issuer, started=obj.issued_on, url=obj.url
        )
    elif type_name == "language":
        item.update(title=obj.name, subtitle=obj.get_fluency_display())
    return item


def _block_item(block: PortfolioBlock) -> dict:
    return {
        "id": f"block:{block.pk}",
        "type": "block",
        "kind": block.kind,
        "title": block.title,
        "body": block.body,
        # MEDIA_URL-relative; dev Vite proxies /media, prod nginx must serve it.
        "image_url": block.image.url if block.image else None,
        "alt_text": block.alt_text,
        "domains": sorted(d.name for d in block.domains.all()),
    }


def _matched_domains(owner, names: list[str]) -> list:
    """Case-insensitive join of requested names against the owner's visible taxonomy —
    forgiving of drift between the frontend questionnaire constants and the DB tags."""
    from jac.models import Domain

    lowered = {n.strip().lower() for n in names if n and n.strip()}
    if not lowered:
        return []
    return [d for d in Domain.objects.for_user(owner) if d.name.lower() in lowered]


def resolve_items(owner, ids: list[str]) -> list[dict]:
    """Join featured ids against the live DB, preserving order. Dead / foreign /
    malformed ids drop silently (the cv-doc philosophy: selection frozen, text live)."""
    wanted: dict[str, list[int]] = {}
    for i in ids:
        t, _, pk = i.partition(":")
        if pk.isdigit():
            wanted.setdefault(t, []).append(int(pk))
    found: dict[str, dict] = {}
    models_map = _career_models()
    for t, pks in wanted.items():
        if t == "block":
            rows = PortfolioBlock.objects.filter(
                user=owner, is_active=True, pk__in=pks
            ).prefetch_related("domains")
            found.update({f"block:{b.pk}": _block_item(b) for b in rows})
        elif t in models_map:
            qs = models_map[t].objects.filter(user=owner, pk__in=pks)
            if t != "language":
                qs = qs.prefetch_related("domains")
            found.update({f"{t}:{o.pk}": _career_item(t, o) for o in qs})
    return [found[i] for i in ids if i in found]


def _entries(
    owner, *, domains=None, favourite=False, exclude_ids=frozenset()
) -> list[dict]:
    """Career items in section order; `domains` (Domain rows) scopes, `favourite`
    restricts. Language has no domains M2M — it drops out of any domain-scoped view."""
    out = []
    for t, model in ((t, _career_models()[t]) for t in _SECTION_ORDER):
        qs = model.objects.filter(user=owner)
        if favourite:
            qs = qs.filter(favourite=True)
        if domains is not None:
            if t == "language":
                continue
            qs = qs.filter(domains__in=domains).distinct()
        if t != "language":
            qs = qs.prefetch_related("domains")
        out += [_career_item(t, o) for o in qs if f"{t}:{o.pk}" not in exclude_ids]
    return out


def _blocks(
    owner, *, domains=None, favourite=False, exclude_ids=frozenset()
) -> list[dict]:
    qs = PortfolioBlock.objects.filter(user=owner, is_active=True).prefetch_related(
        "domains"
    )
    if favourite:
        qs = qs.filter(favourite=True)
    if domains is not None:
        qs = qs.filter(domains__in=domains).distinct()
    return [_block_item(b) for b in qs if f"block:{b.pk}" not in exclude_ids]


def _owner_block(owner) -> dict:
    """Public identity card. Socials only when the profile opts in (`show_socials`,
    spa/models.py:57); never email / phone / postal address."""
    profile = owner.profile
    full = f"{owner.first_name} {owner.last_name}".strip()
    block = {
        "display_name": profile.display_name or full or owner.username,
        "bio": profile.bio,
        "avatar_url": profile.avatar.url if profile.avatar else None,
    }
    if profile.show_socials:
        block.update(
            website=profile.website,
            linkedin_url=profile.linkedin_url,
            github_url=profile.github_url,
        )
    return block


def build_payload(owner, *, link=None, domains=None, lucky=False, seed=None) -> dict:
    """The whole public page in one dict: `{owner, kind, title, intro, featured, more}`.

    Link mode: featured = the link's frozen ids (application links fall back to the
    live `cv_content` while un-frozen — accurate preview before `sent`); `more` = the
    link's domain scope, or favourites + blocks when unscoped; `hide_explore` empties it.
    Native mode: `domains` (names) scope both lists; no/unknown domains = the full
    portfolio (favourites featured). `lucky` = favourites featured + a seeded random
    tasting menu (fresh serendipity per request; `seed` keeps tests deterministic).
    Position IS the featured signal — the favourite flag itself never leaks.
    """
    if link is not None:
        content = link.content or {}
        featured_ids = list(content.get("featured") or [])
        if (
            not featured_ids
            and link.kind == PortfolioLink.Kind.application
            and link.application
        ):
            featured_ids = [
                row["id"]
                for rows in (link.application.cv_content or {}).values()
                for row in rows
                if row.get("id") and not row.get("deselected")
            ]
        featured = resolve_items(owner, featured_ids)
        more: list[dict] = []
        if not content.get("hide_explore"):
            exclude = {i["id"] for i in featured}
            matched = _matched_domains(owner, content.get("domains") or [])
            if matched:
                more = _entries(owner, domains=matched, exclude_ids=exclude)
                more += _blocks(owner, domains=matched, exclude_ids=exclude)
            else:
                more = _entries(owner, favourite=True, exclude_ids=exclude)
                more += _blocks(owner, exclude_ids=exclude)
        return {
            "kind": link.kind,
            "title": link.title,
            "intro": link.intro,
            "owner": _owner_block(owner),
            "featured": featured,
            "more": more,
        }

    if lucky:
        featured = _entries(owner, favourite=True) + _blocks(owner, favourite=True)
        exclude = {i["id"] for i in featured}
        pool = _entries(owner, exclude_ids=exclude) + _blocks(
            owner, exclude_ids=exclude
        )
        rng = random.Random(seed)
        more = rng.sample(pool, min(10, len(pool)))
    else:
        matched = _matched_domains(owner, domains or [])
        scope = matched or None  # nothing matched → the full portfolio
        featured = _entries(owner, domains=scope, favourite=True)
        featured += _blocks(owner, domains=scope, favourite=True)
        exclude = {i["id"] for i in featured}
        more = _entries(owner, domains=scope, exclude_ids=exclude)
        more += _blocks(owner, domains=scope, exclude_ids=exclude)
    return {
        "kind": "native",
        "title": "",
        "intro": "",
        "owner": _owner_block(owner),
        "featured": featured,
        "more": more,
    }


def bump_visit(link: PortfolioLink) -> None:
    """Race-safe daily bump: get_or_create the bucket, then an F() increment."""
    row, _ = PortfolioVisit.objects.get_or_create(link=link, day=timezone.localdate())
    PortfolioVisit.objects.filter(pk=row.pk).update(count=F("count") + 1)


# ── embed finale ──────────────────────────────────────────────────────────────


class PortfolioEmbed(Embed):
    """Visitor free-text interest → entry ranking. Tower-only by construction:
    `llm_connector.embed` hard-routes to HirschAI (never a commercial key), and there
    is no generative call anywhere in the anonymous path. The SnippetEmbed pattern
    (jac/llm_prompts.py:126-134)."""

    _EMBED_INSTRUCT = (
        "Given a visitor's stated interest, retrieve the portfolio entries most "
        "relevant to it."
    )
    DOC_KIND = "portfolio"


MAX_RANK_DOCS = 200  # caps one rank call's tower work


def rank_for_query(owner, query: str, domains: list[str]) -> list[dict]:
    """Cosine-rank the owner's entries + blocks against `query`. `user=None` keeps the
    Qdrant store path off (no "portfolio" doc kind yet — classic per-call embed;
    store-backed is a later optimisation, see jac/vectors.py)."""
    from jac.cv import CV

    matched = _matched_domains(owner, domains)
    cv = CV(owner.pk, domains=[d.name for d in matched] or None)
    # Deliberate reuse of the pipeline's flattener (private by name only); promote it
    # to a public method if a third caller appears.
    docs = [{"id": e["id"], "text": e["text"]} for e in cv._flatten_entries()]
    blocks = PortfolioBlock.objects.filter(user=owner, is_active=True)
    if matched:
        blocks = blocks.filter(domains__in=matched).distinct()
    docs += [
        {"id": f"block:{b.pk}", "text": f"{b.title}\n{b.body}".strip()} for b in blocks
    ]
    docs = docs[:MAX_RANK_DOCS]
    if not docs:
        return []
    ranked = PortfolioEmbed(query, docs, user=None).ranked_entries()
    return [{"id": r["id"], "score": round(r["score"], 4)} for r in ranked]
