"""The skill's script (jac/resources/skill/scripts/jac.py) in file mode, the Claude app's, whose
sandbox can't reach jac: the career comes from the file the user attached, and `save` / `apply`
check what Claude wrote the way jac's import will. The acceptance test is the round trip: what the
script hands over, jac's import takes."""

import importlib.util
import io
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from rest_framework.test import APITestCase

from jac.models import Education, Job, Skill
from jac.skill import SKILL_DIR

from ._helpers import make_user

SKILL_URL = "/api/jac/skill/"


def _load_script():
    """A fresh copy per test: the script caches the career file it found."""
    spec = importlib.util.spec_from_file_location(
        "jac_skill_script", SKILL_DIR / "scripts" / "jac.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillScriptFileModeTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.python = Skill.objects.create(user=cls.user, name="Python")
        cls.job = Job.objects.create(
            user=cls.user,
            title="Backend Dev",
            company="Acme",
            started="2020-01-01",
            description="Built APIs.",
        )
        cls.job.skills.add(cls.python)
        cls.master = Education.objects.create(
            user=cls.user,
            institution="FU Berlin",
            started="2012-10-01",
            ended="2017-09-30",
            degree_level=Education.DegreeLevel.master,
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.script = _load_script()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        # The Claude app's folders, here in a temp dir.
        self.script.USER_DATA = self.root / "user-data"
        self.script.OUTPUTS = self.script.USER_DATA / "outputs"
        self.uploads = self.script.USER_DATA / "uploads"
        self.uploads.mkdir(parents=True)
        self.script.OUTPUTS.mkdir()
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in ("JAC_URL", "JAC_TOKEN", "JAC_CAREER_FILE"):
            os.environ.pop(key, None)
        # The file exactly as Account → Claude → Download career for Claude serves it. The app
        # may rename an upload: the script goes by the file's marker, not its name.
        self.career = json.loads(self.client.get(f"{SKILL_URL}career/file/").content)
        (self.uploads / "career (1).json").write_text(json.dumps(self.career))

    def _run(self, *argv) -> tuple[int, str, str]:
        self.script._career = None  # each command is a process of its own in the sandbox
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = self.script.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def _write(self, name, data) -> Path:
        path = self.root / name
        path.write_text(json.dumps(data))
        return path

    def _application(self, **kw):
        return {
            "posting": {
                "text": "Wir suchen eine:n Backend-Entwickler:in.",
                "title": "Backend-Entwickler:in",
                "language": "de",
            },
            "entries": [
                f"job:{self.job.pk}",
                f"skill:{self.python.pk}",
                f"education:{self.master.pk}",
            ],
            "letter": {"body": " ".join(["Wort"] * 200), "tone": "neutral"},
            **kw,
        }

    def _seen(self, entry_id):
        rows = [e for s in self.career["entries"].values() for e in s]
        return next(e["updated_at"] for e in rows if e["id"] == entry_id)

    # ---- reading --------------------------------------------------------------------------

    def test_career_reads_the_attached_file(self):
        code, out, err = self._run("career")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["entries"]["jobs"][0]["id"], f"job:{self.job.pk}")
        self.assertNotIn("review", data)
        self.assertNotIn("format", data)
        self.assertIn("FILE MODE", err)
        self.assertIn("career (1).json", err)

    def test_review_reads_the_same_file(self):
        code, out, _ = self._run("review")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), self.career["review"])

    def test_other_json_files_are_passed_over(self):
        (self.uploads / "notes.json").write_text(
            json.dumps({"format": "something else"})
        )
        (self.uploads / "broken.json").write_text("{not json")
        code, _, err = self._run("career")
        self.assertEqual(code, 0, err)
        self.assertIn("career (1).json", err)

    def test_without_a_career_file_it_asks_for_one(self):
        (self.uploads / "career (1).json").unlink()
        code, out, err = self._run("career")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("Download career for Claude", err)

    def test_jac_career_file_names_it_outright(self):
        elsewhere = self.root / "elsewhere.json"
        (self.uploads / "career (1).json").rename(elsewhere)
        os.environ["JAC_CAREER_FILE"] = str(elsewhere)
        self.assertEqual(self._run("career")[0], 0)
        os.environ["JAC_CAREER_FILE"] = str(self.root / "missing.json")
        code, _, err = self._run("career")
        self.assertEqual(code, 1)
        self.assertIn("isn't a career file", err)

    # ---- save -----------------------------------------------------------------------------

    def test_save_hands_over_what_the_import_takes(self):
        path = self._write("application.json", self._application())
        code, out, err = self._run("save", str(path))
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        handed = self.script.OUTPUTS / "application.json"
        self.assertEqual(result["file"], str(handed))
        self.assertIn("Import from Claude", result["import"])
        self.assertEqual(result["warnings"], [])
        # Applications → Import from Claude posts the file as it is.
        r = self.client.post(
            f"{SKILL_URL}applications/", json.loads(handed.read_text()), format="json"
        )
        self.assertEqual(r.status_code, 201, r.data)

    def test_save_refuses_an_id_the_career_file_lacks(self):
        app = self._application()
        app["entries"].append("job:99999")
        code, _, err = self._run("save", str(self._write("application.json", app)))
        self.assertEqual(code, 1)
        self.assertIn("job:99999", err)
        self.assertEqual(list(self.script.OUTPUTS.iterdir()), [])

    def test_save_refuses_what_the_import_requires(self):
        app = self._application(letter={"body": ""})
        del app["posting"]["text"]
        code, _, err = self._run("save", str(self._write("application.json", app)))
        self.assertEqual(code, 1)
        self.assertIn("posting.text", err)
        self.assertIn("letter.body", err)

    def test_save_warns_as_jac_would(self):
        app = self._application(letter={"body": " ".join(["Wort"] * 50)})
        app["entries"] = [f"job:{self.job.pk}"]
        code, out, _ = self._run("save", str(self._write("application.json", app)))
        self.assertEqual(code, 0)
        warnings = " ".join(json.loads(out)["warnings"])
        self.assertIn("50 words", warnings)
        self.assertIn(
            f"education:{self.master.pk}", warnings
        )  # the highest degree, added

    # ---- apply ----------------------------------------------------------------------------

    def _batch(self, **job_change):
        job_id = f"job:{self.job.pk}"
        return {
            "changes": [
                {
                    "id": job_id,
                    "seen": self._seen(job_id),
                    "why": "Says what was built.",
                    "set": {"description": "- Built the billing API for 3 teams."},
                    "add": {"skills": ["new:docker"]},
                    **job_change,
                },
                {
                    "new": "skill",
                    "ref": "docker",
                    "why": "Named in the job, missing as a skill.",
                    "set": {
                        "name": "Docker",
                        "category": "technical",
                        "proficiency": "intermediate",
                    },
                },
            ]
        }

    def test_apply_hands_over_a_batch_the_import_takes(self):
        path = self._write("career-changes.json", self._batch())
        code, out, err = self._run("apply", str(path))
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["changes"], 2)
        self.assertIn("Import changes", result["import"])
        # Account → Claude → Import changes posts the file's changes.
        handed = json.loads(Path(result["file"]).read_text())
        r = self.client.post(
            f"{SKILL_URL}career/changes/", {"changes": handed["changes"]}, format="json"
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.job.refresh_from_db()
        self.assertIn("billing API", self.job.description)

    def test_apply_check_hands_nothing_over(self):
        path = self._write("career-changes.json", self._batch())
        code, out, _ = self._run("apply", str(path), "--check")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["checked_only"])
        self.assertEqual(list(self.script.OUTPUTS.iterdir()), [])

    def test_apply_refuses_a_stale_seen(self):
        batch = self._batch(seen="2020-01-01T00:00:00+00:00")
        code, _, err = self._run("apply", str(self._write("c.json", batch)))
        self.assertEqual(code, 1)
        self.assertIn("seen", err)

    def test_apply_refuses_what_the_batch_cant_resolve(self):
        batch = self._batch(add={"skills": ["new:kubernetes", "skill:99999"]})
        batch["changes"].append(dict(batch["changes"][0], add={}))  # the same job twice
        code, _, err = self._run("apply", str(self._write("c.json", batch)))
        self.assertEqual(code, 1)
        self.assertIn("kubernetes", err)
        self.assertIn("skill:99999", err)
        self.assertIn("a second change", err)

    # ---- what needs jac -------------------------------------------------------------------

    def test_online_only_commands_say_where_in_jac(self):
        for argv in (["applications"], ["show", "1"], ["changes"], ["undo", "1"]):
            with self.subTest(argv=argv):
                code, _, err = self._run(*argv)
                self.assertEqual(code, 2)
                self.assertIn("NOT HERE", err)
                self.assertIn("In jac:", err)

    def test_with_jac_url_and_token_it_goes_online(self):
        os.environ["JAC_URL"] = "http://127.0.0.1:9"  # nothing listens there
        os.environ["JAC_TOKEN"] = "jac_test"
        code, out, err = self._run("career")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("can't be reached", err)
