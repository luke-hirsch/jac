"""API — user-scoping, CRUD behaviours, bulk actions."""

import json
from datetime import date, timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APITestCase

from jac.models import (
    Certification,
    Domain,
    Education,
    Job,
    Language,
    ResumeSnippet,
    Skill,
)


class JobViewSetScopingTests(APITestCase):
    """JobViewSet never leaks user A's rows to user B — not in list,
    retrieve, update, or delete. Tests the pattern used by every scoped jac
    viewset (all delegate scoping to get_queryset, so one representative
    viewset is sufficient).
    """

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice_jac", password="pass")
        cls.bob = User.objects.create_user(username="bob_jac", password="pass")
        cls.alice_job = Job.objects.create(
            user=cls.alice,
            title="Alice Engineer",
            company="AliceCo",
            started=date(2022, 1, 1),
        )

    def test_list_returns_only_own_jobs(self):
        self.client.force_login(self.alice)
        r = self.client.get("/api/jac/jobs/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.alice_job.pk, [row["id"] for row in r.data["results"]])

        self.client.force_login(self.bob)
        r = self.client.get("/api/jac/jobs/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 0)

    def test_retrieve_other_users_job_is_404(self):
        self.client.force_login(self.bob)
        r = self.client.get(f"/api/jac/jobs/{self.alice_job.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_patch_other_users_job_is_404(self):
        self.client.force_login(self.bob)
        r = self.client.patch(
            f"/api/jac/jobs/{self.alice_job.pk}/", {"title": "Hacked"}, format="json"
        )
        self.assertEqual(r.status_code, 404)

    def test_delete_other_users_job_is_404(self):
        self.client.force_login(self.bob)
        r = self.client.delete(f"/api/jac/jobs/{self.alice_job.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_unauthenticated_list_is_403(self):
        r = self.client.get("/api/jac/jobs/")
        self.assertIn(r.status_code, (401, 403))


class SkillOverrideAPITests(APITestCase):
    """`years_of_experience_override` is writable and `years_of_experience`
    transparently reflects it; the effective field itself stays read-only.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="skill_api", password="pass")
        cls.skill = Skill.objects.create(
            user=cls.user, name="C/C++", first_used=date(2010, 1, 1)
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_patch_override_changes_effective_years(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.skill.pk}/",
            {"years_of_experience_override": 4},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["years_of_experience_override"], 4)
        self.assertEqual(r.data["years_of_experience"], 4)

    def test_clearing_override_reverts_to_computed(self):
        self.skill.years_of_experience_override = 4
        self.skill.save()
        r = self.client.patch(
            f"/api/jac/skills/{self.skill.pk}/",
            {"years_of_experience_override": None},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["years_of_experience_override"])
        self.assertGreaterEqual(int(r.data["years_of_experience"]), 14)

    def test_years_of_experience_is_read_only(self):
        # Writing the effective field is silently ignored; the stored value
        # stays computed.
        r = self.client.patch(
            f"/api/jac/skills/{self.skill.pk}/",
            {"years_of_experience": 99},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.data["years_of_experience"], 99)
        self.assertIsNone(r.data["years_of_experience_override"])


class SkillRelatedSkillsAPITests(APITestCase):
    """The symmetric M2M ties skills together both ways, guards against
    self-reference, and refuses to point at another user's skill.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="rel_api", password="pass")
        cls.other = User.objects.create_user(username="rel_other", password="pass")
        cls.accounting = Skill.objects.create(user=cls.user, name="Accounting")
        cls.sevdesk = Skill.objects.create(user=cls.user, name="SevDesk")
        cls.foreign = Skill.objects.create(user=cls.other, name="Foreign")

    def setUp(self):
        self.client.force_login(self.user)

    def test_relation_is_symmetric(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.accounting.pk}/",
            {"related_skills": [self.sevdesk.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["related_skills"], [self.sevdesk.pk])

        # Symmetry: SevDesk now lists Accounting without us touching it.
        r = self.client.get(f"/api/jac/skills/{self.sevdesk.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.accounting.pk, r.data["related_skills"])

    def test_self_reference_is_rejected(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.accounting.pk}/",
            {"related_skills": [self.accounting.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_cannot_relate_to_another_users_skill(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.accounting.pk}/",
            {"related_skills": [self.foreign.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.accounting.refresh_from_db()
        self.assertEqual(self.accounting.related_skills.count(), 0)


class SkillBuildsOnAPITests(APITestCase):
    """`builds_on` is directed (unlike `related_skills`): setting it on A does
    not make B build on A; B instead lists A under the read-only `enables`.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="bo_api", password="pass")
        cls.other = User.objects.create_user(username="bo_other", password="pass")
        cls.drf = Skill.objects.create(user=cls.user, name="DRF")
        cls.django = Skill.objects.create(user=cls.user, name="Django")
        cls.foreign = Skill.objects.create(user=cls.other, name="Foreign")

    def setUp(self):
        self.client.force_login(self.user)

    def test_relation_is_directed_not_symmetric(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.drf.pk}/",
            {"builds_on": [self.django.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["builds_on"], [self.django.pk])

        # Django does NOT build on DRF, but lists it under `enables`.
        r = self.client.get(f"/api/jac/skills/{self.django.pk}/")
        self.assertEqual(r.data["builds_on"], [])
        self.assertIn(self.drf.pk, r.data["enables"])

    def test_self_reference_is_rejected(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.drf.pk}/",
            {"builds_on": [self.drf.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_cannot_build_on_another_users_skill(self):
        r = self.client.patch(
            f"/api/jac/skills/{self.drf.pk}/",
            {"builds_on": [self.foreign.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.drf.refresh_from_db()
        self.assertEqual(self.drf.builds_on.count(), 0)

    def test_enables_is_read_only(self):
        # Writing `enables` directly is silently ignored (read-only field).
        r = self.client.patch(
            f"/api/jac/skills/{self.drf.pk}/",
            {"enables": [self.django.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.drf.refresh_from_db()
        self.assertEqual(self.drf.enables.count(), 0)


class ResumeSnippetAPITests(APITestCase):
    """Snippets are user-scoped on create, list, and relation fields; `user`
    is never trusted from the body, choices/ownership are validated, and the
    `language` flag defaults to English and round-trips.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="snip_user", password="pass")
        cls.other = User.objects.create_user(username="snip_other", password="pass")
        cls.domain = Domain.objects.create(user=cls.user, name="Finance")
        cls.skill = Skill.objects.create(user=cls.user, name="Accounting")
        cls.foreign_skill = Skill.objects.create(user=cls.other, name="Foreign")
        cls.foreign_domain = Domain.objects.create(user=cls.other, name="ForeignDom")

    def setUp(self):
        self.client.force_login(self.user)

    def test_create_sets_user_from_request(self):
        r = self.client.post(
            "/api/jac/resume-snippets/",
            {
                "title": "Opening line",
                "content": "I build things people rely on.",
                "kind": "intro",
                "domains": [self.domain.pk],
                "skills": [self.skill.pk],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        snippet = ResumeSnippet.objects.get(pk=r.data["id"])
        self.assertEqual(snippet.user, self.user)

    def test_user_cannot_be_spoofed_via_body(self):
        r = self.client.post(
            "/api/jac/resume-snippets/",
            {
                "title": "Spoof",
                "content": "nope",
                "kind": "other",
                "user": self.other.pk,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        snippet = ResumeSnippet.objects.get(pk=r.data["id"])
        self.assertEqual(snippet.user, self.user)

    def test_list_is_user_scoped(self):
        ResumeSnippet.objects.create(
            user=self.other, title="Theirs", content="x", kind="intro"
        )
        mine = ResumeSnippet.objects.create(
            user=self.user, title="Mine", content="y", kind="intro"
        )
        r = self.client.get("/api/jac/resume-snippets/")
        self.assertEqual(r.status_code, 200)
        ids = [row["id"] for row in r.data["results"]]
        self.assertIn(mine.pk, ids)
        self.assertEqual(len(ids), 1)

    def test_kind_filter(self):
        intro = ResumeSnippet.objects.create(
            user=self.user, title="i", content="x", kind="intro"
        )
        ResumeSnippet.objects.create(
            user=self.user, title="c", content="y", kind="closing"
        )
        r = self.client.get("/api/jac/resume-snippets/?kind=intro")
        self.assertEqual(r.status_code, 200)
        ids = [row["id"] for row in r.data["results"]]
        self.assertEqual(ids, [intro.pk])

    def test_invalid_kind_is_rejected(self):
        r = self.client.post(
            "/api/jac/resume-snippets/",
            {"title": "x", "content": "y", "kind": "not_a_kind"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_cannot_reference_another_users_skill(self):
        r = self.client.post(
            "/api/jac/resume-snippets/",
            {
                "title": "x",
                "content": "y",
                "kind": "other",
                "skills": [self.foreign_skill.pk],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_cannot_reference_another_users_domain(self):
        r = self.client.post(
            "/api/jac/resume-snippets/",
            {
                "title": "x",
                "content": "y",
                "kind": "other",
                "domains": [self.foreign_domain.pk],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_language_defaults_to_en(self):
        s = ResumeSnippet.objects.create(
            user=self.user, title="x", content="y", kind="intro"
        )
        self.assertEqual(s.language, "en")

    def test_language_round_trips_through_api(self):
        r = self.client.post(
            "/api/jac/resume-snippets/",
            {
                "title": "Hallo",
                "content": "Ich baue Dinge.",
                "kind": "intro",
                "language": "de",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["language"], "de")
        self.assertEqual(ResumeSnippet.objects.get(pk=r.data["id"]).language, "de")


class BulkDeleteAPITests(APITestCase):
    """`POST <resource>/bulk/ {"action":"delete"}` removes the user's own rows
    in one request, and refuses the whole batch if any id isn't theirs.
    """

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="bulk_alice", password="pass")
        cls.bob = User.objects.create_user(username="bulk_bob", password="pass")
        cls.s1 = Skill.objects.create(user=cls.alice, name="Python")
        cls.s2 = Skill.objects.create(user=cls.alice, name="Django")
        cls.bob_skill = Skill.objects.create(user=cls.bob, name="Rust")

    def setUp(self):
        self.client.force_login(self.alice)

    def test_bulk_delete_removes_own_rows(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": [self.s1.pk, self.s2.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"deleted": 2})
        self.assertFalse(Skill.objects.filter(pk__in=[self.s1.pk, self.s2.pk]).exists())

    def test_nonexistent_id_aborts_whole_batch(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": [self.s1.pk, 999999]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("ids", r.data)
        self.assertTrue(Skill.objects.filter(pk=self.s1.pk).exists())  # nothing deleted

    def test_cannot_delete_another_users_row(self):
        # bob's id is "missing" from alice's get_queryset() → 400, his row intact.
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": [self.s1.pk, self.bob_skill.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Skill.objects.filter(pk=self.bob_skill.pk).exists())

    def test_unknown_action_is_400(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "nope", "ids": [self.s1.pk]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_non_integer_ids_is_400(self):
        r = self.client.post(
            "/api/jac/skills/bulk/",
            {"action": "delete", "ids": ["not-an-int"]},
            format="json",
        )
        self.assertEqual(r.status_code, 400)


class BulkPatchDomainsAPITests(APITestCase):
    """`patch_domains` merges domains onto the user's rows (add/remove, not
    replace), only accepts domains the user may see, and only exists on
    resources that actually carry a `domains` M2M.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="bpd_user", password="pass")
        cls.other = User.objects.create_user(username="bpd_other", password="pass")
        cls.system = User.objects.create_user(username=settings.SYSTEM_USER_USERNAME)
        cls.d_keep = Domain.objects.create(user=cls.user, name="Keep")
        cls.d_remove = Domain.objects.create(user=cls.user, name="Remove")
        cls.d_add = Domain.objects.create(user=cls.user, name="Add")
        cls.d_default = Domain.objects.create(user=cls.system, name="Backend")
        cls.foreign = Domain.objects.create(user=cls.other, name="Foreign")
        cls.job = Job.objects.create(
            user=cls.user, title="Eng", company="Co", started=date(2022, 1, 1)
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.job.domains.set([self.d_keep, self.d_remove])

    def test_add_and_remove_preserve_the_rest(self):
        r = self.client.post(
            "/api/jac/jobs/bulk/",
            {
                "action": "patch_domains",
                "ids": [self.job.pk],
                "add": [self.d_add.pk],
                "remove": [self.d_remove.pk],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"updated": 1})
        self.assertEqual(
            set(self.job.domains.values_list("pk", flat=True)),
            {self.d_keep.pk, self.d_add.pk},  # kept Keep, gained Add, lost Remove
        )

    def test_system_default_domain_is_allowed(self):
        r = self.client.post(
            "/api/jac/jobs/bulk/",
            {
                "action": "patch_domains",
                "ids": [self.job.pk],
                "add": [self.d_default.pk],
                "remove": [],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.d_default.pk, self.job.domains.values_list("pk", flat=True))

    def test_foreign_domain_is_rejected(self):
        r = self.client.post(
            "/api/jac/jobs/bulk/",
            {
                "action": "patch_domains",
                "ids": [self.job.pk],
                "add": [self.foreign.pk],
                "remove": [],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertNotIn(self.foreign.pk, self.job.domains.values_list("pk", flat=True))

    def test_patch_domains_unsupported_on_domainless_resource(self):
        lang = Language.objects.create(user=self.user, name="German")
        r = self.client.post(
            "/api/jac/languages/bulk/",
            {"action": "patch_domains", "ids": [lang.pk], "add": [], "remove": []},
            format="json",
        )
        self.assertEqual(r.status_code, 400)


class OrderingFieldsAPITests(APITestCase):
    """`updated_at` is now an allowed ordering (the `/cv` dashboard relies on
    it); a field outside the allow-list is ignored, not honoured.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="ord_user", password="pass")
        cls.a = Job.objects.create(
            user=cls.user, title="Older", company="Co", started=date(2020, 1, 1)
        )
        cls.b = Job.objects.create(
            user=cls.user, title="Newer", company="Co", started=date(2023, 1, 1)
        )
        now = timezone.now()
        Job.objects.filter(pk=cls.a.pk).update(updated_at=now)
        Job.objects.filter(pk=cls.b.pk).update(updated_at=now - timedelta(days=1))

    def setUp(self):
        self.client.force_login(self.user)

    def test_ordering_by_updated_at_is_honoured(self):
        r = self.client.get("/api/jac/jobs/?ordering=-updated_at")
        self.assertEqual(r.status_code, 200)
        ids = [row["id"] for row in r.data["results"]]
        self.assertEqual(ids, [self.a.pk, self.b.pk])  # most-recently-updated first

    def test_disallowed_ordering_field_falls_back_to_default(self):
        r = self.client.get("/api/jac/jobs/?ordering=title")
        self.assertEqual(r.status_code, 200)
        ids = [row["id"] for row in r.data["results"]]
        self.assertEqual(ids, [self.b.pk, self.a.pk])


class DomainIsDefaultAPITests(APITestCase):
    """`is_default` is a read-only flag: true for the sentinel-owned shared
    taxonomy, false for the user's own tags, and never writable.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="def_user", password="pass")
        cls.system = User.objects.create_user(username=settings.SYSTEM_USER_USERNAME)
        cls.own = Domain.objects.create(user=cls.user, name="Mine")
        cls.default = Domain.objects.create(user=cls.system, name="Backend")

    def setUp(self):
        self.client.force_login(self.user)

    def test_flag_distinguishes_default_from_own(self):
        r = self.client.get("/api/jac/domains/")
        self.assertEqual(r.status_code, 200)
        flags = {row["id"]: row["is_default"] for row in r.data["results"]}
        self.assertFalse(flags[self.own.pk])
        self.assertTrue(flags[self.default.pk])

    def test_is_default_is_read_only(self):
        r = self.client.patch(
            f"/api/jac/domains/{self.own.pk}/",
            {"is_default": True},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["is_default"])


class DomainFilterAPITests(APITestCase):
    """`?domains=<id>` narrows list endpoints to entries carrying that domain —
    including Education and Certification, which gained the filter in 3a-bis.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="df_user", password="pass")
        cls.backend = Domain.objects.create(user=cls.user, name="Backend")
        cls.design = Domain.objects.create(user=cls.user, name="Design")

        edu_b = Education.objects.create(
            user=cls.user, institution="TU", started=date(2015, 1, 1)
        )
        edu_b.domains.add(cls.backend)
        edu_d = Education.objects.create(
            user=cls.user, institution="Arts", started=date(2016, 1, 1)
        )
        edu_d.domains.add(cls.design)

        cert_b = Certification.objects.create(
            user=cls.user, name="AWS", issuer="Amazon"
        )
        cert_b.domains.add(cls.backend)
        cert_d = Certification.objects.create(
            user=cls.user, name="Figma", issuer="Figma"
        )
        cert_d.domains.add(cls.design)

    def setUp(self):
        self.client.force_login(self.user)

    def test_education_filtered_by_domain(self):
        r = self.client.get(f"/api/jac/education/?domains={self.backend.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([e["institution"] for e in r.data["results"]], ["TU"])

    def test_certification_filtered_by_domain(self):
        r = self.client.get(f"/api/jac/certifications/?domains={self.backend.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c["name"] for c in r.data["results"]], ["AWS"])


class FavouriteAPITests(APITestCase):
    """The favourite flag at the API layer: FavouriteLimitMixin enforces the
    per-type cap (Job limit = 4), and `ordering=-favourite` floats pinned rows
    to the top (the table star sort)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="favapi", password="pass")

    def setUp(self):
        self.client.force_login(self.user)

    def _job(self, title, *, started, favourite=False):
        return Job.objects.create(
            user=self.user,
            title=title,
            company="Acme",
            started=started,
            favourite=favourite,
        )

    def _fill_favourite_limit(self):
        for i in range(4):
            self._job(f"J{i}", started=date(2022, 1, 1), favourite=True)

    def test_create_over_limit_rejected(self):
        self._fill_favourite_limit()
        r = self.client.post(
            "/api/jac/jobs/",
            {
                "title": "Over",
                "company": "Acme",
                "started": "2022-01-01",
                "favourite": True,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("favourite", r.data)

    def test_create_non_favourite_allowed(self):
        self._fill_favourite_limit()
        r = self.client.post(
            "/api/jac/jobs/",
            {
                "title": "Plain",
                "company": "Acme",
                "started": "2022-01-01",
                "favourite": False,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)

    def test_favourites_first_in_ordering(self):
        self._job("Plain", started=date(2024, 1, 1))
        self._job("Pinned", started=date(2019, 1, 1), favourite=True)
        r = self.client.get("/api/jac/jobs/?ordering=-favourite,-started")
        self.assertEqual(r.status_code, 200)
        titles = [row["title"] for row in r.data["results"]]
        # Pinned floats above the more-recent Plain job despite the -started secondary.
        self.assertEqual(titles[0], "Pinned")
