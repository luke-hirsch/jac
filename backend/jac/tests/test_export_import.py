"""cv_export / cv_import round-trip."""

import io
import json
import os
import tempfile
from datetime import date

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from jac.models import (
    Certification,
    Domain,
    Education,
    Job,
    Language,
    Location,
    Project,
    ResumeSnippet,
    Skill,
)


class CvExportImportRoundTripTests(TestCase):
    """`cv_export` of user A, imported into a fresh user B, reproduces the CV —
    including related_skills symmetry, the years override, certification +
    location references, resume snippets, and per-user domain scoping.
    """

    @classmethod
    def setUpTestData(cls):
        cls.a = User.objects.create_user(username="rt_a", password="pass")
        cls.b = User.objects.create_user(username="rt_b", password="pass")

        domain = Domain.objects.create(user=cls.a, name="Backend")
        loc = Location.objects.create(user=cls.a, city="Berlin", country="DE")
        cert = Certification.objects.create(
            user=cls.a, name="AWS SAA", issuer="Amazon", issued_on=date(2022, 5, 1)
        )

        py = Skill.objects.create(
            user=cls.a,
            name="Python",
            proficiency="expert",
            category="technical",
            years_of_experience_override=5,
            certification=cert,
        )
        sev = Skill.objects.create(user=cls.a, name="SevDesk")
        py.related_skills.add(sev)
        py.domains.add(domain)

        # Directed prerequisite chain: DRF builds on Django builds on Python.
        django = Skill.objects.create(user=cls.a, name="Django")
        drf = Skill.objects.create(user=cls.a, name="DRF")
        django.builds_on.add(py)
        drf.builds_on.add(django)

        # Certification evidences a skill + sits in a domain.
        cert.skills.add(py)
        cert.domains.add(domain)

        job = Job.objects.create(
            user=cls.a,
            title="Engineer",
            company="Co",
            location=loc,
            started=date(2021, 1, 1),
        )
        job.skills.add(py)
        job.domains.add(domain)

        # Education with its own skills + domains.
        edu = Education.objects.create(
            user=cls.a,
            institution="TU Berlin",
            field_of_study="CS",
            started=date(2015, 10, 1),
        )
        edu.skills.add(py)
        edu.domains.add(domain)

        # Project tied to the job it was built at.
        project = Project.objects.create(
            user=cls.a, name="Pipeline", started=date(2021, 3, 1), job=job
        )

        Language.objects.create(user=cls.a, name="German", fluency="native")

        snippet = ResumeSnippet.objects.create(
            user=cls.a,
            title="Intro",
            content="Hi.",
            kind="intro",
            job=job,
            project=project,
        )
        snippet.skills.add(py)
        snippet.domains.add(domain)

    def _round_trip(self):
        """Export A to a temp file and import it into B."""
        buf = io.StringIO()
        call_command("cv_export", "--user", str(self.a.pk), stdout=buf)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(buf.getvalue())
            path = fh.name
        try:
            call_command(
                "cv_import", "--username", "rt_b", "--file", path, stdout=io.StringIO()
            )
        finally:
            os.remove(path)

    def test_skill_fields_and_relation_survive(self):
        self._round_trip()
        py = Skill.objects.get(user=self.b, name="Python")
        self.assertEqual(py.proficiency, "expert")
        self.assertEqual(py.years_of_experience_override, 5)
        self.assertEqual(py.certification.name, "AWS SAA")
        # symmetric relation resolved by name
        self.assertIn("SevDesk", py.related_skills.values_list("name", flat=True))
        sev = Skill.objects.get(user=self.b, name="SevDesk")
        self.assertIn("Python", sev.related_skills.values_list("name", flat=True))

    def test_builds_on_direction_survives(self):
        self._round_trip()
        drf = Skill.objects.get(user=self.b, name="DRF")
        django = Skill.objects.get(user=self.b, name="Django")
        py = Skill.objects.get(user=self.b, name="Python")
        # Forward edges preserved …
        self.assertIn("Django", drf.builds_on.values_list("name", flat=True))
        self.assertIn("Python", django.builds_on.values_list("name", flat=True))
        # … and the direction does NOT leak back (asymmetry holds).
        self.assertNotIn("DRF", django.builds_on.values_list("name", flat=True))
        self.assertEqual(py.builds_on.count(), 0)
        self.assertIn("Django", py.enables.values_list("name", flat=True))

    def test_education_certification_project_relations_survive(self):
        self._round_trip()
        edu = Education.objects.get(user=self.b, institution="TU Berlin")
        self.assertIn("Python", edu.skills.values_list("name", flat=True))
        self.assertIn("Backend", edu.domains.values_list("name", flat=True))

        cert = Certification.objects.get(user=self.b, name="AWS SAA")
        self.assertIn("Python", cert.skills.values_list("name", flat=True))
        self.assertIn("Backend", cert.domains.values_list("name", flat=True))

        project = Project.objects.get(user=self.b, name="Pipeline")
        self.assertEqual(project.job.title, "Engineer")
        self.assertEqual(project.job.user, self.b)

        snippet = ResumeSnippet.objects.get(user=self.b, title="Intro")
        self.assertEqual(snippet.job.title, "Engineer")
        self.assertEqual(snippet.project.name, "Pipeline")

    def test_domains_are_scoped_to_importing_user(self):
        self._round_trip()
        py = Skill.objects.get(user=self.b, name="Python")
        b_domain = Domain.objects.get(user=self.b, name="Backend")
        self.assertIn(b_domain, py.domains.all())
        # A's original domain is untouched / not cross-linked to B.
        self.assertEqual(Domain.objects.filter(name="Backend").count(), 2)
        self.assertEqual(Domain.objects.filter(user=self.a, name="Backend").count(), 1)

    def test_job_location_and_snippet_round_trip(self):
        self._round_trip()
        job = Job.objects.get(user=self.b, title="Engineer")
        self.assertEqual(job.location.city, "Berlin")
        self.assertEqual(job.location.user, self.b)
        self.assertIn("Python", job.skills.values_list("name", flat=True))

        snippet = ResumeSnippet.objects.get(user=self.b, title="Intro")
        self.assertEqual(snippet.kind, "intro")
        self.assertIn("Python", snippet.skills.values_list("name", flat=True))
        self.assertIn("Backend", snippet.domains.values_list("name", flat=True))

    def test_computed_years_not_exported(self):
        buf = io.StringIO()
        call_command("cv_export", "--user", str(self.a.pk), stdout=buf)
        # The computed read-only property must not leak into the dump.
        self.assertNotIn('years_of_experience"', buf.getvalue())
        self.assertIn("years_of_experience_override", buf.getvalue())

    def test_system_default_domain_reused_not_duplicated(self):
        # A tags a skill with a shared system-default domain.
        system = User.objects.create_user(username=settings.SYSTEM_USER_USERNAME)
        sysdom = Domain.objects.create(user=system, name="finance")
        Skill.objects.get(user=self.a, name="Python").domains.add(sysdom)

        buf = io.StringIO()
        call_command("cv_export", "--user", str(self.a.pk), stdout=buf)
        dump = buf.getvalue()
        data = json.loads(dump)
        # The shared default is NOT written as one of A's own domains …
        self.assertNotIn("finance", [d["name"] for d in data["domains"]])
        # … but the skill still references it by name.
        py_dump = next(s for s in data["skills"] if s["name"] == "Python")
        self.assertIn("finance", py_dump["domains"])

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(dump)
            path = fh.name
        try:
            call_command(
                "cv_import", "--username", "rt_b", "--file", path, stdout=io.StringIO()
            )
        finally:
            os.remove(path)

        # Import reused the existing system default — no user-owned duplicate.
        self.assertEqual(Domain.objects.filter(name="finance").count(), 1)
        self.assertFalse(Domain.objects.filter(user=self.b, name="finance").exists())
        b_py = Skill.objects.get(user=self.b, name="Python")
        self.assertIn(sysdom, b_py.domains.all())
