"""Settings-hardening helpers + the safe DRF default.

Red until `[backend]-settings-hardening` lands `lukehirsch/prod.py` (import error here) and flips
the DRF default permission to IsAuthenticated. Housed in `spa` because it's the closest thing to a
site/core app; `lukehirsch` is the project package and its own tests would never be collected.

Deny-by-default does NOT mean the site is private — the portfolio is public. It means public
endpoints declare AllowAny explicitly instead of being public by omission.
"""

import io
import logging
from contextlib import contextmanager, redirect_stderr
from unittest import mock

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from lukehirsch.prod import (
    DEV_ENCRYPTION_KEY,
    DEV_SECRET_KEY,
    env_bool,
    env_int,
    env_list,
    verify_production_secrets,
)


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
        # visitors from the root. Public endpoints opt in via explicit AllowAny.
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"message": "I am alive!"})

    def test_schema_stays_public(self):
        # drf-spectacular serves the schema under its own SERVE_PERMISSIONS default
        # (AllowAny) — deliberate: the API docs are part of the public showcase.
        with _muted():
            r = self.client.get("/api/schema/")
        self.assertEqual(r.status_code, 200)
