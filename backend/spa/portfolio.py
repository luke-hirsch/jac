"""spa.portfolio — portfolio-link domain logic.

Slug minting and the application-link lifecycle (get-or-create + freeze-at-sent).
Guide 2 adds owner resolution, payload assembly, and the embed ranking here. Views
stay thin; everything in this module is unit-testable without HTTP.
"""

import logging
import random
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from jac.llm_prompts import Embed  # no spa import in jac.llm_prompts — cycle-safe
from llm_connector import complete
from llm_connector.conf import HIRSCHAI_PROVIDER
from llm_connector.executor import Executor
from llm_connector.probe import hirschai_reachable
from spa.models import PortfolioBlock, PortfolioLink, PortfolioVisit, UserProfile

logger = logging.getLogger(__name__)

SLUG_SUFFIX_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
SLUG_SUFFIX_LEN = 4


def slug_suffix() -> str:
    return "".join(secrets.choice(SLUG_SUFFIX_ALPHABET) for _ in range(SLUG_SUFFIX_LEN))


def _application_company(application) -> str:
    """Company preference: corrected letter recipient -> extracted posting address ->
    posting title -> fallback (unchanged from the entropy-slug era)."""
    address = getattr(application.posting, "address", None)
    return (
        ((application.letter_meta or {}).get("recipient") or {}).get("company")
        or (address.company if address else "")
        or application.posting.title
        or "application"
    )


def _dedupe_slug(user, base: str, extra: str = "") -> str:
    """First free slug among the user's ACTIVE links: `base`, then `base-extra`, then a
    numeric tail. Per-user now — the subdomain carries the user, so no cross-user entropy."""
    taken = set(
        PortfolioLink.objects.filter(user=user, revoked_at__isnull=True).values_list(
            "slug", flat=True
        )
    )
    candidates = [base]
    if extra and extra != base:
        candidates.append(f"{base}-{extra}")
    for c in candidates:
        if c and c not in taken:
            return c
    stem = candidates[-1]
    n = 2
    while f"{stem}-{n}" in taken:
        n += 1
    return f"{stem}-{n}"


def application_slug(application) -> str:
    """`<company>` or `<company>-<role>` — readable and owner-editable (per-user unique).

    A second application to the same company falls to `<company>-<role>` (e.g.
    `acme-intern` vs `acme-lead`), then a numeric tail. The owner can rename the link
    before the sent-freeze via the manage UI.
    """
    base = slugify(_application_company(application))[:40].strip("-") or "application"
    role = slugify(application.posting.title or "")[:20].strip("-")
    return _dedupe_slug(application.user, base, role)


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


def _configured_owner() -> User | None:
    """The apex owner — the single configured user behind `BASE_DOMAIN` itself (the SEO
    landing, and the fallback for request-less callers). Case-insensitive; never the system
    sentinel; None when unset/unknown/inactive."""
    username = (settings.PORTFOLIO_OWNER_USERNAME or "").strip()
    if not username or username.lower() == settings.SYSTEM_USER_USERNAME.lower():
        return None
    return User.objects.filter(username__iexact=username, is_active=True).first()


def owner_for_host(host: str) -> User | None:
    """Resolve the portfolio owner from a raw Host header. `<handle>.<BASE_DOMAIN>` -> that
    user; the bare `BASE_DOMAIN` (or `www.`) -> the configured apex owner; a reserved or
    unknown subdomain / foreign host -> None (callers 404). Pure + unit-testable."""
    host = (host or "").split(":")[0].lower().rstrip(".")
    base = settings.BASE_DOMAIN.lower()
    if host in (base, f"www.{base}"):
        return _configured_owner()
    suffix = f".{base}"
    if not host.endswith(suffix):
        return None
    handle = host[: -len(suffix)]
    if not handle or "." in handle or handle in settings.RESERVED_SUBDOMAINS:
        return None
    return User.objects.filter(profile__handle__iexact=handle, is_active=True).first()


def resolve_owner(request) -> User | None:
    """The public portfolio's owner for this request, from its Host header."""
    return owner_for_host(request.get_host())


def mint_handle(username: str) -> str:
    """A DNS-safe, reserved-aware, unique handle seeded from a username."""
    base = slugify(username)[:40].strip("-") or "user"
    if base in settings.RESERVED_SUBDOMAINS:
        base = f"{base}-1"
    candidate, n = base, 2
    while UserProfile.objects.filter(handle__iexact=candidate).exists():
        suffix = f"-{n}"
        candidate = f"{base[: 40 - len(suffix)]}{suffix}"
        n += 1
    return candidate


def public_portfolio_url(link) -> str:
    """The absolute public URL a QR encodes: the owner's origin + the link slug. Built from
    `PORTFOLIO_ORIGIN_TEMPLATE` so the domain lives in exactly one env var."""
    origin = settings.PORTFOLIO_ORIGIN_TEMPLATE.format(handle=link.user.profile.handle)
    return f"{origin.rstrip('/')}/{link.slug}"


def owner_domains(owner) -> list[str]:
    """Domain names the owner actually has content under (career entries or active
    blocks), sorted. Only non-empty domains — so no questionnaire branch dead-ends (the
    hardcoded-name bug). Language has no domains M2M and is excluded by construction."""
    names: set[str] = set()
    for t in ("job", "project", "skill", "education", "certification"):
        for obj in (
            _career_models()[t].objects.filter(user=owner).prefetch_related("domains")
        ):
            names.update(d.name for d in obj.domains.all())
    for b in PortfolioBlock.objects.filter(user=owner, is_active=True).prefetch_related(
        "domains"
    ):
        names.update(d.name for d in b.domains.all())
    return sorted(names)


def section_order(focus: str = "balanced") -> list[str]:
    """Career section order for the style axis' `focus` (soft↔technical). `technical`
    leads with skills/projects; `soft_skill` leads with roles/education; anything else
    keeps the default. Language always trails (no domains, thin content)."""
    if focus == "technical":
        return ["skill", "project", "job", "certification", "education", "language"]
    if focus == "soft_skill":
        return ["job", "education", "language", "project", "certification", "skill"]
    return list(_SECTION_ORDER)


def default_link(owner):
    """The owner's active `is_default` manual link, or None. The native fallback target."""
    return owner.portfolio_links.filter(
        is_default=True, revoked_at__isnull=True
    ).first()


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
    owner,
    exclude_ids: set | None = None,
    domains=None,
    favourite=False,
    order=None,
) -> list[dict]:
    """Career items in section order; `domains` (Domain rows) scopes, `favourite`
    restricts. Language has no domains M2M — it drops out of any domain-scoped view."""
    out = []
    for t in order or _SECTION_ORDER:
        model = _career_models()[t]
        qs = model.objects.filter(user=owner)
        if favourite:
            qs = qs.filter(favourite=True)
        if domains is not None:
            if t == "language":
                continue
            qs = qs.filter(domains__in=domains).distinct()
        if t != "language":
            qs = qs.prefetch_related("domains")
        out += [
            _career_item(t, o)
            for o in qs
            if exclude_ids is None or f"{t}:{o.pk}" not in exclude_ids
        ]
    return out


def _blocks(
    owner,
    exclude_ids: set | None = None,
    domains=None,
    favourite=False,
) -> list[dict]:
    qs = PortfolioBlock.objects.filter(user=owner, is_active=True).prefetch_related(
        "domains"
    )
    if favourite:
        qs = qs.filter(favourite=True)
    if domains is not None:
        qs = qs.filter(domains__in=domains).distinct()

    return [
        _block_item(b)
        for b in qs
        if exclude_ids is None or f"block:{b.pk}" not in exclude_ids
    ]


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


def build_payload(
    owner,
    link=None,
    domains=None,
    lucky=False,
    seed=None,
    focus="balanced",
    tone="neutral",
) -> dict:
    """The whole public page in one dict: `{owner, kind, title, intro, featured, more}`.

    Link mode (unchanged): frozen ids, application links preview live `cv_content`.
    Native mode: `domains` (names) scope; `focus` reorders career sections; `tone`
    ("personal") floats blocks ahead of career entries. `lucky` = favourites + a seeded
    random tasting menu. When a native result is EMPTY the flow degrades to the owner's
    default link (the standard portfolio) — no dead-end. Position IS the featured signal;
    the favourite flag never leaks.
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
        order = section_order(focus)
        blocks_first = tone == "personal"
        feat_e = _entries(owner, domains=scope, favourite=True, order=order)
        feat_b = _blocks(owner, domains=scope, favourite=True)
        featured = (feat_b + feat_e) if blocks_first else (feat_e + feat_b)
        exclude = {i["id"] for i in featured}
        more_e = _entries(owner, domains=scope, exclude_ids=exclude, order=order)
        more_b = _blocks(owner, domains=scope, exclude_ids=exclude)
        more = (more_b + more_e) if blocks_first else (more_e + more_b)

    if not featured and not more:
        default = default_link(owner)
        if default is not None:
            payload = build_payload(owner, link=default)
            payload["kind"] = (
                "native"  # rendered via the native flow, not a shared link
            )
            return payload

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


# ------ AI stuff

_INTRO_TONE = {
    "personal": "Warm, first-person and genuine, as if greeting the visitor directly.",
    "neutral": "Professional with measured warmth.",
    "formal": "Reserved and professional.",
}
_INTRO_FOCUS = {
    "technical": "Emphasise concrete technical work.",
    "soft_skill": "Emphasise motivation, values and working style.",
    "balanced": "Give craft and character roughly equal weight.",
}


class PortfolioIntroWriter:
    """One short personalised welcome paragraph for a native portfolio visit."""

    _TARGET_WORDS = (40, 80)
    _INSTRUCTION = (
        "Write ONE short welcome paragraph ({lo}-{hi} words) for a visitor to {name}'s "
        "personal portfolio. The visitor is interested in: {interest}. Write in the first "
        "person as {name}. Reference ONLY that interest and the HIGHLIGHTS listed below — "
        "invent no skills, employers, titles, numbers or dates. No header, no sign-off, no "
        "markdown, no lists — just the paragraph."
    )

    def __init__(self, name, interest, highlights, focus="balanced", tone="neutral"):
        self.name = name or "the owner"
        self.interest = interest or "your work in general"
        self.highlights = highlights
        self.focus = focus
        self.tone = tone

    def write(self) -> str:
        """The paragraph, or '' (tower unreachable / any failure — the caller then
        renders the standard portfolio without an intro)."""
        if not hirschai_reachable():
            return ""
        try:
            raw = complete(prompt=self._prompt(), executor=Executor(HIRSCHAI_PROVIDER))
        except Exception:
            logger.exception("PortfolioIntroWriter: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        lo, hi = self._TARGET_WORDS
        flavour = " ".join(
            v for v in (_INTRO_TONE.get(self.tone), _INTRO_FOCUS.get(self.focus)) if v
        )
        highlights = "\n".join(f"- {h}" for h in self.highlights[:12]) or "(none yet)"
        return (
            self._INSTRUCTION.format(
                lo=lo, hi=hi, name=self.name, interest=self.interest
            )
            + (f"\n{flavour}" if flavour else "")
            + f"\n\nHIGHLIGHTS:\n{highlights}\n\nWELCOME PARAGRAPH:"
        )


def build_intro(
    owner, *, domains=None, question="", focus="balanced", tone="neutral"
) -> str:
    """Assemble + run the intro for a native visit. Grounds the writer in the actual
    featured highlights for this (domains, style) selection."""
    payload = build_payload(owner, domains=domains, focus=focus, tone=tone)
    highlights = [
        i.get("title") or i.get("subtitle") or "" for i in payload["featured"]
    ]
    interest = ", ".join(domains or [])
    if question:
        interest = f"{interest}; specifically: {question}".strip(" ;")
    return PortfolioIntroWriter(
        name=payload["owner"]["display_name"],
        interest=interest,
        highlights=[h for h in highlights if h],
        focus=focus,
        tone=tone,
    ).write()


def landing_context(request) -> dict:
    """Context for the Django-rendered landing at the apex/handle host."""
    owner = resolve_owner(request)
    return {
        "owner": _owner_block(owner) if owner else None,
        "domains": owner_domains(owner) if owner else [],
        "explore_url": settings.FRONTEND_URL,  # guide 3: the handle-root questionnaire
        "signup_url": f"{settings.FRONTEND_URL}/auth/signup",
    }
