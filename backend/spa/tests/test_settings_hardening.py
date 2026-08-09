"""Settings-hardening helpers + the safe DRF default + the `--skip` test runner.

Red until `[backend]-settings-hardening` lands `lukehirsch/prod.py` (import error here) and flips
the DRF default permission to IsAuthenticated. Housed in `spa` because it's the closest thing to a
site/core app; `lukehirsch` is the project package and its own tests would never be collected.
(Same reason `SkipGroupRunnerTests` lives down here rather than next to the runner.)

Deny-by-default does NOT mean the site is private — the portfolio is public. It means public
endpoints declare AllowAny explicitly instead of being public by omission.
"""

import argparse
import io
import logging
import unittest
from contextlib import contextmanager, redirect_stderr
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from lukehirsch.prod import (
    DEV_ENCRYPTION_KEY,
    DEV_SECRET_KEY,
    env_bool,
    env_int,
    env_list,
    verify_production_secrets,
)
from lukehirsch.test_runner import SKIP_GROUPS, SkipGroupRunner

# The production settings module, imported directly: the test run itself is on
# lukehirsch.test_settings, so `django.conf.settings` cannot answer "what would production
# hash with?" — only this can.
from lukehirsch import settings as production_settings

User = get_user_model()

ARGON2 = "django.contrib.auth.hashers.Argon2PasswordHasher"
MD5 = "django.contrib.auth.hashers.MD5PasswordHasher"


@contextmanager
def _muted():
    """Silence logging AND stderr inside the block. Wrap ONLY the anonymous schema fetch:
    generating the OpenAPI schema introspects every user-scoped viewset with an AnonymousUser,
    and drf-spectacular emits one warning per viewset it can't introspect — printed straight to
    sys.stderr by its generator-stats summary, bypassing logging. Expected noise for this one
    deliberate request; output anywhere else still surfaces."""
    logging.disable(logging.CRITICAL)
    try:
        with redirect_stderr(io.StringIO()):
            yield
    finally:
        logging.disable(logging.NOTSET)


class EnvBoolTests(TestCase):
    def test_string_false_is_false(self):
        # The headline regression: os.getenv returns "False" (truthy) — env_bool must not.
        for raw in ("False", "false", "0", "no", "off", ""):
            with mock.patch.dict("os.environ", {"FLAG": raw}):
                self.assertIs(env_bool("FLAG", True), False, raw)

    def test_string_true_is_true(self):
        for raw in ("True", "true", "1", "yes", "on"):
            with mock.patch.dict("os.environ", {"FLAG": raw}):
                self.assertIs(env_bool("FLAG", False), True, raw)

    def test_missing_returns_default(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIs(env_bool("NOPE", True), True)
            self.assertIs(env_bool("NOPE", False), False)

    def test_unrecognised_returns_default(self):
        with mock.patch.dict("os.environ", {"FLAG": "banana"}):
            self.assertIs(env_bool("FLAG", True), True)


class EnvIntTests(TestCase):
    def test_parses_int(self):
        with mock.patch.dict("os.environ", {"N": "300"}):
            self.assertEqual(env_int("N", 5), 300)

    def test_missing_returns_default(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(env_int("N", 42), 42)

    def test_non_integer_returns_default(self):
        with mock.patch.dict("os.environ", {"N": "later"}):
            self.assertEqual(env_int("N", 7), 7)


class EnvListTests(TestCase):
    def test_parses_comma_separated_and_strips(self):
        with mock.patch.dict("os.environ", {"L": "a, b ,,c"}):
            self.assertEqual(env_list("L", []), ["a", "b", "c"])

    def test_missing_returns_default(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(env_list("NOPE", ["x"]), ["x"])

    def test_empty_value_is_explicit_no_entries(self):
        # A set-but-blank var means "clear the list", not "fall back to default".
        with mock.patch.dict("os.environ", {"L": "  ,, "}):
            self.assertEqual(env_list("L", ["x"]), [])


class VerifyProductionSecretsTests(TestCase):
    def test_raises_when_debug_off_on_dev_secret(self):
        with self.assertRaises(ImproperlyConfigured):
            verify_production_secrets(
                debug=False,
                secret_key=DEV_SECRET_KEY,
                encryption_key="a-real-key",
            )
        with self.assertRaises(ImproperlyConfigured):
            verify_production_secrets(
                debug=False,
                secret_key="a-real-key",
                encryption_key=DEV_ENCRYPTION_KEY,
            )

    def test_silent_in_debug(self):
        # Local dev is allowed to run on the placeholders.
        verify_production_secrets(
            debug=True, secret_key=DEV_SECRET_KEY, encryption_key=DEV_ENCRYPTION_KEY
        )

    def test_silent_when_overridden(self):
        verify_production_secrets(
            debug=False, secret_key="real-a", encryption_key="real-b"
        )


class DrfDefaultPermissionTests(TestCase):
    def test_default_permission_is_authenticated(self):
        self.assertEqual(
            settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"],
            ["rest_framework.permissions.IsAuthenticated"],
        )

    def test_index_stays_public(self):
        # The portfolio is a PUBLIC site — deny-by-default must not lock out anonymous
        # visitors from the root. `/` is now the Django-rendered landing (HTML, SEO
        # front door); the JSON liveness check moved to `/health/`.
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r["Content-Type"])
        h = self.client.get("/health/")
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json(), {"message": "I am alive!"})

    def test_schema_stays_public(self):
        # drf-spectacular serves the schema under its own SERVE_PERMISSIONS default
        # (AllowAny) — deliberate: the API docs are part of the public showcase.
        with _muted():
            r = self.client.get("/api/schema/")
        self.assertEqual(r.status_code, 200)


def _flatten(suite):
    """Yield the individual TestCase instances out of a (nested) TestSuite."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


class SkipGroupRunnerTests(TestCase):
    """`manage.py test --skip <group>`: everything runs unless you opt out."""

    def _parse(self, argv):
        parser = argparse.ArgumentParser()
        SkipGroupRunner.add_arguments(parser)
        return parser.parse_args(argv)

    def test_the_project_uses_the_skip_group_runner(self):
        # Without this the flag doesn't exist at all.
        self.assertEqual(settings.TEST_RUNNER, "lukehirsch.test_runner.SkipGroupRunner")

    def test_a_group_becomes_an_excluded_tag(self):
        runner = SkipGroupRunner(skip_groups=[["llm"]], verbosity=0, interactive=False)
        self.assertIn("llm", runner.exclude_tags)

    def test_space_separated_groups_all_land(self):
        args = self._parse(["--skip", "llm", "auth"])
        runner = SkipGroupRunner(**vars(args), verbosity=0, interactive=False)
        self.assertEqual(runner.exclude_tags, {"llm", "auth"})

    def test_the_flag_is_repeatable(self):
        # action="append" + nargs="+" gives a list of lists; both spellings must work.
        args = self._parse(["--skip", "llm", "--skip", "auth"])
        runner = SkipGroupRunner(**vars(args), verbosity=0, interactive=False)
        self.assertEqual(runner.exclude_tags, {"llm", "auth"})

    def test_no_skip_excludes_nothing(self):
        runner = SkipGroupRunner(verbosity=0, interactive=False)
        self.assertEqual(runner.exclude_tags, set())

    def test_skip_adds_to_exclude_tag_rather_than_replacing_it(self):
        runner = SkipGroupRunner(
            skip_groups=[["llm"]],
            exclude_tags=["something-else"],
            verbosity=0,
            interactive=False,
        )
        self.assertEqual(runner.exclude_tags, {"llm", "something-else"})

    def test_an_unknown_group_is_refused_with_the_known_list(self):
        # A silently-ignored typo would quietly run the 237s suite you meant to skip.
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as err:
            self._parse(["--skip", "lmm"])
        for name in SKIP_GROUPS:
            self.assertIn(name, err.getvalue())

    def test_every_group_actually_tags_something(self):
        """The drift guard: a group whose tag is on no test is a flag that does nothing.

        Deliberately one-directional — a stray `@tag("wip")` that is not a skip group is
        allowed (it still works with Django's own `--exclude-tag`), so local ad-hoc tags
        never turn the suite red."""
        suite = SkipGroupRunner(verbosity=0, interactive=False).build_suite([])
        tagged = set()
        for test in _flatten(suite):
            tagged |= set(getattr(test, "tags", ()))
        for name in SKIP_GROUPS:
            self.assertIn(name, tagged, f"skip group {name!r} tags no test")

    def test_tests_run_on_the_fast_hasher(self):
        """The whole point of lukehirsch.test_settings. Must hold under `--parallel` too:
        spawned workers re-import settings from scratch, so if this ever passes serially
        and fails in parallel, the mechanism (not the assertion) is wrong."""
        self.assertEqual(settings.PASSWORD_HASHERS, [MD5])

    def test_the_llm_tag_is_inherited_by_every_live_prompt_class(self):
        # One decorator on the base class has to cover the whole module, or --skip llm
        # leaves most of the 237s on the table.
        from jac.tests import test_prompts

        classes = [
            obj
            for obj in vars(test_prompts).values()
            if isinstance(obj, type)
            and issubclass(obj, TestCase)
            and obj is not test_prompts.LivePromptTestCase
            and issubclass(obj, test_prompts.LivePromptTestCase)
        ]
        self.assertTrue(classes, "no LivePromptTestCase subclasses found")
        for cls in classes:
            self.assertIn("llm", getattr(cls, "tags", set()), cls.__name__)


class ArgonProductionHasherTests(TestCase):
    """Real password storage is Argon2. Asserted against the production settings module,
    because the test run deliberately swaps the hasher out (see test_settings.py)."""

    PRODUCTION_HASHERS = production_settings.PASSWORD_HASHERS

    def test_production_hashes_with_argon2(self):
        self.assertEqual(self.PRODUCTION_HASHERS[0], ARGON2)

    def test_the_argon2_library_is_actually_installed(self):
        """The failure this exists for: `Django[argon2]` missing on a server makes every
        set_password/check_password raise, i.e. nobody can log in or sign up. Fails here
        rather than in production — the swapped-in test hasher would otherwise hide it.

        Hashing under the production list is the assertion: `get_hasher("argon2")` would
        only prove the algorithm is *configured*, and it resolves against whatever list is
        active, which during a test run is MD5."""
        with override_settings(PASSWORD_HASHERS=self.PRODUCTION_HASHERS):
            encoded = make_password("correct horse")
        self.assertTrue(encoded.startswith("argon2$"), encoded[:20])

    def test_the_test_hasher_is_never_what_production_uses(self):
        self.assertNotIn(MD5, self.PRODUCTION_HASHERS)

    def test_old_pbkdf2_hashes_still_verify_and_upgrade_on_login(self):
        """Deploy-day property: existing rows are pbkdf2_sha256. They must keep working,
        and Django rewrites each one to argon2 on that user's next successful login —
        which is why the legacy hashers stay listed behind Argon2 rather than deleted."""
        with override_settings(
            PASSWORD_HASHERS=["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
        ):
            legacy = make_password("correct horse")
        self.assertTrue(legacy.startswith("pbkdf2_sha256$"), legacy[:20])

        user = User.objects.create(username="legacy", email="legacy@example.com")
        User.objects.filter(pk=user.pk).update(password=legacy)
        user.refresh_from_db()

        with override_settings(PASSWORD_HASHERS=self.PRODUCTION_HASHERS):
            self.assertTrue(user.check_password("correct horse"))
            self.assertFalse(user.check_password("wrong horse"))
            user.refresh_from_db()

        self.assertTrue(user.password.startswith("argon2"), user.password[:20])
