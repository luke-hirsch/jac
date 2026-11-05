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
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django.utils.log import RequireDebugTrue
from kombu.exceptions import OperationalError
from rest_framework.exceptions import ParseError
from rest_framework.request import Request
from rest_framework.throttling import BaseThrottle

from jac.models import DailyGenerationCount
from llm_connector.models import AiJob, LLMRequestLog

# The production settings module, imported directly: the test run itself is on
# lukehirsch.test_settings, so `django.conf.settings` cannot answer "what would production
# hash with?" — only this can.
from lukehirsch import settings as production_settings
from lukehirsch.mail import QueuedEmailBackend, message_to_dict
from lukehirsch.parsers import ObjectJSONParser
from lukehirsch.prod import (
    DEV_ENCRYPTION_KEY,
    DEV_SECRET_KEY,
    env_bool,
    env_int,
    env_list,
    load_local_env,
    verify_production_secrets,
)
from lukehirsch.test_runner import SKIP_GROUPS, SkipGroupRunner
from spa.tasks import housekeeping, send_email

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


# ── prod-readiness (guide 03) ─────────────────────────────────────────────────────
# Red until `03_[backend]-prod-readiness` lands. The subprocess tests boot the real settings
# module with DEBUG off — the one thing the test run itself (DEBUG on, test_settings) can't show.

BACKEND_DIR = Path(settings.BASE_DIR)
REPO_ROOT = BACKEND_DIR.parent

# Non-dev secrets, so verify_production_secrets lets the boot through. Not real keys.
PROD_ENV = {
    "DEBUG": "0",
    "SECRET_KEY": "q8Vn2Lk7Zr4Xt1Bw9Mc6Yp3Hf5Jd0Sg8Ae2Ui4Ko6Nm1Wl3Er5Ty7",
    "LLM_ENCRYPTION_KEY": "dGVzdC1rZXktbm90LXRoZS1kZXYta2V5LTEyMzQ1Njc=",
    "BASE_DOMAIN": "example.com",
    # Never opened: settings only parse it. %40 is an encoded @ in the password.
    "DATABASE_URL": "postgres://jac:not%40real@db.example.com:5433/jacprod",
    "DJANGO_SETTINGS_MODULE": "lukehirsch.settings",
}


def _python(*args, env=None):
    """Run the venv's python in backend/ and return the CompletedProcess (never raises)."""
    return subprocess.run(
        [sys.executable, *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _prod_env():
    return {**os.environ, **PROD_ENV}


class ProdBootTests(TestCase):
    """The production settings boot and pass Django's own deploy checklist."""

    def test_check_deploy_is_clean_under_a_prod_env(self):
        r = _python(
            "manage.py", "check", "--deploy", "--fail-level", "WARNING", env=_prod_env()
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_prod_env_yields_the_prod_values(self):
        probe = (
            "import json, django; django.setup(); from django.conf import settings as s; "
            "db = s.DATABASES['default']; "
            "print(json.dumps({"
            "'bodies': s.LLM_LOG_BODIES, 'accel': s.PRIVATE_MEDIA_ACCEL, "
            "'redirect': s.SECURE_SSL_REDIRECT, 'hsts': s.SECURE_HSTS_SECONDS, "
            "'static_root': str(s.STATIC_ROOT or ''), "
            "'db': [db['ENGINE'], db['NAME'], db['USER'], db['PASSWORD'], db['HOST'], "
            "int(db['PORT'] or 0)]}))"
        )
        r = _python("-c", probe, env=_prod_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        values = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIs(
            values["bodies"], False, "prod must not store prompt/response bodies"
        )
        self.assertIs(values["accel"], True, "prod hands private files to nginx")
        self.assertIs(values["redirect"], True)
        self.assertGreater(values["hsts"], 0)
        self.assertTrue(values["static_root"])
        self.assertEqual(
            values["db"],
            [
                "django.db.backends.postgresql",
                "jacprod",
                "jac",
                "not@real",
                "db.example.com",
                5433,
            ],
        )

    def test_prod_refuses_to_boot_without_a_database_url(self):
        """It would otherwise try a laptop's local socket and fail on the first query, far from
        the cause. The loader is patched out: `.env.local` is a laptop's file."""
        env = {k: v for k, v in _prod_env().items() if k != "DATABASE_URL"}
        probe = (
            "import lukehirsch.prod as p; p.load_local_env = lambda path: None; "
            "import django; django.setup()"
        )
        r = _python("-c", probe, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DATABASE_URL", r.stderr)

    def test_an_empty_database_url_counts_as_unset(self):
        """`.env.example` ships `DATABASE_URL=` empty, and that reaches the process as ''."""
        probe = (
            "import lukehirsch.prod as p; p.load_local_env = lambda path: None; "
            "import django; django.setup()"
        )
        r = _python("-c", probe, env={**_prod_env(), "DATABASE_URL": ""})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DATABASE_URL", r.stderr)

    def test_a_process_with_no_debug_variable_runs_in_production_mode(self):
        """Off unless something turns it on, so a forgotten variable can't put a public site in
        debug mode. `.env.local` is a laptop's opt-in, so the probe patches its loader out; the
        image never carries that file."""
        env = {k: v for k, v in _prod_env().items() if k != "DEBUG"}
        probe = (
            "import lukehirsch.prod as p; p.load_local_env = lambda path: None; "
            "import django; django.setup(); from django.conf import settings as s; "
            "print(s.DEBUG)"
        )
        r = _python("-c", probe, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(r.stdout.strip().splitlines()[-1], "False")

    def test_prod_refuses_to_boot_without_a_base_domain(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "BASE_DOMAIN"):
            verify_production_secrets(
                debug=False,
                secret_key="real-a",
                encryption_key="real-b",
                base_domain="localhost",
            )

    def test_dev_defaults_keep_bodies_and_stream_private_files_in_django(self):
        # test_settings sets DEBUG=1 before importing settings: the dev side of both switches.
        self.assertIs(production_settings.LLM_LOG_BODIES, True)
        self.assertIs(production_settings.PRIVATE_MEDIA_ACCEL, False)

    def test_asgi_imports_without_a_settings_module_in_the_env(self):
        """daphne in the container sets no DJANGO_SETTINGS_MODULE; asgi.py must, before setup."""
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        env["DEBUG"] = "1"  # the dev secrets are fine here; the prod guard has its own test
        r = _python("-c", "import lukehirsch.asgi", env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])


class PostgresTests(TestCase):
    """Postgres in dev, the suite, CI and prod (guide 03): one engine, so what the suite passes is
    what prod runs."""

    def test_the_suite_runs_on_postgres(self):
        self.assertEqual(connection.vendor, "postgresql")

    def test_connections_are_not_kept_open(self):
        # Django's docs rule persistent connections out under ASGI: daphne would leak them.
        self.assertEqual(production_settings.DATABASES["default"]["CONN_MAX_AGE"], 0)

    def test_the_migrations_match_the_models(self):
        call_command("makemigrations", check=True, dry_run=True, verbosity=0)


def _lock_requirements():
    """Logical requirement lines of requirements.txt (backslash continuations joined, comments
    and blanks dropped)."""
    text = (REPO_ROOT / "requirements.txt").read_text()
    logical, current = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not current and (not line or line.startswith("#")):
            continue
        current += " " + line.rstrip("\\").strip()
        if not line.endswith("\\"):
            logical.append(current.strip())
            current = ""
    return logical


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class RequirementsLockTests(TestCase):
    """requirements.txt is a pip-compile lock: reproducible, hash-checked installs."""

    def test_every_requirement_is_pinned_with_a_hash(self):
        lines = _lock_requirements()
        self.assertTrue(lines, "requirements.txt is empty")
        for line in lines:
            self.assertRegex(line, r"^[A-Za-z0-9][A-Za-z0-9._-]*==\S+", line[:80])
            self.assertIn("--hash=sha256:", line, line[:80])

    def test_the_production_drivers_are_locked(self):
        names = {_normalise(line.split("==", 1)[0]) for line in _lock_requirements()}
        for needed in (
            "channels-redis",
            "daphne",
            "dj-database-url",
            "django",
            "psycopg",
            "psycopg-binary",
        ):
            self.assertIn(needed, names)

    def test_the_production_drivers_import(self):
        """Same insurance as ArgonProductionHasherTests: CHANNEL_LAYERS names the layer as a
        string, so a missing package only shows up when the first run publishes an event."""
        import channels_redis.pubsub  # noqa: F401
        import psycopg  # noqa: F401


class FileRootsTests(TestCase):
    def test_static_root_is_set(self):
        # collectstatic refuses to run without it.
        self.assertTrue(settings.STATIC_ROOT)

    def test_private_and_public_media_roots_do_not_nest(self):
        """nginx aliases MEDIA_ROOT publicly; private files must not be reachable under it."""
        public = Path(settings.MEDIA_ROOT).resolve()
        private = Path(settings.PRIVATE_MEDIA_ROOT).resolve()
        self.assertFalse(private.is_relative_to(public), (private, public))
        self.assertFalse(public.is_relative_to(private), (private, public))

    def test_private_url_is_the_nginx_internal_location(self):
        # The contract with guide 04's `location /_private/ { internal; … }`.
        self.assertEqual(settings.PRIVATE_MEDIA_URL, "/_private/")


class LocalEnvFileTests(TestCase):
    """backend/.env.local: how a laptop says DEBUG=1 now that unset means production."""

    def _load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.local"
            path.write_text(text)
            load_local_env(path)

    def test_exports_key_values_and_skips_comments(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self._load("# a comment\nJAC_A=1\n\nJAC_B = two\n#JAC_C=3\n")
            self.assertEqual(os.environ["JAC_A"], "1")
            self.assertEqual(os.environ["JAC_B"], "two")
            self.assertNotIn("JAC_C", os.environ)

    def test_the_real_environment_wins(self):
        with mock.patch.dict("os.environ", {"DEBUG": "0"}, clear=True):
            self._load("DEBUG=1\n")
            self.assertEqual(os.environ["DEBUG"], "0")

    def test_a_missing_file_is_fine(self):
        load_local_env(Path(tempfile.gettempdir()) / "jac-no-such-env-file")


class LoggingTests(TestCase):
    def test_request_errors_reach_a_stream_handler(self):
        """With DEBUG off, Django's default config only mails 500s to ADMINS (empty) — the
        tracebacks vanished. One must reach a plain stream handler (stdout → docker logs)."""
        logger = logging.getLogger("django.request")
        self.assertTrue(logger.isEnabledFor(logging.ERROR))
        handlers, current = [], logger
        while current:
            handlers += current.handlers
            if not current.propagate:
                break
            current = current.parent
        streams = [
            h
            for h in handlers
            if isinstance(h, logging.StreamHandler)
            and h.level <= logging.ERROR
            and not any(isinstance(f, RequireDebugTrue) for f in h.filters)
        ]
        self.assertTrue(streams, f"no ungated stream handler among {handlers}")


class ProxyTests(TestCase):
    """TLS ends at nginx; Django must believe X-Forwarded-Proto (nginx overwrites it)."""

    def test_forwarded_proto_marks_the_request_secure(self):
        request = RequestFactory().get("/", HTTP_X_FORWARDED_PROTO="https")
        self.assertTrue(request.is_secure())

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_health_is_exempt_from_the_ssl_redirect(self):
        # Compose / the box's uptime probe talk plain http to daphne.
        self.assertEqual(self.client.get("/health/").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 301)


class ClientIpTests(TestCase):
    def test_throttle_identity_ignores_a_spoofed_forwarded_for(self):
        """Behind nginx, daphne puts the real client in REMOTE_ADDR (from X-Real-IP). DRF must
        key throttles on that — not on an X-Forwarded-For the visitor can write themselves."""
        request = Request(
            RequestFactory().get(
                "/", HTTP_X_FORWARDED_FOR="6.6.6.6", REMOTE_ADDR="203.0.113.7"
            )
        )
        self.assertEqual(BaseThrottle().get_ident(request), "203.0.113.7")


_ENV_READ = re.compile(
    r"(?:os\.getenv|os\.environ\.get|env_bool|env_int|env_list)\(\s*\"([A-Z][A-Z0-9_]*)\""
)
_ENV_EXAMPLE_KEY = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=(.*)$")


def _example_entries():
    """{KEY: value} of .env.example, commented-out (optional) keys included."""
    entries = {}
    for line in (REPO_ROOT / ".env.example").read_text().splitlines():
        m = _ENV_EXAMPLE_KEY.match(line)
        if m:
            entries[m.group(1)] = m.group(2).strip()
    return entries


class EnvExampleTests(TestCase):
    """.env.example is the deploy's env template (compose `env_file`). It drifts the moment
    settings.py grows an os.getenv nobody documents — this is the tripwire."""

    def test_every_env_var_settings_reads_is_documented(self):
        source = (BACKEND_DIR / "lukehirsch" / "settings.py").read_text()
        read = set(_ENV_READ.findall(source))
        self.assertTrue(read, "regex found no env reads in settings.py — fix the test")
        missing = read - set(_example_entries())
        self.assertFalse(missing, f"add to .env.example: {sorted(missing)}")

    def test_the_example_carries_no_secret_values(self):
        entries = _example_entries()
        for key in (
            "SECRET_KEY",
            "LLM_ENCRYPTION_KEY",
            "EMAIL_HOST_PASSWORD",
            "DATABASE_URL",
        ):
            self.assertIn(key, entries)
            self.assertEqual(entries[key], "", f"{key} must stay empty in the example")


class ObjectJSONParserTests(SimpleTestCase):
    """Postgres can't store U+0000 in text or jsonb. A serializer's CharField refuses it already,
    but a JSONField takes anything, so a crafted body would reach the database and come back as a
    500. It's never real input, so it stops at the door."""

    def test_a_nul_escape_is_refused(self):
        with self.assertRaises(ParseError):
            ObjectJSONParser().parse(io.BytesIO(b'{"letter_meta": {"x": "a\\u0000b"}}'))

    def test_a_normal_body_still_parses(self):
        body = ObjectJSONParser().parse(io.BytesIO(b'{"a": "caf\\u00e9"}'))
        self.assertEqual(body, {"a": "café"})

    def test_a_list_is_still_refused(self):
        with self.assertRaises(ParseError):
            ObjectJSONParser().parse(io.BytesIO(b"[1, 2]"))


class QueuedEmailBackendTests(TestCase):
    """In prod every mail goes through jac-quick; a slow SMTP server must not hold up a signup."""

    def _message(self):
        message = EmailMultiAlternatives(
            "Your code", "123456", "from@example.com", ["to@example.com"]
        )
        message.attach_alternative("<b>123456</b>", "text/html")
        return message

    def test_production_uses_it(self):
        r = _python(
            "-c",
            "import django; django.setup(); from django.conf import settings as s; "
            "print(s.EMAIL_BACKEND)",
            env=_prod_env(),
        )
        self.assertEqual(r.stdout.strip(), "lukehirsch.mail.QueuedEmailBackend", r.stderr)

    def test_a_message_is_queued_not_sent_and_keeps_its_html(self):
        with mock.patch("spa.tasks.send_email") as task:
            self.assertEqual(QueuedEmailBackend().send_messages([self._message()]), 1)
        queued = task.apply_async.call_args.kwargs["args"][0]
        self.assertEqual(queued["to"], ["to@example.com"])
        self.assertEqual(queued["alternatives"], [["<b>123456</b>", "text/html"]])
        # A verification code never shows up in the worker's log lines.
        self.assertNotIn("123456", task.apply_async.call_args.kwargs["argsrepr"])

    def test_the_task_sends_what_was_queued(self):
        with override_settings(
            QUEUED_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
        ):
            send_email(message_to_dict(self._message()))
        self.assertEqual(mail.outbox[0].body, "123456")
        self.assertEqual(mail.outbox[0].alternatives[0][1], "text/html")

    @override_settings(
        EMAIL_BACKEND="lukehirsch.mail.QueuedEmailBackend",
        QUEUED_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_a_signup_still_gets_its_code_when_the_broker_is_down(self):
        """With this backend as EMAIL_BACKEND, as in prod: `message.send()` would re-enter it
        until the recursion limit. The runner's locmem swap used to hide that here."""
        message = self._message()
        with (
            mock.patch("spa.tasks.send_email") as task,
            self.assertLogs("lukehirsch.mail", level="WARNING"),
        ):
            task.apply_async.side_effect = OperationalError("down")
            QueuedEmailBackend().send_messages([message])
        self.assertEqual(task.apply_async.call_count, 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_mail_and_housekeeping_have_their_own_lane(self):
        routes = settings.CELERY_TASK_ROUTES
        self.assertEqual(routes["spa.tasks.send_email"]["queue"], "jac-mail")
        self.assertEqual(routes["spa.tasks.housekeeping"]["queue"], "jac-mail")
        self.assertEqual(routes["llm_connector.tasks.run_ai_job"]["queue"], "jac-quick")

    def test_a_mail_that_gave_up_is_counted_for_the_queue_report(self):
        cache.clear()
        with self.assertLogs("spa.tasks", level="ERROR"):
            send_email.on_failure(OSError("smtp down"), "id", (), {}, None)
        self.assertEqual(cache.get("jac:mail:failed"), 1)


class HousekeepingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="alice")

    def _job(self, age_days):
        job = AiJob.objects.create(kind="letter_rewrite", user=self.user)
        AiJob.objects.filter(pk=job.pk).update(
            updated_at=timezone.now() - timedelta(days=age_days)
        )
        return job

    def _log(self, age_days):
        row = LLMRequestLog.objects.create(
            provider="ollama", model="m", request_messages=[]
        )
        LLMRequestLog.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=age_days)
        )
        return row

    def test_old_jobs_go_and_fresh_ones_stay(self):
        old, fresh = self._job(2), self._job(0)
        housekeeping()
        self.assertFalse(AiJob.objects.filter(pk=old.pk).exists())
        self.assertTrue(AiJob.objects.filter(pk=fresh.pk).exists())

    @override_settings(LLM_LOG_RETENTION_DAYS=30)
    def test_log_rows_leave_after_the_retention_the_privacy_page_promises(self):
        old, fresh = self._log(31), self._log(29)
        housekeeping()
        self.assertFalse(LLMRequestLog.objects.filter(pk=old.pk).exists())
        self.assertTrue(LLMRequestLog.objects.filter(pk=fresh.pk).exists())

    def test_past_days_generation_counts_go_and_todays_stays(self):
        today = timezone.now().date()
        DailyGenerationCount.objects.create(
            user=self.user, day=today - timedelta(days=1), count=1
        )
        fresh = DailyGenerationCount.objects.create(user=self.user, day=today, count=1)
        housekeeping()
        self.assertEqual(list(DailyGenerationCount.objects.all()), [fresh])
