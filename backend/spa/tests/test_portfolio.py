"""Portfolio tests — the flow-rework + handle-resolution acceptance set.

Covers the flow-rework payload/intro logic (`owner_domains` / `section_order` /
`default_link` / `PortfolioIntroWriter` / `build_intro`) and the multi-user portfolios
(`owner_for_handle` / `random_handle` / per-user slugs / `application_slug` /
`public_portfolio_url`). Every portfolio lives at `portfolio.<domain>/<handle>/`, so the public
endpoints carry the handle in their path (`/api/spa/public/<handle>/…`), and the Host header
plays no part.

LLM calls are always mocked (`spa.portfolio.complete`) —
no live tower in tests, mirroring the rank discipline.
"""

import json
from datetime import date
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils.text import slugify

from jac.models import (
    Domain,
    Education,
    Job,
    JobApplication,
    JobPostAddress,
    JobPosting,
    Skill,
)
from llm_connector.base import LLMTransportError, LLMWarmingUp
from llm_connector.client import _interactive_timeout
from llm_connector.models import AiJob
from llm_connector.tasks import run_ai_job
from spa.models import PortfolioBlock, PortfolioLink, UserProfile
from spa.portfolio import (
    SLUG_SUFFIX_ALPHABET,
    SLUG_SUFFIX_LEN,
    PortfolioIntroWriter,
    application_slug,
    build_intro,
    build_payload,
    default_link,
    freeze_link,
    link_for_application,
    link_owner_for_handle,
    owner_domains,
    owner_for_handle,
    portfolio_home_url,
    public_portfolio_url,
    random_handle,
    resolve_items,
    section_order,
)


def _owner(username="lukas", published=True):
    """A portfolio owner at `portfolio.<domain>/<username>/`. UserProfile + PersonalityProfile are
    auto-created by signals. A real signup gets a random handle (`RandomHandleTests`); this one
    takes its username as the handle, as if claimed in the profile, so test paths stay readable.
    Published unless a test says otherwise: most of this file is about what a public portfolio
    shows. A real signup starts unpublished (`UnpublishedPortfolioTests`)."""
    user = User.objects.create_user(username=username, password="pw")
    user.profile.handle = username
    user.profile.portfolio_public = published
    user.profile.save(update_fields=["handle", "portfolio_public"])
    return user


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


# ── handle-based owner resolution (multi-user portfolios) ───────────────────────


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
class OwnerForHandleTests(TestCase):
    """`portfolio.<domain>/<handle>/` names the owner; nothing reads the Host header."""

    def test_a_handle_resolves_that_user(self):
        jane = _owner("jane")
        _owner("lukas")  # the site owner is never picked for someone else's handle
        self.assertEqual(owner_for_handle("jane"), jane)

    def test_handles_are_case_insensitive(self):
        jane = _owner("jane")
        self.assertEqual(owner_for_handle("JaNe"), jane)
        self.assertEqual(link_owner_for_handle("JANE"), jane)

    def test_an_unknown_or_empty_handle_is_none(self):
        _owner("jane")
        for handle in ("nobody", "", None):
            self.assertIsNone(owner_for_handle(handle), handle)
            self.assertIsNone(link_owner_for_handle(handle), handle)

    def test_a_reserved_handle_is_none_even_when_a_row_holds_it(self):
        """The first path segment would mean something else there: no portfolio, published or
        not, and no links."""
        jane = _owner("jane")
        for handle in sorted(settings.RESERVED_HANDLES):
            UserProfile.objects.filter(user=jane).update(handle=handle)
            for spelling in (handle, handle.upper()):
                self.assertIsNone(owner_for_handle(spelling), spelling)
                self.assertIsNone(link_owner_for_handle(spelling), spelling)

    def test_an_inactive_user_is_none(self):
        jane = _owner("jane")
        User.objects.filter(pk=jane.pk).update(is_active=False)
        self.assertIsNone(owner_for_handle("jane"))
        self.assertIsNone(link_owner_for_handle("jane"))

    def test_an_unpublished_handle_is_no_owner_but_still_owns_its_links(self):
        jane = _owner("jane", published=False)
        self.assertIsNone(owner_for_handle("jane"))
        self.assertEqual(link_owner_for_handle("jane"), jane)

    def test_the_site_owner_needs_no_publishing(self):
        lukas = _owner("lukas", published=False)
        self.assertEqual(owner_for_handle("lukas"), lukas)

    @override_settings(PORTFOLIO_OWNER_USERNAME="Lukas")
    def test_the_site_owner_is_matched_case_insensitively(self):
        # Configured "Lukas" (capital L) still names the "lukas" row: the exact-match 404 bug.
        lukas = _owner("lukas", published=False)
        self.assertEqual(owner_for_handle("lukas"), lukas)


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
class PublicHandleTests(TestCase):
    """Every public endpoint, for each kind of handle in its path. Published: it answers.
    Unknown, reserved or unpublished: one and the same 404. A link is the exception: it works on
    an unpublished handle (what it shows there: `UnpublishedPortfolioTests`)."""

    SLUG = "for-acme"
    ANSWERS = {
        "links": 200,
        "native": 200,
        "meta": 200,
        "rank": 200,
        "prewarm": 204,
        "intro": 202,
    }

    def setUp(self):
        cache.clear()  # 6/hour on rank and intro, and every test here calls them
        jane = _owner("jane")
        hidden = _owner("hidden", published=False)
        # Holds a reserved handle anyway (an admin edit, a row from before the list grew).
        squatter = _owner("squatter")
        UserProfile.objects.filter(user=squatter).update(handle="admin")
        for user in (jane, hidden, squatter):
            PortfolioLink.objects.create(
                user=user, kind=PortfolioLink.Kind.manual, slug=self.SLUG
            )

    def _calls(self, handle):
        api = f"/api/spa/public/{handle}/"
        post = {"data": {"query": "x"}, "content_type": "application/json"}
        with (
            mock.patch("spa.views.rank_for_query", return_value=[]),
            mock.patch("spa.views.request_prewarm"),
            mock.patch("llm_connector.tasks.run_ai_job"),
        ):
            return {
                "links": self.client.get(f"{api}links/{self.SLUG}/"),
                "native": self.client.get(f"{api}native/"),
                "meta": self.client.get(f"{api}native/meta/"),
                "rank": self.client.post(f"{api}native/rank/", **post),
                "prewarm": self.client.post(f"{api}native/prewarm/"),
                "intro": self.client.post(f"{api}native/intro/", **post),
            }

    def test_a_published_handle_answers_on_every_endpoint(self):
        for name, r in self._calls("jane").items():
            self.assertEqual(r.status_code, self.ANSWERS[name], name)
            self.assertEqual(r["X-Robots-Tag"], "noindex", name)

    def test_the_handle_is_case_insensitive_on_every_endpoint(self):
        for name, r in self._calls("JaNe").items():
            self.assertEqual(r.status_code, self.ANSWERS[name], name)

    def test_unknown_and_reserved_handles_get_the_same_404_everywhere(self):
        unknown = self._calls("nobody")
        for name, r in self._calls("admin").items():
            self.assertEqual(r.status_code, 404, name)
            self.assertEqual(r.content, unknown[name].content, name)

    def test_an_unpublished_handle_gets_that_404_everywhere_but_its_links(self):
        unknown = self._calls("nobody")
        for name, r in self._calls("hidden").items():
            if name == "links":
                self.assertEqual(r.status_code, 200)
                continue
            self.assertEqual(r.status_code, 404, name)
            self.assertEqual(r.content, unknown[name].content, name)

    def test_a_link_answers_only_under_its_owners_handle(self):
        PortfolioLink.objects.create(
            user=User.objects.get(username="jane"),
            kind=PortfolioLink.Kind.manual,
            slug="only-jane",
        )
        url = "/api/spa/public/{}/links/only-jane/"
        self.assertEqual(self.client.get(url.format("jane")).status_code, 200)
        self.assertEqual(self.client.get(url.format("hidden")).status_code, 404)


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
class UnpublishedPortfolioTests(TestCase):
    """A handle is minted at signup, and nothing under it is public until the owner says so.
    The exception is a link they share, which shows its own selection and no more."""

    API = "/api/spa/public/jane/"

    def setUp(self):
        cache.clear()
        self.jane = _owner("jane", published=False)
        self.jane.profile.display_name = "Jane Secret"
        self.jane.profile.save(update_fields=["display_name"])
        music = Domain.objects.create(user=self.jane, name="music")
        self.picked = _job(self.jane, title="Picked")
        self.other = _job(self.jane, title="Other", favourite=True)
        self.other.domains.add(music)
        PortfolioBlock.objects.create(user=self.jane, kind="text", body="hi")

    def _publish(self):
        self.jane.profile.portfolio_public = True
        self.jane.profile.save(update_fields=["portfolio_public"])

    def _manual_link(self, **content):
        return PortfolioLink.objects.create(
            user=self.jane,
            kind=PortfolioLink.Kind.manual,
            slug="for-acme",
            content={"featured": [f"job:{self.picked.pk}"], **content},
        )

    def _resolve(self, link):
        return self.client.get(f"{self.API}links/{link.slug}/")

    def test_publishing_opens_the_native_endpoints(self):
        native = f"{self.API}native/"
        self.assertEqual(self.client.get(native).status_code, 404)
        self._publish()
        self.assertEqual(self.client.get(native).status_code, 200)
        self.assertEqual(self.client.get(f"{native}meta/").status_code, 200)

    def test_a_manual_link_shows_its_selection_and_no_more(self):
        r = self._resolve(self._manual_link(domains=["music"]))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual([i["id"] for i in body["featured"]], [f"job:{self.picked.pk}"])
        self.assertEqual(body["more"], [])
        self.assertNotContains(r, "Other")

    def test_an_application_qr_link_shows_its_selection_and_no_more(self):
        app = _application(self.jane)
        app.cv_content = {"jobs": [{"id": f"job:{self.picked.pk}"}]}
        app.save()
        r = self._resolve(link_for_application(app))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual([i["id"] for i in body["featured"]], [f"job:{self.picked.pk}"])
        self.assertEqual(body["more"], [])

    def test_publishing_brings_explore_more_back(self):
        link = self._manual_link()
        self._publish()
        more = [i["id"] for i in self._resolve(link).json()["more"]]
        self.assertIn(f"job:{self.other.pk}", more)

    def test_a_revoked_link_is_gone_like_any_other(self):
        link = self._manual_link()
        link.revoke()
        self.assertEqual(self._resolve(link).status_code, 404)


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
class ProfilePublishTests(TestCase):
    URL = "/api/spa/profile/"

    def _patch(self, **body):
        return self.client.patch(
            self.URL, data=json.dumps(body), content_type="application/json"
        )

    def test_a_signup_starts_unpublished(self):
        user = User.objects.create_user(username="new", password="pw")
        self.assertFalse(UserProfile.objects.get(user=user).portfolio_public)

    def test_the_owner_publishes_and_unpublishes_through_the_profile(self):
        user = _owner("jane", published=False)
        self.client.force_login(user)
        for value in (True, False):
            r = self._patch(portfolio_public=value)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["portfolio_public"], value)
            self.assertFalse(r.json()["portfolio_always_public"])
            self.assertEqual(UserProfile.objects.get(user=user).portfolio_public, value)

    def test_the_site_owner_is_told_the_switch_does_nothing(self):
        self.client.force_login(_owner("lukas", published=False))
        body = self.client.get(self.URL).json()
        self.assertFalse(body["portfolio_public"])
        self.assertTrue(body["portfolio_always_public"])

    def test_always_public_cant_be_claimed(self):
        self.client.force_login(_owner("jane", published=False))
        r = self._patch(portfolio_always_public=True)
        self.assertFalse(r.json()["portfolio_always_public"])


HANDLE_RE = rf"^u-[{SLUG_SUFFIX_ALPHABET}]{{{SLUG_SUFFIX_LEN}}}$"


class RandomHandleTests(TestCase):
    """allauth makes the username from the email's local part, so a handle minted from it put
    `max.mustermann.<domain>` online at signup. A new account's address names no one; the owner
    picks a real one in the profile (`HandleClaimTests`)."""

    def test_a_new_users_handle_is_random_and_holds_nothing_of_the_username(self):
        user = User.objects.create_user(
            username="maximilian.mustermann",
            email="maximilian.mustermann@example.com",
            password="pw",
        )
        handle = user.profile.handle
        self.assertRegex(handle, HANDLE_RE)
        self.assertLessEqual(
            len(handle), UserProfile._meta.get_field("handle").max_length
        )
        for part in ("maximilian", "mustermann"):
            self.assertNotIn(part, handle)

    def test_two_users_get_different_handles(self):
        a = User.objects.create_user(username="jane", password="pw")
        b = User.objects.create_user(username="jane2", password="pw")
        for user in (a, b):
            self.assertRegex(user.profile.handle, HANDLE_RE)
        self.assertNotEqual(a.profile.handle, b.profile.handle)

    @override_settings(RESERVED_HANDLES={"u-reserved"})
    def test_a_reserved_or_taken_candidate_is_rolled_again(self):
        # Taken is case-insensitive, like validate_handle.
        UserProfile.objects.filter(user=_owner("jane")).update(handle="U-TAKEN234")
        rolls = ["reserved", "taken234", "fresh234"]
        with mock.patch("spa.portfolio.slug_suffix", side_effect=rolls):
            self.assertEqual(random_handle(), "u-fresh234")

    def test_it_gives_up_rather_than_loop_forever(self):
        UserProfile.objects.filter(user=_owner("jane")).update(handle="u-taken234")
        with (
            mock.patch("spa.portfolio.slug_suffix", return_value="taken234"),
            self.assertRaises(IntegrityError),
        ):
            random_handle()


class HandleClaimTests(TestCase):
    """Authed `PATCH /api/spa/profile/` claiming a portfolio handle — the writable
    `handle` field routed through `validate_handle` (normalize → reserved guard →
    case-insensitive uniqueness → min length)."""

    PROFILE_URL = "/api/spa/profile/"

    def setUp(self):
        self.user = _owner("claimer")  # handle "claimer"
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

    def test_no_reserved_handle_can_be_claimed(self):
        """The portfolio host's own paths, the SPA's routes, and "system": handles are random now,
        so nothing holds that one by accident any more. Normalized first, then refused. slugify
        strips a leading underscore, so "_allauth" can't even be spelled: that claim lands on the
        free "allauth". Only an admin edit puts it on a row (`OwnerForHandleTests`)."""
        for handle in sorted(settings.RESERVED_HANDLES):
            with self.subTest(handle=handle):
                r = self._patch(handle.upper())
                if slugify(handle) == handle:
                    self.assertEqual(r.status_code, 400)
                    self.assertIn("handle", r.json())
                self.user.profile.refresh_from_db()
                self.assertNotIn(self.user.profile.handle, settings.RESERVED_HANDLES)

    def test_handle_taken_by_another_user_is_rejected(self):
        _owner("jane")  # handle "jane"
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
        self._link(b, "acme")  # the path carries the handle → no cross-user clash
        self.assertEqual(PortfolioLink.objects.filter(slug="acme").count(), 2)

    def test_same_user_cannot_hold_two_active(self):
        a = _owner("a")
        self._link(a, "acme")
        with self.assertRaises(IntegrityError), transaction.atomic():
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
    def test_company_then_suffix(self):
        app = _application(_owner(), company="Acme", role="Backend Engineer")
        self.assertRegex(
            application_slug(app),
            rf"^acme-[{SLUG_SUFFIX_ALPHABET}]{{{SLUG_SUFFIX_LEN}}}$",
        )

    def test_two_applications_to_one_company_differ(self):
        owner = _owner()
        app1 = _application(owner, company="Acme", role="Backend Engineer")
        app2 = _application(owner, company="Acme", role="Frontend Engineer")
        self.assertNotEqual(
            link_for_application(app1).slug, link_for_application(app2).slug
        )

    def test_the_suffix_is_too_long_to_guess(self):
        """The public throttle is per address, so count on many addresses, not on the rate."""
        self.assertGreaterEqual(len(SLUG_SUFFIX_ALPHABET) ** SLUG_SUFFIX_LEN, 2**39)

    def test_a_long_company_name_still_fits_the_field(self):
        app = _application(_owner(), company="x" * 200)
        slug = application_slug(app)
        self.assertLessEqual(
            len(slug), PortfolioLink._meta.get_field("slug").max_length
        )

    def test_trying_the_company_name_finds_nothing(self):
        cache.clear()
        owner = _owner("jane")
        link = link_for_application(_application(owner, company="Acme"))
        url = "/api/spa/public/jane/links/{}/"
        self.assertEqual(self.client.get(url.format("acme")).status_code, 404)
        self.assertEqual(self.client.get(url.format(link.slug)).status_code, 200)

    def test_a_manual_link_stays_as_named(self):
        self.client.force_login(_owner())
        r = self.client.post(
            "/api/spa/portfolio/manage/links/",
            data=json.dumps({"slug": "acme", "content": {}}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["slug"], "acme")


class ApplicationSectionsOffTests(TestCase):
    """A section switched off for an application stays off on its public page too."""

    def setUp(self):
        self.owner = _owner()
        self.job = _job(self.owner)
        self.skill = _skill(self.owner, favourite=True)
        self.job_id, self.skill_id = f"job:{self.job.pk}", f"skill:{self.skill.pk}"
        self.app = _application(self.owner)
        self.app.cv_content = {
            "jobs": [{"id": self.job_id}],
            "skills": [{"id": self.skill_id}],
        }
        self.app.sections_off = ["skills"]
        self.app.save()
        self.link = link_for_application(self.app)

    def _payload(self):
        payload = build_payload(self.owner, link=self.link)
        return (
            [i["id"] for i in payload["featured"]],
            [i["id"] for i in payload["more"]],
        )

    def test_the_live_page_skips_an_off_section(self):
        featured, _ = self._payload()
        self.assertEqual(featured, [self.job_id])

    def test_the_frozen_list_skips_an_off_section(self):
        freeze_link(self.app)
        self.link.refresh_from_db()
        self.assertEqual(self.link.content["featured"], [self.job_id])

    def test_a_deselected_row_still_drops_out(self):
        self.app.cv_content["jobs"][0]["deselected"] = True
        self.app.sections_off = []
        self.app.save()
        featured, _ = self._payload()
        self.assertEqual(featured, [self.skill_id])

    def test_a_favourite_in_an_off_section_doesnt_float_into_more(self):
        _, more = self._payload()
        self.assertNotIn(self.skill_id, more)

    def test_a_domain_match_in_an_off_section_doesnt_float_into_more(self):
        music = Domain.objects.create(user=self.owner, name="music")
        self.skill.domains.add(music)
        other_job = _job(self.owner, title="Other")
        other_job.domains.add(music)
        self.link.content = {"domains": ["music"]}
        self.link.save()
        _, more = self._payload()
        self.assertEqual(more, [f"job:{other_job.pk}"])

    def test_every_section_off_leaves_only_blocks_in_more(self):
        self.app.sections_off = [
            "jobs",
            "projects",
            "skills",
            "educations",
            "certifications",
            "languages",
        ]
        self.app.save()
        block = PortfolioBlock.objects.create(user=self.owner, kind="text", body="hi")
        self.assertEqual(self._payload(), ([], [f"block:{block.pk}"]))

    def test_the_explore_list_survives_a_deleted_application(self):
        """`application` is SET_NULL: a frozen page outlives it, with nothing left to say off."""
        freeze_link(self.app)
        self.link.refresh_from_db()
        self.app.delete()
        self.link.refresh_from_db()
        featured, more = self._payload()
        self.assertEqual(featured, [self.job_id])
        self.assertIn(self.skill_id, more)


@override_settings(PORTFOLIO_URL="http://portfolio.example.test")
class PublicPortfolioUrlTests(TestCase):
    """One host for every portfolio: the handle and the slug are path segments."""

    def setUp(self):
        self.jane = _owner("jane")  # handle "jane"
        self.link = PortfolioLink.objects.create(
            user=self.jane, kind=PortfolioLink.Kind.manual, slug="acme"
        )

    def test_a_links_url_is_the_handle_then_the_slug(self):
        self.assertEqual(
            public_portfolio_url(self.link), "http://portfolio.example.test/jane/acme/"
        )

    def test_the_owners_home_is_their_handle(self):
        self.assertEqual(
            portfolio_home_url(self.jane), "http://portfolio.example.test/jane/"
        )

    @override_settings(PORTFOLIO_URL="https://portfolio.example.test/")
    def test_a_trailing_slash_in_the_setting_changes_nothing(self):
        self.assertEqual(
            public_portfolio_url(self.link), "https://portfolio.example.test/jane/acme/"
        )

    def test_the_manage_api_hands_out_that_url(self):
        """What the QR on an exported letter encodes."""
        self.client.force_login(self.jane)
        [row] = self.client.get("/api/spa/portfolio/manage/links/").json()
        self.assertEqual(row["url"], "http://portfolio.example.test/jane/acme/")


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
    @mock.patch("spa.portfolio.complete", return_value="Welcome, traveller.")
    def test_write_returns_text(self, m_complete):
        w = PortfolioIntroWriter(name="Lukas", interest="music", highlights=["A role"])
        self.assertEqual(w.write(), "Welcome, traveller.")
        m_complete.assert_called_once()

    @mock.patch(
        "spa.portfolio.complete", side_effect=LLMTransportError("model server is off")
    )
    def test_a_sleeping_model_server_returns_empty(self, m_complete):
        w = PortfolioIntroWriter(name="Lukas", interest="music", highlights=[])
        with self.assertLogs("spa.portfolio", level="WARNING"):
            self.assertEqual(w.write(), "")

    @mock.patch("spa.portfolio.complete", side_effect=RuntimeError("boom"))
    def test_llm_failure_returns_empty(self, m_complete):
        with self.assertLogs("spa.portfolio", level="WARNING"):
            self.assertEqual(
                PortfolioIntroWriter("Lukas", "music", []).write(), ""
            )

    @mock.patch("spa.portfolio.complete", return_value="Grounded intro.")
    def test_build_intro_wires_a_payload(self, m_complete):
        owner = _owner()
        _job(owner, favourite=True)
        self.assertEqual(
            build_intro(owner, domains=["music"], question="gigs", tone="personal"),
            "Grounded intro.",
        )


# ── public endpoints (meta / intro) + landing ───────────────────────────────────


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
class PublicPortfolioEndpointTests(TestCase):
    API = "/api/spa/public/lukas/native/"

    def setUp(self):
        cache.clear()  # reset scoped-throttle counters between tests
        self.owner = _owner("lukas")

    def test_meta_returns_domains_and_style_vocab(self):
        d = Domain.objects.create(user=self.owner, name="music")
        job = _job(self.owner)
        job.domains.add(d)
        r = self.client.get(f"{self.API}meta/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("music", body["domains"])
        self.assertTrue(any(t["value"] == "neutral" for t in body["tones"]))
        self.assertTrue(any(f["value"] == "balanced" for f in body["focuses"]))

    def test_meta_is_the_handles_own(self):
        """Two owners, two shortlists: the handle in the path picks whose."""
        jane = _owner("jane")
        _job(jane).domains.add(Domain.objects.create(user=jane, name="theatre"))
        _job(self.owner).domains.add(
            Domain.objects.create(user=self.owner, name="music")
        )
        r = self.client.get("/api/spa/public/jane/native/meta/")
        self.assertEqual(r.json()["domains"], ["theatre"])

    def _queue_intro(self):
        with mock.patch("llm_connector.tasks.run_ai_job"):
            return self.client.post(
                f"{self.API}intro/",
                data={
                    "domains": ["music"],
                    "query": "gigs",
                    "tone": "personal",
                    "focus": "technical",
                },
                content_type="application/json",
            )

    @mock.patch("spa.portfolio.complete", return_value="Hello there.")
    def test_intro_is_queued_then_read_by_its_job_id(self, m_complete):
        """Pages first, AI second: the POST answers at once, whatever the model server is doing."""
        r = self._queue_intro()
        self.assertEqual(r.status_code, 202)
        url = f"/api/spa/public/intro/{r.json()['id']}/"
        self.assertEqual(self.client.get(url).json(), {"status": "pending", "intro": ""})
        m_complete.assert_not_called()
        run_ai_job(r.json()["id"])
        self.assertEqual(
            self.client.get(url).json(), {"status": "done", "intro": "Hello there."}
        )

    def test_the_visitors_question_is_gone_when_the_job_ends(self):
        r = self._queue_intro()
        job = AiJob.objects.get(pk=r.json()["id"])
        self.assertEqual(job.payload["query"], "gigs")
        with mock.patch("spa.portfolio.complete", return_value="Hello there."):
            run_ai_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.payload, {})

    @mock.patch("spa.portfolio.complete", side_effect=LLMTransportError("off"))
    def test_a_sleeping_model_server_ends_with_no_intro_not_an_error(self, _complete):
        r = self._queue_intro()
        with (
            override_settings(PORTFOLIO_INTRO_WAIT_S=0),
            mock.patch("llm_connector.client.time.sleep"),
            self.assertLogs("spa.portfolio", level="WARNING"),
        ):
            run_ai_job(r.json()["id"])
        body = self.client.get(f"/api/spa/public/intro/{r.json()['id']}/").json()
        self.assertEqual(body["intro"], "")

    def test_only_intro_jobs_are_readable_without_a_login(self):
        job = AiJob.objects.create(
            kind="letter_rewrite", user=self.owner, result={"text": "private"}
        )
        r = self.client.get(f"/api/spa/public/intro/{job.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_the_intro_is_written_for_the_handles_owner(self):
        jane = _owner("jane")
        with mock.patch("llm_connector.tasks.run_ai_job"):
            r = self.client.post(
                "/api/spa/public/jane/native/intro/",
                data={},
                content_type="application/json",
            )
        self.assertEqual(AiJob.objects.get(pk=r.json()["id"]).user, jane)

    def test_intro_rejects_overlong_query(self):
        r = self.client.post(
            f"{self.API}intro/",
            data={"query": "x" * 1000},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
class LandingTests(TestCase):
    """The apex is Django-rendered (SEO): the site owner's card, and the way on to their
    questionnaire on the portfolio host."""

    def test_landing_renders_owner_html(self):
        User.objects.create_user(
            username="lukas", password="pw", first_name="Lukas", last_name="H"
        )
        User.objects.create_user(username="jane", password="pw", first_name="Janet")
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r["Content-Type"])
        self.assertContains(r, "Lukas")
        self.assertNotContains(r, "Janet")  # a second user is never the site

    @override_settings(PORTFOLIO_URL="https://portfolio.testserver")
    def test_explore_leads_to_the_owners_questionnaire(self):
        """The questionnaire lives on the portfolio host, under the owner's handle. The app host
        only redirects to /applications, and from there a stranger lands on a login form."""
        owner = User.objects.create_user(username="lukas", password="pw")
        r = self.client.get("/")
        self.assertContains(
            r, f'href="https://portfolio.testserver/{owner.profile.handle}/"'
        )

    @override_settings(PORTFOLIO_OWNER_USERNAME="Lukas")
    def test_the_configured_name_is_case_insensitive(self):
        # Configured "Lukas" (capital L) still names the "lukas" row: the exact-match 404 bug.
        User.objects.create_user(username="lukas", password="pw", first_name="Lukas")
        self.assertContains(self.client.get("/"), "Explore my work")

    def test_without_an_owner_theres_nothing_to_explore(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Explore my work")

    def test_the_sentinel_an_unset_name_or_an_inactive_owner_is_no_owner(self):
        lukas = User.objects.create_user(username="lukas", password="pw")
        for username in ("", "system"):
            with override_settings(PORTFOLIO_OWNER_USERNAME=username):
                self.assertNotContains(self.client.get("/"), "Explore my work")
        User.objects.filter(pk=lukas.pk).update(is_active=False)
        self.assertNotContains(self.client.get("/"), "Explore my work")

    def test_health_endpoint(self):
        r = self.client.get("/health/")
        self.assertEqual(r.status_code, 200)

    @override_settings(RELEASE="0123abc")
    def test_health_names_the_release(self):
        """The deploy workflow's smoke test waits for its own sha in X-Release (guide 05).
        A header, so the JSON body other probes assert stays as it is."""
        r = self.client.get("/health/")
        self.assertEqual(r["X-Release"], "0123abc")
        self.assertEqual(r.json(), {"message": "I am alive!"})


# The site owner, so the landing shows them too.
@override_settings(PORTFOLIO_OWNER_USERNAME="maximilian.mustermann")
class PublicNameTests(TestCase):
    """The username is the email's local part, so no public page falls back to it: without a
    display name or a full name, the handle stands in, which the address shows anyway."""

    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="maximilian.mustermann",
            email="maximilian.mustermann@example.com",
            password="pw",
        )
        UserProfile.objects.filter(user=self.owner).update(portfolio_public=True)
        self.handle = self.owner.profile.handle

    def test_without_a_name_the_payload_shows_the_handle(self):
        owner = build_payload(self.owner)["owner"]
        self.assertEqual(owner["display_name"], self.handle)

    def test_no_public_page_shows_the_username(self):
        link = PortfolioLink.objects.create(
            user=self.owner, kind=PortfolioLink.Kind.manual, slug="hello"
        )
        for url in (
            "/",  # the landing: <title>, og:title, the heading, the avatar's alt
            f"/api/spa/public/{self.handle}/native/",
            f"/api/spa/public/{self.handle}/links/{link.slug}/",
        ):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertContains(r, self.handle, msg_prefix=url)
            for part in ("maximilian", "mustermann"):
                self.assertNotContains(r, part, msg_prefix=url)

    @mock.patch("spa.portfolio.complete", return_value="Hi.")
    def test_the_ai_intro_isnt_told_the_username(self, m_complete):
        build_intro(self.owner)
        prompt = m_complete.call_args.kwargs["prompt"]
        self.assertIn(self.handle, prompt)
        self.assertNotIn("maximilian", prompt)

    def test_a_name_comes_before_the_handle(self):
        User.objects.filter(pk=self.owner.pk).update(first_name="Max", last_name="M")
        self.owner.refresh_from_db()
        self.assertEqual(build_payload(self.owner)["owner"]["display_name"], "Max M")


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
class PortfolioPrewarmTests(TestCase):
    """A visitor has started the questionnaire: wake the model server ahead of the rank and the
    intro. No login, and nothing for a portfolio that isn't there."""

    URL = "/api/spa/public/{}/native/prewarm/"

    def setUp(self):
        cache.clear()  # scoped-throttle history lives in the default cache

    def test_a_visitor_asks_for_it(self):
        _owner("lukas")
        with mock.patch("spa.views.request_prewarm") as prewarm:
            r = self.client.post(self.URL.format("lukas"))
        self.assertEqual(r.status_code, 204)
        prewarm.assert_called_once()

    def test_no_portfolio_nothing_wakes(self):
        _owner("hidden", published=False)
        UserProfile.objects.filter(user=_owner("squatter")).update(handle="admin")
        with mock.patch("spa.views.request_prewarm") as prewarm:
            for handle in ("nobody", "hidden", "admin"):
                r = self.client.post(self.URL.format(handle))
                self.assertEqual(r.status_code, 404, handle)
        prewarm.assert_not_called()

    def test_loading_the_questionnaire_wakes_nothing(self):
        """Crawlers load pages. Only the POST a person's first click sends may wake the tower."""
        _owner("lukas")
        with mock.patch("spa.views.request_prewarm") as prewarm:
            self.client.get("/api/spa/public/lukas/native/meta/")
        prewarm.assert_not_called()


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
class PortfolioRankFallbackTests(TestCase):
    """A visitor is waiting, so a model server that is off or starting is a normal answer: the page
    keeps its standard selection and learns why, without an error and without a retry."""

    URL = "/api/spa/public/lukas/native/rank/"

    def setUp(self):
        cache.clear()
        self.owner = _owner("lukas")

    def _post(self):
        return self.client.post(
            self.URL, data={"query": "gigs"}, content_type="application/json"
        )

    @mock.patch("spa.views.rank_for_query", return_value=[{"id": "job:1", "score": 0.9}])
    def test_ok(self, _rank):
        body = self._post().json()
        self.assertEqual(body["ai"], "ok")
        self.assertEqual(body["ranked"], [{"id": "job:1", "score": 0.9}])

    @mock.patch(
        "spa.views.rank_for_query", side_effect=LLMWarmingUp("waking", retry_after=30)
    )
    def test_503_with_retry_after_is_warming_up(self, _rank):
        r = self._post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ranked": [], "ai": "warming_up"})

    @mock.patch("spa.views.rank_for_query", side_effect=LLMTransportError("off"))
    def test_an_absent_model_server_is_unavailable(self, _rank):
        with self.assertLogs("spa.views", level="WARNING"):
            r = self._post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ranked": [], "ai": "unavailable"})

    @override_settings(PORTFOLIO_RANK_TIMEOUT_S=3)
    def test_the_call_runs_under_the_short_timeout(self):
        seen = {}

        def rank(*args):
            seen["timeout"] = _interactive_timeout.get()
            return []

        with mock.patch("spa.views.rank_for_query", side_effect=rank):
            self._post()
        self.assertEqual(seen["timeout"], 3)


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


# --- [fullstack]-education-degree ---------------------------------------------------
# ACTIVE guide (activated 2026-08-07). Red until `Education.degree_level` lands.


class CareerItemDegreeTests(TestCase):
    """The public card titles an education entry `degree or field_of_study`. Once the
    free-text "Drop Out …" wording is gone from the degree field, an unfinished study
    period would present as a plain degree on the portfolio — the one surface strangers
    read without context. Same marker as the CV heading."""

    def test_an_unfinished_study_period_is_marked(self):
        owner = _owner()
        edu = Education.objects.create(
            user=owner,
            institution="FU Berlin",
            field_of_study="Physics",
            started=date(2016, 10, 1),
            ended=date(2018, 9, 30),
            degree_level=Education.DegreeLevel.none,
        )
        [item] = resolve_items(owner, [f"education:{edu.pk}"])
        self.assertEqual(item["title"], "Physics (no degree)")
        self.assertEqual(item["subtitle"], "FU Berlin")

    def test_a_degree_keeps_its_plain_title(self):
        owner = _owner()
        edu = Education.objects.create(
            user=owner,
            institution="TU",
            field_of_study="Physics",
            degree="B.Sc.",
            started=date(2012, 10, 1),
            ended=date(2015, 9, 30),
            degree_level=Education.DegreeLevel.bachelor,
        )
        [item] = resolve_items(owner, [f"education:{edu.pk}"])
        self.assertEqual(item["title"], "B.Sc.")


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
