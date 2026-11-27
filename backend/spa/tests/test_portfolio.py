"""Portfolio tests — the flow-rework + handle-resolution acceptance set.

Covers the flow-rework payload/intro logic (`owner_domains` / `section_order` /
`default_link` / `PortfolioIntroWriter` / `build_intro`), the multi-user portfolios
(`owner_for_handle` / `random_handle` / per-user slugs / `application_slug` /
`public_portfolio_url`), the stored standard page (`standard_page` and what the native page
reads from it) and the colour every page wears (`brand_for`, guide 31). Every portfolio lives at `portfolio.<domain>/<handle>/`, so the public
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
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.text import slugify

from jac.models import (
    Domain,
    Education,
    Job,
    JobApplication,
    JobPostAddress,
    JobPosting,
    Project,
    Skill,
)
from llm_connector.base import WAITING_STAGE, LLMTransportError
from llm_connector.models import AiJob
from llm_connector.tasks import run_ai_job
from spa import portfolio  # the standard-page helpers by module: guide 27 adds them
from spa.models import PortfolioBlock, PortfolioLink, UserProfile
from spa.portfolio import (
    SLUG_SUFFIX_ALPHABET,
    SLUG_SUFFIX_LEN,
    PortfolioIntroWriter,
    application_slug,
    build_intro,
    build_payload,
    carry_domain_rename,
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
from spa.tests._helpers import solved_captcha


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


def _standard(owner, **fields):
    """A standard page straight in the ORM, as `standard_page_for` makes it. The kind as a plain
    string, so a test that only needs the row runs before the kind exists."""
    fields.setdefault("slug", "")
    fields.setdefault("content", {"featured": []})
    return PortfolioLink.objects.create(user=owner, kind="standard", **fields)


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

    def test_the_explainer_page_holds_its_path(self):
        """portfolio.<domain>/about/ is the SPA's "What's this?" page, and a static route wins over
        a handle: an owner called "about" would never be reached."""
        self.assertIn("about", settings.RESERVED_HANDLES)

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
        "prewarm": 204,
        "personalise": 202,
    }

    def setUp(self):
        cache.clear()  # 6/hour on personalise, and every test here calls it
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
        """One solved check for both POSTs, as the questionnaire's tick: a portfolio that isn't
        there is the 404 before any check."""
        api = f"/api/spa/public/{handle}/"
        captcha = solved_captcha()
        post = {
            "data": {"query": "x", "captcha": captcha},
            "content_type": "application/json",
        }
        prewarm = {"data": {"captcha": captcha}, "content_type": "application/json"}
        with (
            mock.patch("spa.views.request_prewarm"),
            mock.patch("llm_connector.tasks.run_ai_job"),
        ):
            return {
                "links": self.client.get(f"{api}links/{self.SLUG}/"),
                "native": self.client.get(f"{api}native/"),
                "meta": self.client.get(f"{api}native/meta/"),
                "prewarm": self.client.post(f"{api}native/prewarm/", **prewarm),
                "personalise": self.client.post(f"{api}native/personalise/", **post),
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


@override_settings(PORTFOLIO_URL="http://portfolio.example.test")
class StandardPageManageTests(TestCase):
    """`/api/spa/portfolio/manage/standard/`: the owner reads and writes their standard page.
    Reading writes nothing; the first write makes the row."""

    URL = "/api/spa/portfolio/manage/standard/"
    ADDRESS = "http://portfolio.example.test/jane/?focus=balanced&tone=neutral"

    def setUp(self):
        self.jane = _owner("jane")
        self.client.force_login(self.jane)

    def _patch(self, **body):
        return self.client.patch(
            self.URL, data=json.dumps(body), content_type="application/json"
        )

    def _row(self):
        return PortfolioLink.objects.get(user=self.jane, kind="standard")

    def test_reading_it_before_any_write_shows_an_empty_page_and_writes_nothing(self):
        r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.json(),
            {
                "title": "",
                "intro": "",
                "content": {"featured": []},
                "url": self.ADDRESS,
                "updated_at": None,
            },
        )
        self.assertFalse(PortfolioLink.objects.filter(user=self.jane).exists())

    def test_the_first_write_makes_it(self):
        job = _job(self.jane)
        r = self._patch(
            title="Hi, I'm Jane",
            intro="I build things.",
            content={"featured": [f"job:{job.pk}"]},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["title"], "Hi, I'm Jane")
        self.assertEqual(body["content"], {"featured": [f"job:{job.pk}"]})
        self.assertEqual(body["url"], self.ADDRESS)
        self.assertIsNotNone(body["updated_at"])
        row = self._row()
        self.assertEqual(
            (row.slug, row.title, row.intro, row.is_default),
            ("", "Hi, I'm Jane", "I build things.", False),
        )

    def test_a_second_write_edits_the_same_row(self):
        self._patch(title="One")
        self._patch(intro="Two")
        row = self._row()
        self.assertEqual((row.title, row.intro), ("One", "Two"))
        self.assertEqual(PortfolioLink.objects.filter(user=self.jane).count(), 1)

    def test_a_write_without_content_keeps_the_highlights(self):
        self._patch(content={"featured": ["job:1"]})
        self._patch(title="New title")
        self.assertEqual(self._row().content, {"featured": ["job:1"]})

    def test_the_highlights_are_link_ids_deduped_in_order(self):
        r = self._patch(content={"featured": ["job:2", "block:1", "job:2"]})
        self.assertEqual(r.json()["content"], {"featured": ["job:2", "block:1"]})

    def test_a_malformed_highlight_is_refused(self):
        for content in (
            {"featured": ["not-an-id"]},
            {"featured": "job:1"},
            {"featured": [1]},
            ["job:1"],
        ):
            with self.subTest(content=content):
                r = self._patch(content=content)
                self.assertEqual(r.status_code, 400)
                self.assertIn("content", r.json())

    def test_the_link_parts_dont_get_in(self):
        """No explore scope, no slug, no default flag: the visitor's picks are the scope, and the
        standard page is no link."""
        r = self._patch(
            slug="mine",
            is_default=True,
            kind="manual",
            content={"featured": [], "domains": ["music"], "hide_explore": True},
        )
        self.assertEqual(r.status_code, 200)
        row = self._row()
        self.assertEqual((row.slug, row.is_default), ("", False))
        self.assertEqual(row.content, {"featured": []})

    def test_each_owner_has_their_own(self):
        self._patch(title="Jane's")
        self.client.force_login(_owner("kim"))
        body = self.client.get(self.URL).json()
        self.assertEqual(body["title"], "")
        self.assertEqual(
            body["url"],
            "http://portfolio.example.test/kim/?focus=balanced&tone=neutral",
        )

    def test_it_needs_a_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.URL).status_code, 403)
        self.assertEqual(self._patch(title="x").status_code, 403)


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
class StandardPageIsNoLinkTests(TestCase):
    """The standard page shares the links' table, not their endpoints: the owner's link list,
    a link's own endpoints and the public resolve view never see it."""

    LINKS = "/api/spa/portfolio/manage/links/"

    def setUp(self):
        cache.clear()
        self.jane = _owner("jane")
        self.client.force_login(self.jane)
        self.page = _standard(self.jane, title="Standard")
        self.link = PortfolioLink.objects.create(
            user=self.jane, kind=PortfolioLink.Kind.manual, slug="for-acme"
        )

    def test_the_link_list_leaves_it_out(self):
        rows = self.client.get(self.LINKS).json()
        self.assertEqual([r["id"] for r in rows], [self.link.pk])

    def test_a_links_endpoints_dont_reach_it(self):
        url = f"{self.LINKS}{self.page.pk}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        patch = {"data": json.dumps({"slug": "x"}), "content_type": "application/json"}
        self.assertEqual(self.client.patch(url, **patch).status_code, 404)
        self.assertEqual(self.client.post(f"{url}revoke/").status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.page.refresh_from_db()
        self.assertEqual((self.page.slug, self.page.revoked_at), ("", None))

    def test_no_public_path_resolves_it(self):
        """Its slug is blank, which no path can name. Even a row that somehow holds one stays
        out of the resolve view."""
        PortfolioLink.objects.filter(pk=self.page.pk).update(slug="standard")
        r = self.client.get("/api/spa/public/jane/links/standard/")
        self.assertEqual(r.status_code, 404)

    def test_a_domain_rename_leaves_it_alone(self):
        """`carry_domain_rename` walks the links that keep a scope by name. The standard page
        keeps none (its content is `featured` only), so a rename passes it by. A guard: green
        before this guide too."""
        self.link.content = {"featured": [], "domains": ["music"]}
        self.link.save()
        before = self.page.updated_at
        carry_domain_rename(self.jane, "music", "sound")
        self.link.refresh_from_db()
        self.page.refresh_from_db()
        self.assertEqual(self.link.content["domains"], ["sound"])
        self.assertEqual(self.page.content, {"featured": []})
        self.assertEqual(self.page.updated_at, before)

    def test_the_admin_saves_it_without_a_slug(self):
        """A blank slug is right for a standard page, and the admin's change form must not
        demand one."""
        admin = User.objects.create_superuser(username="root", password="pw")
        self.client.force_login(admin)
        url = f"/admin/spa/portfoliolink/{self.page.pk}/change/"
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'name="slug"')
        r = self.client.post(
            url,
            {
                "user": self.jane.pk,
                "title": "Edited in the admin",
                "intro": "",
                "content": json.dumps({"featured": []}),
                "application": "",
                "revoked_at_0": "",
                "revoked_at_1": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.page.refresh_from_db()
        self.assertEqual((self.page.title, self.page.slug), ("Edited in the admin", ""))


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


# ── the standard page and the default-link fallback ─────────────────────────────


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


class StandardPageRowTests(TestCase):
    """One standard page per owner, made on its first write, at an address that shows it
    without the questions."""

    def test_there_is_none_until_the_first_write(self):
        owner = _owner()
        self.assertIsNone(portfolio.standard_page(owner))

    def test_the_first_write_makes_an_empty_one(self):
        owner = _owner()
        page = portfolio.standard_page_for(owner)
        self.assertEqual(page.kind, PortfolioLink.Kind.standard)
        self.assertEqual(
            (page.slug, page.title, page.intro, page.content, page.is_default),
            ("", "", "", {"featured": []}, False),
        )
        self.assertEqual(portfolio.standard_page(owner), page)

    def test_the_next_write_finds_the_same_row(self):
        owner = _owner()
        first = portfolio.standard_page_for(owner)
        self.assertEqual(portfolio.standard_page_for(owner), first)
        self.assertEqual(PortfolioLink.objects.filter(user=owner).count(), 1)

    def test_one_active_standard_page_per_owner(self):
        owner = _owner()
        _standard(owner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            _standard(owner, slug="other")  # past the slug constraint: this one's own

    def test_a_revoked_one_makes_room(self):
        owner = _owner()
        old = _standard(owner)
        old.revoke()
        self.assertIsNone(portfolio.standard_page(owner))
        self.assertNotEqual(portfolio.standard_page_for(owner), old)

    def test_two_first_writes_at_once_end_on_one_row(self):
        """The loser of the race trips `one_standard_page_per_user` and reads the winner's row."""
        owner = _owner()
        winner = _standard(owner)
        with mock.patch("spa.portfolio.standard_page", return_value=None):
            self.assertEqual(portfolio.standard_page_for(owner), winner)

    @override_settings(PORTFOLIO_URL="http://portfolio.example.test")
    def test_its_address_is_the_home_with_the_questions_skipped(self):
        """What the questionnaire's "Skip the questions" sends: the SPA's
        `formToSearch(EMPTY_FORM)`, no picks and no free text, so no AI job."""
        self.assertEqual(
            portfolio.standard_page_url(_owner("jane")),
            "http://portfolio.example.test/jane/?focus=balanced&tone=neutral",
        )


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
class StandardPagePayloadTests(TestCase):
    """The native page reads the standard page: its title, its intro, its Highlights in the
    owner's order. Without one, or with an empty list, the favourites stand in, as before."""

    def setUp(self):
        cache.clear()
        self.owner = _owner()
        self.music = Domain.objects.create(user=self.owner, name="music")
        self.code = Domain.objects.create(user=self.owner, name="code")
        self.fav = _job(self.owner, favourite=True, title="Favourite job")
        self.gig = _job(self.owner, title="Gig")
        self.gig.domains.add(self.music)
        self.python = _skill(self.owner, name="Python")
        self.python.domains.add(self.code)
        self.block = PortfolioBlock.objects.create(
            user=self.owner, kind="text", title="About me", body="hi"
        )
        self.block.domains.add(self.music)
        self.fav_id, self.gig_id = f"job:{self.fav.pk}", f"job:{self.gig.pk}"
        self.python_id = f"skill:{self.python.pk}"
        self.block_id = f"block:{self.block.pk}"

    def _ids(self, items):
        return [i["id"] for i in items]

    def _pick(self, *ids, **fields):
        return _standard(self.owner, content={"featured": list(ids)}, **fields)

    def test_without_one_nothing_changes(self):
        """An owner who never writes one: no title or intro (the SPA shows the display name and
        the bio), and the favourites are the Highlights. A guard: green before this guide too."""
        payload = build_payload(self.owner)
        self.assertEqual((payload["title"], payload["intro"]), ("", ""))
        self.assertEqual(self._ids(payload["featured"]), [self.fav_id])

    def test_an_empty_one_changes_nothing_either(self):
        _standard(self.owner)
        payload = build_payload(self.owner)
        self.assertEqual((payload["title"], payload["intro"]), ("", ""))
        self.assertEqual(self._ids(payload["featured"]), [self.fav_id])

    def test_its_title_and_intro_head_the_page(self):
        _standard(self.owner, title="Hi, I'm Lukas", intro="I build things.")
        payload = build_payload(self.owner)
        self.assertEqual(payload["title"], "Hi, I'm Lukas")
        self.assertEqual(payload["intro"], "I build things.")
        self.assertEqual(payload["kind"], "native")

    def test_its_highlights_replace_the_favourites_in_its_order(self):
        self._pick(self.block_id, self.python_id, self.gig_id)
        payload = build_payload(self.owner)
        self.assertEqual(
            self._ids(payload["featured"]), [self.block_id, self.python_id, self.gig_id]
        )
        more = self._ids(payload["more"])
        self.assertIn(self.fav_id, more)  # still on the page, below them
        for picked in (self.block_id, self.python_id, self.gig_id):
            self.assertNotIn(picked, more)

    def test_the_style_axis_leaves_its_order_alone(self):
        expected = [self.gig_id, self.python_id, self.block_id]
        self._pick(*expected)
        for style in ({"focus": "technical"}, {"tone": "personal"}):
            with self.subTest(**style):
                payload = build_payload(self.owner, **style)
                self.assertEqual(self._ids(payload["featured"]), expected)

    def test_under_a_scope_only_its_highlights_in_that_scope_stay(self):
        self._pick(self.python_id, self.gig_id, self.block_id)
        payload = build_payload(self.owner, domains=["music"])
        self.assertEqual(self._ids(payload["featured"]), [self.gig_id, self.block_id])
        self.assertNotIn(self.python_id, self._ids(payload["more"]))

    def test_a_scope_none_of_them_meet_falls_back_to_its_favourites(self):
        self._pick(self.gig_id)
        fav_code = _skill(self.owner, favourite=True, name="Rust")
        fav_code.domains.add(self.code)
        payload = build_payload(self.owner, domains=["code"])
        self.assertEqual(self._ids(payload["featured"]), [f"skill:{fav_code.pk}"])

    def test_a_language_drops_out_under_a_scope(self):
        """A language has no domains, so a scope never meets it: as in the rest of the page."""
        from jac.models import Language

        german = Language.objects.create(user=self.owner, name="German")
        self._pick(f"language:{german.pk}", self.gig_id)
        scoped = build_payload(self.owner, domains=["music"])
        self.assertEqual(self._ids(scoped["featured"]), [self.gig_id])
        whole = build_payload(self.owner)
        self.assertEqual(
            self._ids(whole["featured"]), [f"language:{german.pk}", self.gig_id]
        )

    def test_gone_and_hidden_highlights_drop_out(self):
        hidden = PortfolioBlock.objects.create(
            user=self.owner, kind="text", body="draft", is_active=False
        )
        self._pick("job:99999", f"block:{hidden.pk}", self.gig_id)
        payload = build_payload(self.owner)
        self.assertEqual(self._ids(payload["featured"]), [self.gig_id])

    def test_nothing_left_of_them_is_the_favourites(self):
        self._pick("job:99999")
        payload = build_payload(self.owner)
        self.assertEqual(self._ids(payload["featured"]), [self.fav_id])

    def test_a_lucky_draw_leads_with_them_unscoped(self):
        self._pick(self.python_id, self.gig_id)
        payload = build_payload(self.owner, lucky=True, seed=1)
        self.assertEqual(self._ids(payload["featured"]), [self.python_id, self.gig_id])
        self.assertNotIn(self.gig_id, self._ids(payload["more"]))

    def test_a_lucky_draw_without_them_leads_with_the_favourites(self):
        _standard(self.owner)
        payload = build_payload(self.owner, lucky=True, seed=1)
        self.assertEqual(self._ids(payload["featured"]), [self.fav_id])

    def test_the_default_link_still_catches_an_empty_page(self):
        """A guard: green before this guide too."""
        Domain.objects.create(user=self.owner, name="empty")
        self._pick(self.gig_id)
        PortfolioLink.objects.create(
            user=self.owner,
            kind=PortfolioLink.Kind.manual,
            slug="for-all",
            is_default=True,
            content={"featured": [self.fav_id], "hide_explore": True},
        )
        payload = build_payload(self.owner, domains=["empty"])
        self.assertEqual(self._ids(payload["featured"]), [self.fav_id])
        self.assertEqual(payload["kind"], "native")

    @mock.patch("spa.portfolio.complete", return_value="Hello.")
    def test_the_ai_welcome_is_grounded_in_them(self, m_complete):
        self._pick(self.gig_id)
        build_intro(self.owner, domains=["music"], question="gigs")
        prompt = m_complete.call_args.kwargs["prompt"]
        self.assertIn("HIGHLIGHTS:\n- Gig\n", prompt)

    def test_the_standard_address_shows_it(self):
        """What the SPA asks for at `standard_page_url`."""
        self._pick(self.gig_id, title="Hi, I'm Lukas")
        r = self.client.get("/api/spa/public/lukas/native/?focus=balanced&tone=neutral")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["title"], "Hi, I'm Lukas")
        self.assertEqual(self._ids(r.json()["featured"]), [self.gig_id])


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


# ── public endpoints (meta / personalise) + landing ───────────────────────────────────


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

    def _personalise(self, **data):
        """A questionnaire answer as the SPA sends it, with a freshly solved check unless the
        test brings its own (or none: `captcha=None` leaves the field out)."""
        body = {
            "domains": ["music"],
            "query": "gigs",
            "tone": "personal",
            "focus": "technical",
            "captcha": solved_captcha(),
        }
        body.update(data)
        body = {k: v for k, v in body.items() if v is not None}
        with mock.patch("llm_connector.tasks.run_ai_job"):
            return self.client.post(
                f"{self.API}personalise/", data=body, content_type="application/json"
            )

    def _status(self, r):
        return self.client.get(f"/api/spa/public/personalise/{r.json()['id']}/").json()

    RANKED = [{"id": "job:1", "score": 0.9}]

    @mock.patch("spa.portfolio.complete", return_value="Hello there.")
    def test_it_is_queued_then_read_by_its_job_id(self, m_complete):
        """Pages first, AI second: the POST answers at once, whatever the model server is doing."""
        r = self._personalise()
        self.assertEqual(r.status_code, 202)
        self.assertEqual(
            self._status(r), {"status": "pending", "waking": False, "intro": ""}
        )
        m_complete.assert_not_called()
        with mock.patch("spa.ai_jobs.rank_for_query", return_value=self.RANKED) as rank:
            run_ai_job(r.json()["id"])
        rank.assert_called_once_with(self.owner, "gigs", ["music"])
        self.assertEqual(
            self._status(r),
            {
                "status": "done",
                "waking": False,
                "ranked": self.RANKED,
                "intro": "Hello there.",
            },
        )

    def test_the_ranking_shows_before_the_welcome_is_written(self):
        """The waiting page ticks the ranking off while the welcome is still being written. Until
        then `ranked` is absent: null means there's none to come."""
        r = self._personalise()
        seen = {}

        def write(**kwargs):
            seen["status"] = self._status(r)
            return "Hello there."

        with (
            mock.patch("spa.ai_jobs.rank_for_query", return_value=self.RANKED),
            mock.patch("spa.portfolio.complete", side_effect=write),
        ):
            run_ai_job(r.json()["id"])
        self.assertEqual(
            seen["status"],
            {"status": "running", "waking": False, "ranked": self.RANKED, "intro": ""},
        )

    @mock.patch("spa.portfolio.complete", return_value="Hello there.")
    def test_without_a_question_nothing_is_ranked(self, _complete):
        r = self._personalise(query="")
        with mock.patch("spa.ai_jobs.rank_for_query") as rank:
            run_ai_job(r.json()["id"])
        rank.assert_not_called()
        self.assertEqual(
            self._status(r),
            {
                "status": "done",
                "waking": False,
                "ranked": None,
                "intro": "Hello there.",
            },
        )

    @mock.patch("spa.portfolio.complete", return_value="Hello there.")
    def test_a_broken_ranking_still_gets_a_welcome(self, _complete):
        r = self._personalise()
        with (
            mock.patch("spa.ai_jobs.rank_for_query", side_effect=ValueError("boom")),
            self.assertLogs("spa.ai_jobs", level="WARNING"),
        ):
            run_ai_job(r.json()["id"])
        body = self._status(r)
        self.assertEqual((body["status"], body["ranked"]), ("done", None))
        self.assertEqual(body["intro"], "Hello there.")

    def test_a_model_server_that_never_wakes_fails_the_job(self):
        """Past the wait, the ranking's transport error ends the job: the welcome would only wait
        for the same server. The page then keeps the standard portfolio."""
        r = self._personalise()
        with (
            mock.patch(
                "spa.ai_jobs.rank_for_query", side_effect=LLMTransportError("off")
            ),
            mock.patch("spa.portfolio.complete") as m_complete,
            self.assertLogs("llm_connector.tasks", level="WARNING"),
        ):
            run_ai_job(r.json()["id"])
        m_complete.assert_not_called()
        self.assertEqual(
            self._status(r), {"status": "failed", "waking": False, "intro": ""}
        )

    @mock.patch("spa.portfolio.complete", side_effect=LLMTransportError("off"))
    def test_a_welcome_that_fails_leaves_the_ranking(self, _complete):
        r = self._personalise()
        with (
            mock.patch("spa.ai_jobs.rank_for_query", return_value=self.RANKED),
            self.assertLogs("spa.portfolio", level="WARNING"),
        ):
            run_ai_job(r.json()["id"])
        body = self._status(r)
        self.assertEqual((body["status"], body["ranked"]), ("done", self.RANKED))
        self.assertEqual(body["intro"], "")

    def test_it_says_when_the_model_server_is_waking(self):
        r = self._personalise()
        AiJob.objects.filter(pk=r.json()["id"]).update(
            status=AiJob.Status.running, stage=WAITING_STAGE
        )
        self.assertTrue(self._status(r)["waking"])

    @mock.patch("spa.ai_jobs.rank_for_query", return_value=[])
    @mock.patch("spa.portfolio.complete", return_value="Hello there.")
    def test_the_visitors_question_is_gone_when_the_job_ends(self, _c, _r):
        r = self._personalise()
        job = AiJob.objects.get(pk=r.json()["id"])
        self.assertEqual(job.payload["query"], "gigs")
        run_ai_job(str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.payload, {})

    def test_only_portfolio_jobs_are_readable_without_a_login(self):
        job = AiJob.objects.create(
            kind="letter_rewrite", user=self.owner, result={"text": "private"}
        )
        r = self.client.get(f"/api/spa/public/personalise/{job.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_it_runs_for_the_handles_owner(self):
        jane = _owner("jane")
        with mock.patch("llm_connector.tasks.run_ai_job"):
            r = self.client.post(
                "/api/spa/public/jane/native/personalise/",
                data={"captcha": solved_captcha()},
                content_type="application/json",
            )
        self.assertEqual(AiJob.objects.get(pk=r.json()["id"]).user, jane)

    def test_it_rejects_an_overlong_query(self):
        r = self._personalise(query="x" * 1000)
        self.assertEqual(r.status_code, 400)
        self.assertIn("query", r.json())

    def test_the_seventh_in_an_hour_is_throttled(self):
        """The one anonymous call that costs model-server compute: the tight scope is the valve."""
        codes = [self._personalise().status_code for _ in range(7)]
        self.assertEqual(codes, [202] * 6 + [429])

    # The check in front of the AI (guide 30): every job wakes the model server.

    def _refused(self, r):
        self.assertEqual(r.status_code, 400)
        self.assertEqual(list(r.json()), ["captcha"])
        self.assertFalse(AiJob.objects.filter(kind="portfolio_personalise").exists())

    def test_without_a_check_no_job_is_queued(self):
        self._refused(self._personalise(captcha=None))

    def test_a_bad_check_queues_no_job(self):
        for junk in ("", "x", "A" * 5000):
            with self.subTest(junk=junk[:8]):
                self._refused(self._personalise(captcha=junk))

    def test_a_check_queues_one_job_and_no_second(self):
        captcha = solved_captcha()
        self.assertEqual(self._personalise(captcha=captcha).status_code, 202)
        again = self._personalise(captcha=captcha)
        self.assertEqual(again.status_code, 400)
        self.assertEqual(list(again.json()), ["captcha"])
        self.assertEqual(AiJob.objects.filter(kind="portfolio_personalise").count(), 1)

    def test_a_refused_field_leaves_the_check_unspent(self):
        """The check comes last: an answer the serializer refuses doesn't use the payload up, so
        the visitor's next try still has it."""
        captcha = solved_captcha()
        self.assertEqual(
            self._personalise(query="x" * 1000, captcha=captcha).status_code, 400
        )
        self.assertEqual(self._personalise(captcha=captcha).status_code, 202)

    def test_the_check_stays_out_of_the_job(self):
        """The job's payload is the answer: the check has done its work at the door."""
        r = self._personalise()
        payload = AiJob.objects.get(pk=r.json()["id"]).payload
        self.assertEqual(set(payload), {"domains", "query", "focus", "tone"})


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
class BrandTests(TestCase):
    """Guide 31: the UI wears one colour, from the account the page belongs to. The chain is
    `spa.portfolio.brand_for`: the user's accent (`UserProfile.accent_color`, the CV's colour
    too), else the site owner's, else "" (frontend/src/theme.css then wears its fallback). jac
    wears the signed-in user's (the profile's read-only `brand`), a portfolio its owner's (the
    payload's and the meta's `accent`), every other page the site owner's
    (`/api/spa/public/brand/`). A colour is no contact detail, so it doesn't wait for
    `show_socials`. `brand_for` is reached through the module: it's new in guide 31."""

    PROFILE_URL = "/api/spa/profile/"
    SITE_URL = "/api/spa/public/brand/"

    def setUp(self):
        cache.clear()  # the public endpoints are throttled
        self.lukas = _owner("lukas")  # the site owner
        self.owner = _owner("jane")

    def _pick(self, user, colour):
        UserProfile.objects.filter(user=user).update(accent_color=colour)
        user.refresh_from_db()

    # --- the chain ---

    def test_the_users_own_colour_comes_first(self):
        self._pick(self.lukas, "#8f4700")
        self._pick(self.owner, "#1a7f56")
        self.assertEqual(portfolio.brand_for(self.owner), "#1a7f56")

    def test_without_one_the_site_owners(self):
        self._pick(self.lukas, "#8f4700")
        self.assertEqual(portfolio.brand_for(self.owner), "#8f4700")
        self.assertEqual(portfolio.brand_for(None), "#8f4700")  # a page nobody owns

    def test_without_either_its_blank(self):
        self.assertEqual(portfolio.brand_for(self.owner), "")
        self.assertEqual(portfolio.brand_for(None), "")

    @override_settings(PORTFOLIO_OWNER_USERNAME="")
    def test_without_a_site_owner_its_the_users_or_blank(self):
        self._pick(self.lukas, "#8f4700")
        self.assertEqual(portfolio.brand_for(self.owner), "")
        self.assertEqual(portfolio.brand_for(None), "")
        self._pick(self.owner, "#1a7f56")
        self.assertEqual(portfolio.brand_for(self.owner), "#1a7f56")

    def test_only_a_plain_hex_in_lower_case(self):
        """The Django pages put it into a style attribute, and `.update()` skips the model's
        validator: anything but `#rrggbb` counts as no colour."""
        self._pick(self.lukas, "#8f4700")
        self._pick(self.owner, "#1A7F56")
        self.assertEqual(portfolio.brand_for(self.owner), "#1a7f56")
        for junk in ("red", "#1a7f5", "#1a7f5z", "1a7f56", "#1a7;}"):
            with self.subTest(junk=junk):
                self._pick(self.owner, junk)
                self.assertEqual(portfolio.brand_for(self.owner), "#8f4700")

    # --- a portfolio wears its owner's ---

    def test_every_kind_of_page_wears_the_owners_else_the_site_owners(self):
        link = PortfolioLink.objects.create(
            user=self.owner, kind=PortfolioLink.Kind.manual, slug="for-acme"
        )
        def accents():
            return {
                build_payload(self.owner)["owner"]["accent"],
                build_payload(self.owner, link=link)["owner"]["accent"],
                build_payload(self.owner, lucky=True, seed=1)["owner"]["accent"],
            }

        self.assertEqual(accents(), {""})
        self._pick(self.lukas, "#8f4700")
        self.assertEqual(accents(), {"#8f4700"})
        self._pick(self.owner, "#1a7f56")
        self.assertEqual(accents(), {"#1a7f56"})

    def test_it_doesnt_wait_for_the_socials_opt_in(self):
        self._pick(self.owner, "#1a7f56")
        UserProfile.objects.filter(user=self.owner).update(
            website="https://example.com", show_socials=False
        )
        self.owner.refresh_from_db()
        owner = build_payload(self.owner)["owner"]
        self.assertEqual(owner["accent"], "#1a7f56")
        self.assertNotIn("website", owner)

    def test_the_public_endpoints_carry_it(self):
        self._pick(self.lukas, "#8f4700")
        PortfolioLink.objects.create(
            user=self.owner, kind=PortfolioLink.Kind.manual, slug="for-acme"
        )
        api = "/api/spa/public/jane/"
        for url in (f"{api}native/", f"{api}links/for-acme/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertEqual(r.json()["owner"]["accent"], "#8f4700", url)

    def test_the_questionnaires_meta_carries_it(self):
        """The questionnaire has no payload yet: its meta says the same colour."""
        meta = "/api/spa/public/jane/native/meta/"
        self.assertEqual(self.client.get(meta).json()["accent"], "")
        self._pick(self.lukas, "#8f4700")
        self.assertEqual(self.client.get(meta).json()["accent"], "#8f4700")
        self._pick(self.owner, "#1a7f56")
        self.assertEqual(self.client.get(meta).json()["accent"], "#1a7f56")

    # --- jac wears the signed-in user's ---

    def test_the_profile_says_which_colour_jac_wears(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.PROFILE_URL).json()["brand"], "")
        self._pick(self.lukas, "#8f4700")
        body = self.client.get(self.PROFILE_URL).json()
        self.assertEqual(body["brand"], "#8f4700")
        self.assertEqual(body["accent_color"], "")  # the CV's own rule: blank is the layout's
        self._pick(self.owner, "#1a7f56")
        self.assertEqual(self.client.get(self.PROFILE_URL).json()["brand"], "#1a7f56")

    def test_the_profiles_brand_is_read_only(self):
        self.client.force_login(self.owner)
        r = self.client.patch(
            self.PROFILE_URL,
            data=json.dumps({"brand": "#000000"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.profile.accent_color, "")
        self.assertEqual(r.json()["brand"], "")

    # --- every other page wears the site owner's ---

    def test_the_site_brand_is_the_site_owners(self):
        r = self.client.get(self.SITE_URL)  # anonymous
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"brand": ""})
        self.assertEqual(r["X-Robots-Tag"], "noindex")
        self._pick(self.owner, "#1a7f56")  # a portfolio owner's is no site colour
        self.assertEqual(self.client.get(self.SITE_URL).json(), {"brand": ""})
        self._pick(self.lukas, "#8f4700")
        self.assertEqual(self.client.get(self.SITE_URL).json(), {"brand": "#8f4700"})

    def test_the_site_brand_isnt_throttled(self):
        """Every SPA load asks once. On the `portfolio` scope it would eat the budget a visitor's
        portfolio pages need, and it says nothing the landing doesn't print for anyone."""
        allowed = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["portfolio"].split("/")[0])
        codes = {self.client.get(self.SITE_URL).status_code for _ in range(allowed + 1)}
        self.assertEqual(codes, {200})


@override_settings(PORTFOLIO_OWNER_USERNAME="lukas")
class PortfolioPrewarmTests(TestCase):
    """A visitor has solved the questionnaire's check: wake the model server ahead of the rank and
    the intro. No login, a solved check (guide 30), and nothing for a portfolio that isn't there."""

    URL = "/api/spa/public/{}/native/prewarm/"

    def setUp(self):
        cache.clear()  # scoped-throttle history and the checks' records live in the default cache

    def _post(self, handle="lukas", captcha=None):
        body = {} if captcha is None else {"captcha": captcha}
        return self.client.post(
            self.URL.format(handle), data=body, content_type="application/json"
        )

    def test_a_visitor_asks_for_it(self):
        _owner("lukas")
        with mock.patch("spa.views.request_prewarm") as prewarm:
            r = self._post(captcha=solved_captcha())
        self.assertEqual(r.status_code, 204)
        prewarm.assert_called_once()

    def test_without_a_check_nothing_wakes(self):
        _owner("lukas")
        with mock.patch("spa.views.request_prewarm") as prewarm:
            r = self._post()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(list(r.json()), ["captcha"])
        prewarm.assert_not_called()

    def test_a_bad_check_wakes_nothing(self):
        _owner("lukas")
        with mock.patch("spa.views.request_prewarm") as prewarm:
            for junk in ("", "x", "A" * 5000):
                r = self._post(captcha=junk)
                self.assertEqual(r.status_code, 400, junk[:8])
                self.assertEqual(list(r.json()), ["captcha"], junk[:8])
        prewarm.assert_not_called()

    def test_a_check_wakes_once(self):
        _owner("lukas")
        captcha = solved_captcha()
        with mock.patch("spa.views.request_prewarm") as prewarm:
            self.assertEqual(self._post(captcha=captcha).status_code, 204)
            self.assertEqual(self._post(captcha=captcha).status_code, 400)
        prewarm.assert_called_once()

    def test_one_tick_serves_the_prewarm_and_the_personalise(self):
        """The questionnaire sends the same payload twice: the prewarm on the tick, the job on
        "Show me". Each purpose takes it once."""
        _owner("lukas")
        captcha = solved_captcha()
        with (
            mock.patch("spa.views.request_prewarm"),
            mock.patch("llm_connector.tasks.run_ai_job"),
        ):
            self.assertEqual(self._post(captcha=captcha).status_code, 204)
            r = self.client.post(
                "/api/spa/public/lukas/native/personalise/",
                data={"query": "gigs", "captcha": captcha},
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 202)

    def test_no_portfolio_nothing_wakes(self):
        """Answered like every public path on a portfolio that isn't there: the 404 comes before
        the check, so a missing one doesn't tell handles apart."""
        _owner("hidden", published=False)
        UserProfile.objects.filter(user=_owner("squatter")).update(handle="admin")
        with mock.patch("spa.views.request_prewarm") as prewarm:
            for handle in ("nobody", "hidden", "admin"):
                for captcha in (None, solved_captcha()):
                    r = self._post(handle, captcha)
                    self.assertEqual(r.status_code, 404, handle)
        prewarm.assert_not_called()

    def test_loading_the_questionnaire_wakes_nothing(self):
        """Crawlers load pages, and they can fetch a challenge too. Only a solved one wakes the
        tower."""
        _owner("lukas")
        with mock.patch("spa.views.request_prewarm") as prewarm:
            self.client.get("/api/spa/public/lukas/native/meta/")
            self.client.get("/api/spa/public/captcha/")
        prewarm.assert_not_called()


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


class SkillNetworkPayloadTests(TestCase):
    """The portfolio draws skills as a network around the jobs and projects that used them. A
    job's or project's `skills` are its links to skills that are on the page too: both ends
    shown, or no line. A link page must not reveal an entry it didn't select."""

    def setUp(self):
        self.owner = _owner()
        self.python = _skill(self.owner, name="Python")
        self.django = _skill(self.owner, name="Django")
        self.job = _job(self.owner)
        self.job.skills.add(self.python, self.django)
        self.project = Project.objects.create(user=self.owner, name="jac")
        self.project.skills.add(self.django)

    def _by_id(self, payload):
        return {i["id"]: i for i in payload["featured"] + payload["more"]}

    def test_jobs_and_projects_name_their_skills_on_the_page(self):
        items = self._by_id(build_payload(self.owner))
        self.assertEqual(
            sorted(items[f"job:{self.job.pk}"]["skills"]),
            sorted([f"skill:{self.python.pk}", f"skill:{self.django.pk}"]),
        )
        self.assertEqual(
            items[f"project:{self.project.pk}"]["skills"], [f"skill:{self.django.pk}"]
        )

    def test_only_jobs_and_projects_carry_skills(self):
        items = self._by_id(build_payload(self.owner))
        self.assertNotIn("skills", items[f"skill:{self.python.pk}"])

    def test_a_skill_off_the_page_draws_no_line(self):
        """A link that selected the job and one skill shows that one line only."""
        link = PortfolioLink.objects.create(
            user=self.owner,
            kind=PortfolioLink.Kind.manual,
            slug="for-acme",
            content={
                "featured": [f"job:{self.job.pk}", f"skill:{self.python.pk}"],
                "hide_explore": True,
            },
        )
        items = self._by_id(build_payload(self.owner, link=link))
        self.assertEqual(
            items[f"job:{self.job.pk}"]["skills"], [f"skill:{self.python.pk}"]
        )

    def test_a_job_without_skills_on_the_page_has_an_empty_list(self):
        self.job.skills.clear()
        items = self._by_id(build_payload(self.owner))
        self.assertEqual(items[f"job:{self.job.pk}"]["skills"], [])

    def test_more_jobs_cost_no_more_queries(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                build_payload(self.owner)
            return len(ctx.captured_queries)

        one = count()
        for n in range(5):
            _job(self.owner, title=f"Dev {n}").skills.add(self.python)
        self.assertEqual(count(), one)


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


# --- guide 26: the block kinds -----------------------------------------------------------

QUOTE = {"by": "Ada Lovelace", "role": "Analyst"}
FIGURES = {
    "figures": [
        {"value": "40%", "label": "faster builds"},
        {"value": "3", "label": "teams"},
    ]
}
CTA = {"label": "Write to me", "url": "mailto:jane@example.com"}
LINKLIST = {
    "items": [
        {"title": "Talk", "url": "https://example.com/talk", "note": "Twenty minutes"},
        {"title": "Paper", "url": "http://example.org/paper.pdf", "note": ""},
    ]
}


class BlockKindsApiTests(TestCase):
    """The kind table: what each kind needs, what its `data` may hold (spa/blocks.py), on a
    create and on a PATCH that leaves things out."""

    URL = "/api/spa/portfolio/manage/blocks/"

    def setUp(self):
        self.user = _owner("blocker")
        self.client.force_login(self.user)

    def _post(self, **body):
        return self.client.post(
            self.URL, data=json.dumps(body), content_type="application/json"
        )

    def _patch(self, block, **body):
        return self.client.patch(
            f"{self.URL}{block.pk}/",
            data=json.dumps(body),
            content_type="application/json",
        )

    def _refused(self, res, field="data"):
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn(field, res.json())

    def _stored(self, res) -> PortfolioBlock:
        self.assertIn(res.status_code, (200, 201), res.content)
        return PortfolioBlock.objects.get(pk=res.json()["id"])

    def test_every_new_kind_is_created_with_its_fields(self):
        for kind, body, data in (
            ("quote", "Code is poetry.", QUOTE),
            ("figures", "", FIGURES),
            ("cta", "Hiring?", CTA),
            ("linklist", "", LINKLIST),
        ):
            with self.subTest(kind):
                block = self._stored(self._post(kind=kind, body=body, data=data))
                self.assertEqual(block.kind, kind)
                self.assertEqual(block.data, data)

    def test_the_api_answers_with_data_and_media(self):
        self._post(kind="quote", body="Code is poetry.", data=QUOTE)
        [row] = self.client.get(self.URL).json()
        self.assertEqual(row["data"], QUOTE)
        self.assertIsNone(row["media"])

    def test_strings_are_stripped_and_unknown_keys_dropped(self):
        block = self._stored(
            self._post(
                kind="quote",
                body="Code is poetry.",
                data={"by": "  Ada  ", "role": " CTO ", "email": "ada@example.com"},
            )
        )
        self.assertEqual(block.data, {"by": "Ada", "role": "CTO"})

    def test_a_kind_without_fields_of_its_own_stores_no_data(self):
        block = self._stored(self._post(kind="text", body="Hi", data={"by": "Ada"}))
        self.assertEqual(block.data, {})

    def test_a_quote_needs_its_words_and_who_said_them(self):
        self._refused(self._post(kind="quote", data=QUOTE), "body")
        self._refused(self._post(kind="quote", body="Code is poetry.", data={}))
        self._refused(
            self._post(kind="quote", body="x", data={"by": "A" * 101}),
        )
        self._refused(
            self._post(kind="quote", body="x", data={"by": "Ada", "role": "R" * 151}),
        )

    def test_key_figures_take_two_to_four_rows(self):
        row = {"value": "3", "label": "teams"}
        for rows in ([row], [row] * 5, "3 teams"):
            with self.subTest(rows=rows):
                self._refused(self._post(kind="figures", data={"figures": rows}))
        for bad in (
            {"value": "1234567890123", "label": "too long a value"},
            {"value": "3", "label": ""},
            {"value": 3, "label": "teams"},
            "3 teams",
        ):
            with self.subTest(bad=bad):
                self._refused(self._post(kind="figures", data={"figures": [row, bad]}))
        self._stored(self._post(kind="figures", data={"figures": [row] * 4}))

    def test_a_call_to_action_links_to_https_or_mail_only(self):
        for url in (
            "javascript:alert(1)",
            "http://example.com",
            "ftp://example.com",
            "data:text/html,<script>alert(1)</script>",
            "mailto:not-an-address",
            "",
        ):
            with self.subTest(url=url):
                self._refused(self._post(kind="cta", data={"label": "Go", "url": url}))
        for url in (
            "https://example.com/contact",
            "mailto:jane@example.com?subject=Hi",
        ):
            with self.subTest(url=url):
                self._stored(self._post(kind="cta", data={"label": "Go", "url": url}))
        self._refused(
            self._post(kind="cta", data={"label": "L" * 41, "url": "https://x.de"})
        )

    def test_a_link_list_takes_one_to_twelve_web_links(self):
        item = {"title": "Talk", "url": "https://example.com/talk"}
        for items in ([], [item] * 13):
            with self.subTest(count=len(items)):
                self._refused(self._post(kind="linklist", data={"items": items}))
        for bad in (
            {"title": "Talk", "url": "javascript:alert(1)"},
            {"title": "Talk", "url": "mailto:jane@example.com"},
            {"title": "", "url": "https://example.com"},
            {"title": "Talk", "url": "https://example.com", "note": "N" * 201},
        ):
            with self.subTest(bad=bad):
                self._refused(self._post(kind="linklist", data={"items": [bad]}))
        block = self._stored(self._post(kind="linklist", data={"items": [item] * 12}))
        self.assertEqual(block.data["items"][0]["note"], "")

    def test_data_must_be_an_object(self):
        for data in (["Ada"], "Ada"):
            with self.subTest(data=data):
                self._refused(self._post(kind="quote", body="x", data=data))

    def test_a_new_block_without_a_kind_is_a_text_block(self):
        self._refused(self._post(title="Untitled"), "body")

    def test_a_patch_that_switches_kind_is_checked_against_what_is_stored(self):
        block = PortfolioBlock.objects.create(user=self.user, kind="text", body="Hi")
        self._refused(self._patch(block, kind="quote"))
        block = self._stored(self._patch(block, kind="quote", data={"by": "Ada"}))
        self.assertEqual((block.kind, block.body), ("quote", "Hi"))

    def test_a_patch_that_switches_kind_drops_the_old_kinds_data(self):
        block = PortfolioBlock.objects.create(
            user=self.user, kind="quote", body="Hi", data=QUOTE
        )
        block = self._stored(self._patch(block, kind="text"))
        self.assertEqual(block.data, {})

    def test_a_patch_of_data_alone_follows_the_stored_kind(self):
        block = PortfolioBlock.objects.create(user=self.user, kind="cta", data=CTA)
        self._refused(self._patch(block, data={"label": "Mail me"}))
        block = self._stored(self._patch(block, title="Contact"))
        self.assertEqual(block.data, CTA)

    def test_data_travels_over_multipart_as_json_text(self):
        """The editor goes multipart whenever a file rides along; `data` is then a text field."""
        res = self.client.post(
            self.URL, {"kind": "linklist", "data": json.dumps(LINKLIST)}
        )
        self.assertEqual(self._stored(res).data, LINKLIST)

    def test_a_nul_in_multipart_data_is_refused(self):
        """A JSON body never gets this far (lukehirsch.parsers), a multipart one does."""
        res = self.client.post(
            self.URL,
            {"kind": "quote", "body": "x", "data": json.dumps({"by": "Ada\u0000"})},
        )
        self._refused(res)

    def test_the_admin_runs_the_same_rules(self):
        from django.core.exceptions import ValidationError

        for kind, body, data in (
            ("quote", "Hi", {}),
            ("cta", "", {"label": "Go", "url": "javascript:alert(1)"}),
            ("quote", "", QUOTE),
        ):
            block = PortfolioBlock(user=self.user, kind=kind, body=body, data=data)
            with self.subTest(kind=kind, data=data), self.assertRaises(ValidationError):
                block.full_clean()
        block = PortfolioBlock(
            user=self.user, kind="quote", body="Hi", data={"by": " Ada ", "x": 1}
        )
        block.full_clean()
        self.assertEqual(block.data, {"by": "Ada", "role": ""})


class BlockPayloadTests(TestCase):
    """The public item of a block: its `data` by the kind's rules, its media file and type."""

    def setUp(self):
        self.owner = _owner()

    def _item(self, **fields) -> dict:
        block = PortfolioBlock.objects.create(user=self.owner, **fields)
        [item] = resolve_items(self.owner, [f"block:{block.pk}"])
        return item

    def test_every_block_carries_data_and_media(self):
        item = self._item(kind="text", body="Hi")
        self.assertEqual(item["data"], {})
        self.assertIsNone(item["media_url"])
        self.assertIsNone(item["media_type"])

    def test_each_kind_shows_its_fields(self):
        for kind, data in (
            ("quote", QUOTE),
            ("figures", FIGURES),
            ("cta", CTA),
            ("linklist", LINKLIST),
        ):
            with self.subTest(kind):
                item = self._item(kind=kind, body="x", data=data)
                self.assertEqual((item["kind"], item["data"]), (kind, data))

    def test_only_the_kinds_own_keys_reach_the_page(self):
        item = self._item(
            kind="quote", body="x", data={**QUOTE, "email": "ada@example.com"}
        )
        self.assertEqual(item["data"], QUOTE)

    def test_a_link_written_past_the_serializer_never_reaches_the_page(self):
        item = self._item(
            kind="cta", data={"label": "Go", "url": "javascript:alert(1)"}
        )
        self.assertEqual(item["data"], {})

    def test_a_media_block_names_its_file_its_type_and_its_poster(self):
        # Names only, no files written: the payload reads the stored name.
        item = self._item(
            kind="media",
            media=f"portfolio/media/{self.owner.pk}/abc.m4a",
            image=f"portfolio/blocks/{self.owner.pk}/cover.png",
        )
        self.assertEqual(
            item["media_url"], f"/media/portfolio/media/{self.owner.pk}/abc.m4a"
        )
        self.assertEqual(item["media_type"], "audio/mp4")
        self.assertEqual(
            item["image_url"], f"/media/portfolio/blocks/{self.owner.pk}/cover.png"
        )

    def test_a_file_left_from_an_earlier_kind_isnt_shown(self):
        item = self._item(
            kind="text", body="Hi", media=f"portfolio/media/{self.owner.pk}/abc.mp4"
        )
        self.assertIsNone(item["media_url"])
        self.assertIsNone(item["media_type"])

    def test_the_media_type_follows_the_stored_extension(self):
        from spa.blocks import media_type

        for ext, expected in (
            ("mp4", "video/mp4"),
            ("webm", "video/webm"),
            ("mp3", "audio/mpeg"),
            ("m4a", "audio/mp4"),
            ("ogg", "audio/ogg"),
            ("mov", None),
        ):
            with self.subTest(ext=ext):
                block = PortfolioBlock(
                    user=self.owner, media=f"portfolio/media/1/x.{ext}"
                )
                self.assertEqual(media_type(block.media), expected)
        self.assertIsNone(media_type(PortfolioBlock(user=self.owner).media))


class BlockRankingTextTests(TestCase):
    """What the embed ranking reads of a block: its title, its body and the words in its data,
    so a key-figures block without a body still says what it's about."""

    def setUp(self):
        self.owner = _owner()

    def test_it_reads_the_words_in_the_data(self):
        from spa.blocks import block_text

        for kind, data, words in (
            ("quote", QUOTE, ["Ada Lovelace", "Analyst"]),
            ("figures", FIGURES, ["40% faster builds", "3 teams"]),
            ("cta", CTA, ["Write to me"]),
            ("linklist", LINKLIST, ["Talk", "Twenty minutes", "Paper"]),
        ):
            with self.subTest(kind):
                block = PortfolioBlock(
                    user=self.owner, kind=kind, title="Title", body="Body", data=data
                )
                text = block_text(block)
                for word in ["Title", "Body", *words]:
                    self.assertIn(word, text)
        self.assertNotIn(
            "example.com", block_text(PortfolioBlock(kind="cta", data=CTA))
        )

    def test_the_ranking_hands_it_to_the_embedder(self):
        from spa.portfolio import rank_for_query

        block = PortfolioBlock.objects.create(
            user=self.owner, kind="figures", data=FIGURES
        )
        with mock.patch("spa.portfolio.PortfolioEmbed") as embed:
            embed.return_value.ranked_entries.return_value = []
            rank_for_query(self.owner, "build speed", [])
        docs = embed.call_args.args[1]
        [doc] = [d for d in docs if d["id"] == f"block:{block.pk}"]
        self.assertIn("40% faster builds", doc["text"])
