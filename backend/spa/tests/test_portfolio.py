"""Portfolio tests — the flow-rework + host-resolution acceptance set.

Covers the flow-rework payload/intro logic (`owner_domains` / `section_order` /
`default_link` / `PortfolioIntroWriter` / `build_intro`) and the multi-user host-resolution
guide (`owner_for_host` / `mint_handle` / per-user slugs / `application_slug` /
`public_portfolio_url`). Owner is resolved from the request host, so the endpoint classes
pin `BASE_DOMAIN="testserver"` (the default test host) → apex → configured owner.

LLM calls are always mocked (`spa.portfolio.complete`, `spa.portfolio.hirschai_reachable`) —
no live tower in tests, mirroring the rank discipline.
"""

import json
from datetime import date
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from jac.models import Domain, Job, JobApplication, JobPostAddress, JobPosting, Skill

from spa.models import PortfolioBlock, PortfolioLink
from spa.portfolio import (
    PortfolioIntroWriter,
    application_slug,
    build_intro,
    build_payload,
    default_link,
    mint_handle,
    owner_domains,
    owner_for_host,
    public_portfolio_url,
    resolve_items,
    section_order,
)


def _owner(username="lukas"):
    """A portfolio owner. UserProfile + PersonalityProfile are auto-created by signals."""
    return User.objects.create_user(username=username, password="pw")


def _job(owner, favourite=False, title="Dev", company="Acme"):
    return Job.objects.create(
        user=owner,
        title=title,
        company=company,
        started=date(2020, 1, 1),
        favourite=favourite,
    )


def _skill(owner, favourite=False, name="Python"):
    return Skill.objects.create(user=owner, name=name, favourite=favourite)


def _application(owner, company="Acme", role="Backend Engineer"):
    """A JobApplication whose company (via the extracted address) and role (posting
    title) drive `application_slug`."""
    posting = JobPosting.objects.create(
        user=owner, posting_text="We are hiring.", title=role
    )
    JobPostAddress.objects.create(job_posting=posting, company=company)
    return JobApplication.objects.create(user=owner, posting=posting)


# ── host-based owner resolution (multi-user portfolios) ─────────────────────────


@override_settings(BASE_DOMAIN="localhost", PORTFOLIO_OWNER_USERNAME="lukas")
class OwnerForHostTests(TestCase):
    def test_handle_subdomain_resolves_that_user(self):
        jane = _owner("jane")  # the signal mints handle "jane" from the username
        self.assertEqual(owner_for_host("jane.localhost"), jane)

    def test_apex_resolves_configured_owner(self):
        lukas = _owner("lukas")
        _owner("jane")  # a second user must never be picked at the apex
        self.assertEqual(owner_for_host("localhost"), lukas)

    def test_www_resolves_configured_owner(self):
        lukas = _owner("lukas")
        self.assertEqual(owner_for_host("www.localhost"), lukas)

    def test_reserved_subdomain_is_none(self):
        self.assertIsNone(owner_for_host("app.localhost"))

    def test_unknown_handle_is_none(self):
        self.assertIsNone(owner_for_host("nobody.localhost"))

    def test_foreign_host_is_none(self):
        _owner("jane")  # a valid handle must not resolve on a foreign apex
        self.assertIsNone(owner_for_host("jane.evil.com"))

    def test_port_is_stripped(self):
        jane = _owner("jane")
        self.assertEqual(owner_for_host("jane.localhost:8000"), jane)

    def test_inactive_user_is_none(self):
        User.objects.create_user(username="jane", password="pw", is_active=False)
        self.assertIsNone(owner_for_host("jane.localhost"))

    @override_settings(PORTFOLIO_OWNER_USERNAME="Lukas")
    def test_apex_owner_is_case_insensitive(self):
        # Configured "Lukas" (capital L) resolves the "lukas" row — the exact-match
        # 404 bug, now on the apex host.
        lukas = _owner("lukas")
        self.assertEqual(owner_for_host("localhost"), lukas)

    @override_settings(PORTFOLIO_OWNER_USERNAME="system")
    def test_apex_sentinel_owner_is_none(self):
        _owner("lukas")
        self.assertIsNone(owner_for_host("localhost"))

    @override_settings(PORTFOLIO_OWNER_USERNAME="")
    def test_apex_unset_owner_is_none(self):
        _owner("lukas")
        self.assertIsNone(owner_for_host("localhost"))


class MintHandleTests(TestCase):
    def test_slugifies_username(self):
        self.assertEqual(mint_handle("Jane Doe"), "jane-doe")

    @override_settings(RESERVED_SUBDOMAINS={"app", "www", "api"})
    def test_reserved_base_is_suffixed(self):
        self.assertEqual(mint_handle("app"), "app-1")

    def test_collision_increments(self):
        _owner("lukas")  # the signal takes handle "lukas"
        self.assertEqual(mint_handle("lukas"), "lukas-2")


class HandleClaimTests(TestCase):
    """Authed `PATCH /api/spa/profile/` claiming a portfolio handle — the writable
    `handle` field routed through `validate_handle` (normalize → reserved guard →
    case-insensitive uniqueness → min length)."""

    PROFILE_URL = "/api/spa/profile/"

    def setUp(self):
        self.user = _owner("claimer")  # the signal mints handle "claimer"
        self.client.force_login(self.user)

    def _patch(self, handle):
        return self.client.patch(
            self.PROFILE_URL,
            data=json.dumps({"handle": handle}),
            content_type="application/json",
        )

    def test_free_handle_is_normalized_and_saved(self):
        # The headline promise: a display-name-shaped input becomes a slug. Requires the
        # serializer CharField override — the model's SlugField would 400 on the space.
        r = self._patch("Jane Doe")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["handle"], "jane-doe")
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.handle, "jane-doe")

    @override_settings(RESERVED_SUBDOMAINS={"app"})
    def test_reserved_handle_is_rejected(self):
        r = self._patch("app")
        self.assertEqual(r.status_code, 400)
        self.assertIn("handle", r.json())

    def test_handle_taken_by_another_user_is_rejected(self):
        _owner("jane")  # the signal takes handle "jane"
        r = self._patch("Jane")  # normalizes to "jane" → clash
        self.assertEqual(r.status_code, 400)
        self.assertIn("handle", r.json())

    def test_too_short_handle_is_rejected(self):
        r = self._patch("a")
        self.assertEqual(r.status_code, 400)
        self.assertIn("handle", r.json())

    def test_keeping_own_handle_is_allowed(self):
        # Re-submitting the handle you already hold must not trip the uniqueness guard
        # (the validator excludes self.instance).
        r = self._patch("claimer")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["handle"], "claimer")


class PerUserSlugTests(TestCase):
    def _link(self, user, slug):
        return PortfolioLink.objects.create(
            user=user, kind=PortfolioLink.Kind.manual, slug=slug
        )

    def test_two_users_can_share_a_slug(self):
        a, b = _owner("a"), _owner("b")
        self._link(a, "acme")
        self._link(b, "acme")  # the host carries the user → no cross-user clash
        self.assertEqual(PortfolioLink.objects.filter(slug="acme").count(), 2)

    def test_same_user_cannot_hold_two_active(self):
        a = _owner("a")
        self._link(a, "acme")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._link(a, "acme")

    def test_revoked_slug_is_freed(self):
        a = _owner("a")
        first = self._link(a, "acme")
        first.revoke()
        self._link(a, "acme")  # revoked ≡ gone → the slug is free to reuse
        self.assertEqual(
            PortfolioLink.objects.filter(
                user=a, slug="acme", revoked_at__isnull=True
            ).count(),
            1,
        )


class ApplicationSlugTests(TestCase):
    def test_first_application_is_company(self):
        owner = _owner()
        app = _application(owner, company="Acme", role="Backend Engineer")
        self.assertEqual(application_slug(app), "acme")

    def test_second_same_company_falls_to_company_role(self):
        owner = _owner()
        app1 = _application(owner, company="Acme", role="Backend Engineer")
        PortfolioLink.objects.create(
            user=owner,
            kind=PortfolioLink.Kind.application,
            slug=application_slug(app1),
            application=app1,
        )
        app2 = _application(owner, company="Acme", role="Frontend Engineer")
        self.assertEqual(application_slug(app2), "acme-frontend-engineer")


@override_settings(PORTFOLIO_ORIGIN_TEMPLATE="http://{handle}.example.test")
class PublicPortfolioUrlTests(TestCase):
    def test_url_uses_handle_and_slug(self):
        jane = _owner("jane")  # handle "jane"
        link = PortfolioLink.objects.create(
            user=jane, kind=PortfolioLink.Kind.manual, slug="acme"
        )
        self.assertEqual(public_portfolio_url(link), "http://jane.example.test/acme")


# ── dynamic domains + style axis ────────────────────────────────────────────────


class OwnerDomainsTests(TestCase):
    def test_only_domains_with_content_sorted(self):
        owner = _owner()
        d_sw = Domain.objects.create(user=owner, name="software development")
        d_music = Domain.objects.create(user=owner, name="music")
        Domain.objects.create(user=owner, name="empty domain")  # no content
        job = _job(owner)
        job.domains.add(d_sw)
        block = PortfolioBlock.objects.create(user=owner, kind="text", body="hi")
        block.domains.add(d_music)
        self.assertEqual(
            owner_domains(owner), ["music", "software development"]
        )


class SectionOrderTests(TestCase):
    _DEFAULT = ["job", "project", "skill", "education", "certification", "language"]

    def test_technical_leads_with_skills(self):
        self.assertEqual(section_order("technical")[0], "skill")

    def test_soft_skill_leads_with_roles(self):
        self.assertEqual(section_order("soft_skill")[0], "job")

    def test_balanced_and_unknown_are_default(self):
        self.assertEqual(section_order("balanced"), self._DEFAULT)
        self.assertEqual(section_order("whatever"), self._DEFAULT)


class BuildPayloadStyleTests(TestCase):
    def test_focus_reorders_featured(self):
        owner = _owner()
        job = _job(owner, favourite=True)
        skill = _skill(owner, favourite=True)
        tech = [i["id"] for i in build_payload(owner, focus="technical")["featured"]]
        self.assertLess(
            tech.index(f"skill:{skill.pk}"), tech.index(f"job:{job.pk}")
        )
        soft = [i["id"] for i in build_payload(owner, focus="soft_skill")["featured"]]
        self.assertLess(
            soft.index(f"job:{job.pk}"), soft.index(f"skill:{skill.pk}")
        )

    def test_tone_personal_floats_blocks_first(self):
        owner = _owner()
        job = _job(owner, favourite=True)
        block = PortfolioBlock.objects.create(
            user=owner, kind="text", body="hi", favourite=True
        )
        personal = [i["id"] for i in build_payload(owner, tone="personal")["featured"]]
        self.assertLess(
            personal.index(f"block:{block.pk}"), personal.index(f"job:{job.pk}")
        )
        neutral = [i["id"] for i in build_payload(owner, tone="neutral")["featured"]]
        self.assertLess(
            neutral.index(f"job:{job.pk}"), neutral.index(f"block:{block.pk}")
        )


# ── default / standard-portfolio fallback ───────────────────────────────────────


class DefaultFallbackTests(TestCase):
    def test_empty_native_falls_back_to_default_link(self):
        owner = _owner()
        Domain.objects.create(user=owner, name="empty")  # exists, no entries
        job = _job(owner)  # no domain, not favourite -> not in the scoped view
        PortfolioLink.objects.create(
            user=owner,
            kind=PortfolioLink.Kind.manual,
            slug="standard",
            is_default=True,
            content={"featured": [f"job:{job.pk}"], "domains": [], "hide_explore": True},
        )
        # Scoped to a domain with no content -> empty -> fallback to the default link.
        payload = build_payload(owner, domains=["empty"])
        self.assertIn(f"job:{job.pk}", [i["id"] for i in payload["featured"]])
        self.assertEqual(payload["kind"], "native")

    def test_empty_native_without_default_stays_empty(self):
        owner = _owner()
        Domain.objects.create(user=owner, name="empty")
        payload = build_payload(owner, domains=["empty"])
        self.assertEqual(payload["featured"], [])
        self.assertEqual(payload["more"], [])


class IsDefaultExclusivityTests(TestCase):
    def test_second_default_demotes_the_first(self):
        owner = _owner()
        a = PortfolioLink.objects.create(
            user=owner, kind=PortfolioLink.Kind.manual, slug="a", is_default=True
        )
        b = PortfolioLink.objects.create(
            user=owner, kind=PortfolioLink.Kind.manual, slug="b", is_default=True
        )
        a.refresh_from_db()
        self.assertFalse(a.is_default)
        self.assertTrue(PortfolioLink.objects.get(pk=b.pk).is_default)

    def test_default_link_helper_ignores_revoked(self):
        owner = _owner()
        a = PortfolioLink.objects.create(
            user=owner, kind=PortfolioLink.Kind.manual, slug="a", is_default=True
        )
        self.assertEqual(default_link(owner), a)
        a.revoke()
        self.assertIsNone(default_link(owner))


# ── the AI intro (the one generative anonymous call) ────────────────────────────


class PortfolioIntroWriterTests(TestCase):
    @mock.patch("spa.portfolio.hirschai_reachable", return_value=True)
    @mock.patch("spa.portfolio.complete", return_value="Welcome, traveller.")
    def test_write_returns_text(self, m_complete, m_reach):
        w = PortfolioIntroWriter(name="Lukas", interest="music", highlights=["A role"])
        self.assertEqual(w.write(), "Welcome, traveller.")
        m_complete.assert_called_once()

    @mock.patch("spa.portfolio.hirschai_reachable", return_value=False)
    @mock.patch("spa.portfolio.complete")
    def test_unreachable_tower_returns_empty(self, m_complete, m_reach):
        w = PortfolioIntroWriter(name="Lukas", interest="music", highlights=[])
        self.assertEqual(w.write(), "")
        m_complete.assert_not_called()

    @mock.patch("spa.portfolio.hirschai_reachable", return_value=True)
    @mock.patch("spa.portfolio.complete", side_effect=RuntimeError("boom"))
    def test_llm_failure_returns_empty(self, m_complete, m_reach):
        with self.assertLogs("spa.portfolio", level="ERROR"):
            self.assertEqual(
                PortfolioIntroWriter("Lukas", "music", []).write(), ""
            )

    @mock.patch("spa.portfolio.hirschai_reachable", return_value=True)
    @mock.patch("spa.portfolio.complete", return_value="Grounded intro.")
    def test_build_intro_wires_a_payload(self, m_complete, m_reach):
        owner = _owner()
        _job(owner, favourite=True)
        self.assertEqual(
            build_intro(owner, domains=["music"], question="gigs", tone="personal"),
            "Grounded intro.",
        )


# ── public endpoints (meta / intro) + landing ───────────────────────────────────


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas", BASE_DOMAIN="testserver")
class PublicPortfolioEndpointTests(TestCase):
    def setUp(self):
        cache.clear()  # reset scoped-throttle counters between tests
        self.owner = _owner("lukas")

    def test_meta_returns_domains_and_style_vocab(self):
        d = Domain.objects.create(user=self.owner, name="music")
        job = _job(self.owner)
        job.domains.add(d)
        r = self.client.get("/api/spa/portfolio/native/meta/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("music", body["domains"])
        self.assertTrue(any(t["value"] == "neutral" for t in body["tones"]))
        self.assertTrue(any(f["value"] == "balanced" for f in body["focuses"]))

    @override_settings(PORTFOLIO_OWNER_USERNAME="")
    def test_meta_404_without_owner(self):
        self.assertEqual(
            self.client.get("/api/spa/portfolio/native/meta/").status_code, 404
        )

    @mock.patch("spa.portfolio.hirschai_reachable", return_value=True)
    @mock.patch("spa.portfolio.complete", return_value="Hello there.")
    def test_intro_returns_paragraph(self, m_complete, m_reach):
        r = self.client.post(
            "/api/spa/portfolio/native/intro/",
            data={
                "domains": ["music"],
                "query": "gigs",
                "tone": "personal",
                "focus": "technical",
            },
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["intro"], "Hello there.")

    def test_intro_rejects_overlong_query(self):
        r = self.client.post(
            "/api/spa/portfolio/native/intro/",
            data={"query": "x" * 1000},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    @override_settings(PORTFOLIO_OWNER_USERNAME="")
    def test_intro_404_without_owner(self):
        r = self.client.post(
            "/api/spa/portfolio/native/intro/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 404)


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas", BASE_DOMAIN="testserver")
class LandingTests(TestCase):
    def test_landing_renders_owner_html(self):
        User.objects.create_user(
            username="lukas", password="pw", first_name="Lukas", last_name="H"
        )
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r["Content-Type"])
        self.assertContains(r, "Lukas")

    def test_health_endpoint(self):
        r = self.client.get("/health/")
        self.assertEqual(r.status_code, 200)


# ── block links (guide [fullstack]-block-links) ─────────────────────────────────


class BlockLinksResolveTests(TestCase):
    """`resolve_items` nests a block's `links` one level deep, self-excluded, dead-id-safe."""

    def test_block_nests_linked_entry_one_level(self):
        owner = _owner()
        job = _job(owner)
        block = PortfolioBlock.objects.create(
            user=owner, kind="text", body="hi", links=[f"job:{job.pk}"]
        )
        [item] = resolve_items(owner, [f"block:{block.pk}"])
        self.assertEqual([sub["id"] for sub in item["links"]], [f"job:{job.pk}"])

    def test_nested_block_link_is_a_leaf(self):
        owner = _owner()
        job = _job(owner)
        inner = PortfolioBlock.objects.create(
            user=owner, kind="text", body="inner", links=[f"job:{job.pk}"]
        )
        outer = PortfolioBlock.objects.create(
            user=owner, kind="text", body="outer", links=[f"block:{inner.pk}"]
        )
        [item] = resolve_items(owner, [f"block:{outer.pk}"])
        [nested] = item["links"]
        self.assertEqual(nested["id"], f"block:{inner.pk}")
        # leaf: the inner block's own link is not expanded (bounds nesting to one level)
        self.assertEqual(nested["links"], [])

    def test_self_link_is_dropped(self):
        owner = _owner()
        block = PortfolioBlock.objects.create(
            user=owner, kind="text", body="hi", links=[]
        )
        block.links = [f"block:{block.pk}"]
        block.save(update_fields=["links"])
        [item] = resolve_items(owner, [f"block:{block.pk}"])
        self.assertEqual(item["links"], [])

    def test_dead_link_ids_drop(self):
        owner = _owner()
        block = PortfolioBlock.objects.create(
            user=owner, kind="text", body="hi", links=["job:99999", "block:88888"]
        )
        [item] = resolve_items(owner, [f"block:{block.pk}"])
        self.assertEqual(item["links"], [])


class BuildPayloadClaimedTests(TestCase):
    """An entry nested under a rendered block isn't also floated loose in `more`."""

    def test_linked_entry_not_repeated_in_more(self):
        owner = _owner()
        job = _job(owner)  # not favourite -> would otherwise land in `more`
        block = PortfolioBlock.objects.create(
            user=owner,
            kind="text",
            body="hi",
            favourite=True,
            links=[f"job:{job.pk}"],
        )
        payload = build_payload(owner)
        featured_ids = [i["id"] for i in payload["featured"]]
        more_ids = [i["id"] for i in payload["more"]]
        self.assertIn(f"block:{block.pk}", featured_ids)
        self.assertNotIn(f"job:{job.pk}", more_ids)
        feat_block = next(
            i for i in payload["featured"] if i["id"] == f"block:{block.pk}"
        )
        self.assertEqual([s["id"] for s in feat_block["links"]], [f"job:{job.pk}"])


class BlockLinksSerializerTests(TestCase):
    """`links` is grammar-validated ('<type>:<pk>'), order-deduped, on block CRUD."""

    URL = "/api/spa/portfolio/manage/blocks/"

    def setUp(self):
        self.user = _owner("blocker")
        self.client.force_login(self.user)

    def _post(self, links):
        return self.client.post(
            self.URL,
            data=json.dumps({"kind": "text", "body": "hi", "links": links}),
            content_type="application/json",
        )

    def test_rejects_non_id_links(self):
        self.assertEqual(self._post(["not-an-id"]).status_code, 400)

    def test_accepts_and_order_dedupes_ids(self):
        self.assertEqual(self._post(["job:1", "job:1", "block:2"]).status_code, 201)
        block = PortfolioBlock.objects.get(user=self.user)
        self.assertEqual(block.links, ["job:1", "block:2"])
