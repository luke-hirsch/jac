"""Portfolio flow-rework tests — the [backend]-portfolio-flow guide's acceptance set.

New topic file (guides 1-2 shipped without one). Red until the guide lands:
`owner_domains` / `section_order` / `default_link` / `PortfolioIntroWriter` / `build_intro`
don't exist yet, so the import below errors the whole module until they do.

LLM calls are always mocked (`spa.portfolio.complete`, `spa.portfolio.hirschai_reachable`) —
no live tower in tests, mirroring the rank discipline.
"""

from datetime import date
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from jac.models import Domain, Job, Skill

from spa.models import PortfolioBlock, PortfolioLink
from spa.portfolio import (
    PortfolioIntroWriter,
    build_intro,
    build_payload,
    default_link,
    get_owner,
    owner_domains,
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


# ── owner resolution (the 404 regression) ──────────────────────────────────────


@override_settings(PORTFOLIO_OWNER_USERNAME="Lukas")
class GetOwnerTests(TestCase):
    def test_case_insensitive_match(self):
        # Setting "Lukas" (capital L) must resolve the "lukas" row — the shipped
        # exact-match bug that 404'd every native call.
        u = _owner("lukas")
        self.assertEqual(get_owner(), u)

    def test_inactive_owner_is_none(self):
        User.objects.create_user(username="lukas", password="pw", is_active=False)
        self.assertIsNone(get_owner())

    @override_settings(PORTFOLIO_OWNER_USERNAME="system")
    def test_sentinel_is_none(self):
        self.assertIsNone(get_owner())

    @override_settings(PORTFOLIO_OWNER_USERNAME="")
    def test_unset_is_none(self):
        self.assertIsNone(get_owner())


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


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
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


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
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
        self.assertEqual(r.json(), {"message": "I am alive!"})
