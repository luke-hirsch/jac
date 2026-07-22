"""Crypto + the LLMConfig model + the HirschAI system row + executor resolution +
the model catalog + the reachability probe + the Executor object.

Target API = `[backend]-executor-connector` (2026-07-16 single-executor redesign):
LLMConfig is user+provider+key+default, unique per (user, provider); the tower is
a system-owned row; models are per-run picks validated against the catalog.
"""

import unittest
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import TestCase, override_settings

from llm_connector import embed
from llm_connector.catalog import CATALOG, default_model, is_known_model, models_for
from llm_connector.conf import (
    HIRSCHAI_PROVIDER,
    ExecutorError,
    default_executor,
    get_embed_floors,
    hirschai_row,
    resolve_config,
    resolve_executor,
)
from llm_connector.crypto import _fernet, decrypt, encrypt
from llm_connector.executor import Executor
from llm_connector.models import LLMConfig
from llm_connector.probe import hirschai_reachable

from ._helpers import TEST_HIRSCHAI, FakeAdapter, fake_row


class CryptoTests(TestCase):
    def setUp(self):
        _fernet.cache_clear()

    def tearDown(self):
        _fernet.cache_clear()

    def test_roundtrip(self):
        self.assertEqual(decrypt(encrypt("sk-secret-123")), "sk-secret-123")

    def test_encrypt_empty_returns_empty(self):
        self.assertEqual(encrypt(""), "")

    def test_decrypt_empty_returns_empty(self):
        self.assertEqual(decrypt(""), "")

    def test_ciphertext_is_not_plaintext(self):
        ct = encrypt("plain")
        self.assertNotEqual(ct, "plain")
        self.assertNotIn("plain", ct)

    @override_settings(LLM_ENCRYPTION_KEY=None)
    def test_missing_key_raises(self):
        _fernet.cache_clear()
        with self.assertRaises(ImproperlyConfigured):
            encrypt("anything")

    @override_settings(LLM_ENCRYPTION_KEY="not-a-valid-fernet-key")
    def test_invalid_key_raises(self):
        _fernet.cache_clear()
        with self.assertRaises(ImproperlyConfigured):
            encrypt("anything")


class LLMConfigModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="alice")
        cls.other = User.objects.create(username="bob")

    def test_api_key_property_roundtrips_through_encryption(self):
        cfg = LLMConfig.objects.create(user=self.user, provider="anthropic")
        cfg.api_key = "sk-ant-abc"
        cfg.save()
        cfg.refresh_from_db()
        self.assertEqual(cfg.api_key, "sk-ant-abc")
        self.assertNotEqual(cfg.api_key_encrypted, "sk-ant-abc")
        self.assertTrue(cfg.has_api_key)

    def test_empty_api_key_clears_encrypted_value(self):
        cfg = LLMConfig.objects.create(user=self.user, provider="anthropic")
        cfg.api_key = "secret"
        cfg.api_key = ""
        self.assertEqual(cfg.api_key_encrypted, "")
        self.assertFalse(cfg.has_api_key)

    def test_one_row_per_user_and_provider(self):
        LLMConfig.objects.create(user=self.user, provider="anthropic")
        with self.assertRaises(Exception):
            LLMConfig.objects.create(user=self.user, provider="anthropic")

    def test_to_config_dict_includes_key_and_extras(self):
        cfg = LLMConfig.objects.create(
            user=self.user,
            provider="openai",
            max_tokens=8192,
            extra={"reasoning_effort": "high"},
        )
        cfg.api_key = "sk-foo"
        cfg.save()
        d = cfg.to_config_dict()
        self.assertEqual(d["provider"], "openai")
        self.assertEqual(d["max_tokens"], 8192)
        self.assertEqual(d["api_key"], "sk-foo")
        self.assertEqual(d["reasoning_effort"], "high")
        # No stored model — WHICH model runs is a per-run catalog pick.
        self.assertNotIn("model", d)

    def test_default_is_exclusive_per_user(self):
        first = LLMConfig.objects.create(
            user=self.user, provider="anthropic", default=True
        )
        bob_row = LLMConfig.objects.create(
            user=self.other, provider="anthropic", default=True
        )
        LLMConfig.objects.create(user=self.user, provider="openai", default=True)
        first.refresh_from_db()
        bob_row.refresh_from_db()
        self.assertFalse(first.default)  # last write wins for alice
        self.assertTrue(bob_row.default)  # bob's default untouched

    # -- Tower deployment contract: an ollama row at a private address must pass
    # validation ONLY when the operator allowlisted the tunnel subnet. Exercises
    # the clean() -> validate_safe_llm_url wiring on the (system) tower row.

    @override_settings(LLM_URL_ALLOWLIST=["10.10.0.0/24"], LLM_URL_ALLOW_PRIVATE=False)
    def test_ollama_row_at_allowlisted_vpn_ip_validates(self):
        cfg = LLMConfig(
            user=self.user, provider="ollama", url="http://10.10.0.2:11434"
        )
        cfg.full_clean()  # must not raise

    @override_settings(LLM_URL_ALLOWLIST=[], LLM_URL_ALLOW_PRIVATE=False)
    def test_ollama_row_at_private_ip_rejected_without_allowlist(self):
        cfg = LLMConfig(
            user=self.user, provider="ollama", url="http://10.10.0.2:11434"
        )
        with self.assertRaisesRegex(ValidationError, "non-public"):
            cfg.full_clean()


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class HirschAiRowTests(TestCase):
    """The system-owned tower row: settings seed it once, the DB row is the
    runtime truth afterwards."""

    def test_bootstraps_the_system_row_from_settings(self):
        row = hirschai_row()
        self.assertEqual(row.provider, HIRSCHAI_PROVIDER)
        self.assertEqual(row.user.username, "system")
        self.assertFalse(row.user.is_active)
        self.assertEqual(row.url, TEST_HIRSCHAI["url"])
        self.assertEqual(row.extra["model"], TEST_HIRSCHAI["model"])
        self.assertEqual(row.extra["embed_model"], TEST_HIRSCHAI["embed_model"])

    def test_idempotent_and_db_wins_over_settings(self):
        row = hirschai_row()
        row.url = "http://tower.wg:11434"
        row.save()
        again = hirschai_row()
        self.assertEqual(again.pk, row.pk)
        self.assertEqual(again.url, "http://tower.wg:11434")
        self.assertEqual(LLMConfig.objects.filter(provider=HIRSCHAI_PROVIDER).count(), 1)

    def test_system_row_is_visible_via_for_user(self):
        row = hirschai_row()
        alice = User.objects.create(username="alice")
        self.assertIn(row, LLMConfig.objects.for_user(alice))

    @override_settings(HIRSCHAI=None)
    def test_missing_seed_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            hirschai_row()

    def test_embed_floors_read_the_tower_row(self):
        row = hirschai_row()
        row.extra = {**row.extra, "embed_floors": {"skill": 0.4}}
        row.save()
        self.assertEqual(get_embed_floors(), {"skill": 0.4})

    def test_embed_floors_empty_when_unset_or_garbage(self):
        self.assertEqual(get_embed_floors(), {})
        row = hirschai_row()
        row.extra = {**row.extra, "embed_floors": "not-a-dict"}
        row.save()
        self.assertEqual(get_embed_floors(), {})


@override_settings(HIRSCHAI=TEST_HIRSCHAI, LLM_LOGGING=False)
class ResolveConfigTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username="alice")
        cls.bob = User.objects.create(username="bob")

    def test_no_provider_and_no_default_row_resolves_to_hirschai(self):
        cfg = resolve_config(user=self.alice)
        self.assertEqual(cfg["provider"], HIRSCHAI_PROVIDER)
        self.assertEqual(cfg["model"], TEST_HIRSCHAI["model"])  # the tower's own

    def test_no_provider_with_default_row_resolves_to_it(self):
        fake_row(self.alice, default=True, model="fake-9")
        cfg = resolve_config(user=self.alice)
        self.assertEqual(cfg["provider"], "fake")
        self.assertEqual(cfg["model"], "fake-9")

    def test_explicit_model_overrides_the_rows(self):
        fake_row(self.alice)
        cfg = resolve_config("fake", user=self.alice, model="fake-override")
        self.assertEqual(cfg["model"], "fake-override")

    def test_commercial_model_defaults_from_the_catalog(self):
        row = LLMConfig(user=self.alice, provider="anthropic")
        row.api_key = "sk-a"
        row.save()
        cfg = resolve_config("anthropic", user=self.alice)
        self.assertEqual(cfg["model"], default_model("anthropic"))

    def test_missing_commercial_row_raises(self):
        with self.assertRaises(ExecutorError):
            resolve_config("anthropic", user=self.alice)

    def test_rows_do_not_leak_across_users(self):
        fake_row(self.alice)
        with self.assertRaises(ExecutorError):
            resolve_config("fake", user=self.bob)


@override_settings(HIRSCHAI=TEST_HIRSCHAI, LLM_LOGGING=False)
class ResolveExecutorTests(TestCase):
    """`resolve_executor` = the API boundary's validation: everything a client
    can name wrong raises ExecutorError (the serializers turn that into a 400)."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username="alice")

    def _anthropic_row(self, *, key="sk-a", default=False):
        row = LLMConfig(user=self.alice, provider="anthropic", default=default)
        if key:
            row.api_key = key
        row.save()
        return row

    def test_hirschai_ignores_a_client_sent_model(self):
        ex = resolve_executor(self.alice, HIRSCHAI_PROVIDER, "sneaky-model")
        self.assertTrue(ex.is_hirschai)
        self.assertIsNone(ex.model)

    def test_unknown_provider_raises(self):
        with self.assertRaises(ExecutorError):
            resolve_executor(self.alice, "google")

    def test_commercial_requires_a_stored_key(self):
        self._anthropic_row(key="")
        with self.assertRaises(ExecutorError):
            resolve_executor(self.alice, "anthropic")

    def test_commercial_model_defaults_and_validates_against_catalog(self):
        self._anthropic_row()
        ex = resolve_executor(self.alice, "anthropic")
        self.assertEqual(ex.model, default_model("anthropic"))
        with self.assertRaises(ExecutorError):
            resolve_executor(self.alice, "anthropic", "gpt-4o")

    def test_blank_provider_uses_the_default_executor(self):
        self._anthropic_row(default=True)
        ex = resolve_executor(self.alice)
        self.assertEqual(ex.provider, "anthropic")
        self.assertEqual(ex.model, default_model("anthropic"))

    def test_blank_provider_with_nothing_available_raises(self):
        with patch("llm_connector.probe.hirschai_reachable", return_value=False):
            with self.assertRaises(ExecutorError):
                resolve_executor(self.alice)


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class DefaultExecutorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username="alice")

    def test_prefers_the_commercial_default_row(self):
        row = LLMConfig(user=self.alice, provider="anthropic", default=True)
        row.api_key = "sk-a"
        row.save()
        ex = default_executor(self.alice)
        self.assertEqual(ex.provider, "anthropic")
        self.assertEqual(ex.model, default_model("anthropic"))

    def test_default_row_without_key_falls_through_to_hirschai(self):
        LLMConfig.objects.create(user=self.alice, provider="anthropic", default=True)
        with patch("llm_connector.probe.hirschai_reachable", return_value=True):
            ex = default_executor(self.alice)
        self.assertTrue(ex.is_hirschai)

    def test_tower_offline_and_nothing_configured_is_none(self):
        with patch("llm_connector.probe.hirschai_reachable", return_value=False):
            self.assertIsNone(default_executor(self.alice))


class CatalogTests(TestCase):
    def test_every_provider_names_exactly_one_default(self):
        for provider, rows in CATALOG.items():
            self.assertEqual(
                sum(1 for r in rows if r.get("default")), 1, f"provider {provider!r}"
            )

    def test_default_model_is_a_member(self):
        for provider in CATALOG:
            self.assertTrue(is_known_model(provider, default_model(provider)))

    def test_unknown_provider_and_model(self):
        self.assertEqual(models_for("nope"), [])
        self.assertIsNone(default_model("nope"))
        self.assertFalse(is_known_model("anthropic", "gpt-4o"))

    def test_hirschai_is_not_in_the_catalog(self):
        # The tower's models live on its system row, not in the pick list.
        self.assertNotIn(HIRSCHAI_PROVIDER, CATALOG)


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class ProbeTests(TestCase):
    def setUp(self):
        from llm_connector import probe

        probe._CACHE.update(ts=0.0, ok=False)

    def test_reachable_when_tags_answers(self):
        with patch("llm_connector.probe.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.status = 200
            self.assertTrue(hirschai_reachable(refresh=True))
        url = mock_open.call_args[0][0]
        self.assertTrue(str(url).endswith("/api/tags"))

    def test_unreachable_on_any_error(self):
        with patch(
            "llm_connector.probe.request.urlopen", side_effect=OSError("down")
        ):
            self.assertFalse(hirschai_reachable(refresh=True))

    def test_result_is_cached_and_refresh_busts_it(self):
        with patch("llm_connector.probe.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.status = 200
            hirschai_reachable(refresh=True)
            hirschai_reachable()
            self.assertEqual(mock_open.call_count, 1)  # cache hit
            hirschai_reachable(refresh=True)
            self.assertEqual(mock_open.call_count, 2)


@override_settings(HIRSCHAI=TEST_HIRSCHAI, LLM_LOGGING=False)
class ExecutorObjectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username="alice")

    def setUp(self):
        FakeAdapter.instances.clear()

    def test_complete_runs_on_the_named_provider_and_model(self):
        fake_row(self.alice, model="fake-1")
        out = Executor("fake", "fake-2", self.alice).complete("hi")
        self.assertEqual(out, "pong")
        adapter = FakeAdapter.instances[-1]
        self.assertEqual(adapter.config["provider"], "fake")
        self.assertEqual(adapter.config["model"], "fake-2")  # the per-run pick wins

    def test_model_none_uses_the_rows_own_model(self):
        fake_row(self.alice, model="fake-1")
        Executor("fake", None, self.alice).complete("hi")
        self.assertEqual(FakeAdapter.instances[-1].config["model"], "fake-1")

    def test_web_search_routes_to_the_adapter(self):
        fake_row(self.alice, provider="fakesearch", model="fs-1")
        res = Executor("fakesearch", user=self.alice).web_search("who is acme?")
        self.assertEqual(res["sources"], ["https://example.com/about"])

    def test_supports_web_search_flags(self):
        self.assertFalse(Executor("fake", user=self.alice).supports_web_search)
        self.assertTrue(Executor("fakesearch", user=self.alice).supports_web_search)
        self.assertFalse(Executor(HIRSCHAI_PROVIDER).supports_web_search)

    def test_is_hirschai(self):
        self.assertTrue(Executor(HIRSCHAI_PROVIDER).is_hirschai)
        self.assertFalse(Executor("anthropic").is_hirschai)

    def test_embed_is_tower_only(self):
        # The module-level embed() must always resolve the HirschAI executor —
        # embedding is a tower capability; commercial runs simply don't embed.
        with patch("llm_connector.get_client") as get_client:
            get_client.return_value.embed.return_value = [[1.0]]
            out = embed(["x"])
        get_client.assert_called_once_with(HIRSCHAI_PROVIDER)
        self.assertEqual(out, [[1.0]])


class LLMConfigAdminFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="alice")

    def _form_data(self, **overrides):
        data = {
            "user": str(self.user.pk),
            "default": "",
            "provider": "anthropic",
            "url": "",
            "max_tokens": "",
            "extra": "{}",
            "api_key": "",
        }
        data.update(overrides)
        return data

    def test_new_instance_with_api_key_encrypts_on_save(self):
        from llm_connector.admin import LLMConfigAdminForm

        form = LLMConfigAdminForm(self._form_data(api_key="sk-new"))
        self.assertTrue(form.is_valid(), form.errors)
        instance = form.save()
        self.assertEqual(instance.api_key, "sk-new")
        self.assertTrue(instance.has_api_key)

    def test_empty_api_key_on_edit_preserves_existing(self):
        from llm_connector.admin import LLMConfigAdminForm

        cfg = LLMConfig.objects.create(user=self.user, provider="anthropic")
        cfg.api_key = "sk-original"
        cfg.save()
        form = LLMConfigAdminForm(self._form_data(api_key=""), instance=cfg)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().api_key, "sk-original")

    def test_new_api_key_on_edit_replaces_existing(self):
        from llm_connector.admin import LLMConfigAdminForm

        cfg = LLMConfig.objects.create(user=self.user, provider="anthropic")
        cfg.api_key = "sk-old"
        cfg.save()
        form = LLMConfigAdminForm(self._form_data(api_key="sk-new"), instance=cfg)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().api_key, "sk-new")


@unittest.skip("[fullstack]-model-knobs — unskip when starting that guide")
class KnobSpecTests(TestCase):
    """[fullstack]-model-knobs: the knob spec is DATA in the catalog — bounds,
    choices, and exclusions validated once (`validate_params`), mapped mechanically
    by the adapters, advertised by the executors endpoint."""

    def _validate(self, provider, params):
        from llm_connector.catalog import validate_params

        return validate_params(provider, params)

    def test_every_catalog_provider_has_a_knob_spec(self):
        from llm_connector.catalog import CATALOG, KNOBS

        self.assertEqual(set(KNOBS), set(CATALOG))
        for spec in KNOBS.values():
            self.assertIn("effort", spec)
            self.assertTrue(spec["effort"]["choices"])
        # temperature is provider-specific: anthropic takes it, the OpenAI catalog is
        # reasoning-only (gpt-5.6-* reject a custom temperature) so it has none.
        self.assertIn("temperature", KNOBS["anthropic"])
        self.assertNotIn("temperature", KNOBS["openai"])
        self.assertLess(
            KNOBS["anthropic"]["temperature"]["min"],
            KNOBS["anthropic"]["temperature"]["max"],
        )

    def test_empty_params_are_always_valid(self):
        self.assertEqual(self._validate("anthropic", {}), [])
        self.assertEqual(self._validate("ollama", {}), [])

    def test_valid_values_pass(self):
        self.assertEqual(self._validate("anthropic", {"effort": "high"}), [])
        self.assertEqual(self._validate("anthropic", {"temperature": 0.5}), [])
        self.assertEqual(self._validate("openai", {"effort": "low"}), [])

    def test_unknown_knob_and_bad_values_are_caught(self):
        self.assertTrue(self._validate("anthropic", {"top_k": 5}))
        self.assertTrue(self._validate("anthropic", {"effort": "max"}))
        self.assertTrue(self._validate("anthropic", {"temperature": 9}))
        self.assertTrue(self._validate("anthropic", {"temperature": True}))  # bool ≠ number
        # OpenAI is reasoning-only — temperature is not a knob it offers.
        self.assertTrue(self._validate("openai", {"temperature": 0.5}))

    def test_exclusions_come_from_the_spec(self):
        problems = self._validate(
            "anthropic", {"effort": "high", "temperature": 0.3}
        )
        self.assertTrue(any("cannot be combined" in p for p in problems))

    def test_no_knob_providers_reject_any_params(self):
        self.assertTrue(self._validate("ollama", {"effort": "high"}))

    def test_non_dict_params_are_caught(self):
        self.assertTrue(self._validate("anthropic", "effort=high"))
