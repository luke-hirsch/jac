"""Crypto + the LLMConfig model + the HirschAI system row + executor resolution +
the rules over a key's model list + the reachability probe + the Executor object.

Target API = `[backend]-executor-connector` (2026-07-16 single-executor redesign):
LLMConfig is user+provider+key+default, unique per (user, provider); the tower is
a system-owned row; models are per-run picks validated against the list the key's
row stores (`[fullstack]-live-model-catalog`: no model id in code).
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError
from django.test import TestCase, override_settings

from llm_connector import embed
from llm_connector.catalog import (
    COMMERCIAL_PROVIDERS,
    REFRESH_TIMEOUT_S,
    default_model,
    find_model,
    is_known_model,
    knobs_for,
    picker_models,
    refresh_models,
    validate_params,
)
from llm_connector.conf import (
    HIRSCHAI_PROVIDER,
    ExecutorError,
    default_executor,
    executor_knobs,
    get_embed_floors,
    hirschai_row,
    resolve_config,
    resolve_executor,
)
from llm_connector.crypto import _fernet, decrypt, encrypt
from llm_connector.executor import Executor
from llm_connector.models import LLMConfig, Provider
from llm_connector.probe import (
    hirschai_reachable,
    hirschai_state,
    note_hirschai_answered,
    note_hirschai_failed,
)

from ._helpers import ANTHROPIC_MODELS, TEST_HIRSCHAI, FakeAdapter, fake_row, keyed_row

DEFAULT_PICK = "claude-sonnet-5"  # the newest Sonnet in ANTHROPIC_MODELS, not its first entry


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

    def test_usable_needs_the_key_and_the_consent(self):
        """`has_api_key` is the badge; `usable` decides whether data goes out."""
        self.assertTrue(keyed_row(self.user).usable)
        unstamped = keyed_row(self.other, consent=False)
        self.assertTrue(unstamped.has_api_key)
        self.assertFalse(unstamped.usable)
        stamp_only = keyed_row(self.user, "openai", api_key="")
        self.assertFalse(stamp_only.usable)

    def test_one_row_per_user_and_provider(self):
        LLMConfig.objects.create(user=self.user, provider="anthropic")
        with self.assertRaises(IntegrityError):
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
        # No stored model — WHICH model runs is a per-run pick; the list stays on the row.
        self.assertNotIn("model", d)
        self.assertNotIn("models", d)

    def test_a_new_row_has_no_model_list(self):
        cfg = LLMConfig.objects.create(user=self.user, provider="anthropic")
        self.assertEqual(cfg.models, [])
        self.assertIsNone(cfg.models_checked_at)

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
    """The system-owned tower row: settings seed it once. Afterwards it carries what the
    admin tweaks, under whatever this process's environment names."""

    def test_bootstraps_the_system_row_from_settings(self):
        row = hirschai_row()
        self.assertEqual(row.provider, HIRSCHAI_PROVIDER)
        self.assertEqual(row.user.username, "system")
        self.assertFalse(row.user.is_active)
        self.assertEqual(row.url, TEST_HIRSCHAI["url"])
        self.assertEqual(row.extra["model"], TEST_HIRSCHAI["model"])
        self.assertEqual(row.extra["embed_model"], TEST_HIRSCHAI["embed_model"])

    def test_idempotent_and_the_row_wins_over_the_seed(self):
        row = hirschai_row()
        row.url = "http://tower.wg:11434"
        row.save()
        again = hirschai_row()
        self.assertEqual(again.pk, row.pk)
        self.assertEqual(again.url, "http://tower.wg:11434")
        self.assertEqual(LLMConfig.objects.filter(provider=HIRSCHAI_PROVIDER).count(), 1)
        self.assertEqual(resolve_config(HIRSCHAI_PROVIDER)["url"], "http://tower.wg:11434")

    @override_settings(
        HIRSCHAI_ENV={"url": "http://elsewhere:11434", "api_key": "worker-key"}
    )
    def test_the_environment_wins_over_the_row_on_every_call(self):
        """Moving the model server means changing env, and web + worker can carry different
        keys for one row. A silently ignored env var would be the failure."""
        row = hirschai_row()
        row.url = "http://stale-row:11434"
        row.save()
        config = resolve_config(HIRSCHAI_PROVIDER)
        self.assertEqual(config["url"], "http://elsewhere:11434")
        self.assertEqual(config["api_key"], "worker-key")
        self.assertEqual(config["model"], TEST_HIRSCHAI["model"])  # unset: the row's

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

    def test_commercial_model_defaults_from_the_stored_list(self):
        keyed_row(self.alice, models=ANTHROPIC_MODELS)
        cfg = resolve_config("anthropic", user=self.alice)
        self.assertEqual(cfg["model"], DEFAULT_PICK)

    def test_the_models_thinking_mode_travels_to_the_adapter(self):
        """What map_params decides on: adaptive + effort, or the old token budget."""
        keyed_row(self.alice, models=ANTHROPIC_MODELS)
        cfg = resolve_config("anthropic", user=self.alice, model="claude-opus-4-8")
        self.assertEqual(cfg["thinking"], "adaptive")
        cfg = resolve_config(
            "anthropic", user=self.alice, model="claude-haiku-4-5-20251001"
        )
        self.assertEqual(cfg["thinking"], "budget")
        self.assertNotIn("models", cfg)

    def test_a_key_without_a_list_says_how_to_fix_it(self):
        keyed_row(self.alice)
        with self.assertRaisesRegex(ExecutorError, "press Check"):
            resolve_config("anthropic", user=self.alice)

    def test_a_model_the_key_doesnt_reach_raises(self):
        """The pipeline builds its Executor from the run: a list that changed since the run
        was queued must fail it, not run another model."""
        keyed_row(self.alice, models=ANTHROPIC_MODELS)
        with self.assertRaises(ExecutorError):
            resolve_config("anthropic", user=self.alice, model="claude-gone-1")

    def test_a_key_without_consent_never_leaves_the_database(self):
        """The gate under every call on a user's key, the pipeline's included: it builds its
        Executor from the run and never asks `resolve_executor` again."""
        keyed_row(self.alice, consent=False)
        with self.assertRaisesRegex(ExecutorError, "agree to the transfer"):
            resolve_config("anthropic", user=self.alice)

    def test_no_provider_with_an_unconsented_default_row_is_hirschai(self):
        fake_row(self.alice, default=True, consent=False)
        cfg = resolve_config(user=self.alice)
        self.assertEqual(cfg["provider"], HIRSCHAI_PROVIDER)
        self.assertNotIn("sk-test", cfg.values())

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

    def _anthropic_row(self, *, key="sk-a", default=False, consent=True,
                       models=ANTHROPIC_MODELS):
        return keyed_row(
            self.alice, api_key=key, default=default, consent=consent, models=models
        )

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

    def test_a_key_without_consent_counts_as_unconfigured(self):
        """A key set in the admin, or older than the consent: re-saving it is the fix, so the
        message says that rather than "no key"."""
        self._anthropic_row(consent=False)
        with self.assertRaises(ExecutorError) as raised:
            resolve_executor(self.alice, "anthropic")
        message = str(raised.exception)
        self.assertIn("save the key again and agree to the transfer", message)
        self.assertNotIn("No 'anthropic' API key", message)

    def test_the_same_key_with_consent_runs(self):
        self._anthropic_row()
        ex = resolve_executor(self.alice, "anthropic")
        self.assertEqual((ex.provider, ex.model), ("anthropic", DEFAULT_PICK))

    def test_blank_provider_skips_an_unconsented_default(self):
        self._anthropic_row(default=True, consent=False)
        self.assertTrue(resolve_executor(self.alice).is_hirschai)

    def test_commercial_model_defaults_and_validates_against_the_stored_list(self):
        self._anthropic_row()
        ex = resolve_executor(self.alice, "anthropic")
        self.assertEqual(ex.model, DEFAULT_PICK)
        self.assertEqual(
            resolve_executor(self.alice, "anthropic", "claude-opus-4-8").model,
            "claude-opus-4-8",
        )
        with self.assertRaises(ExecutorError):
            resolve_executor(self.alice, "anthropic", "gpt-4o")

    def test_an_empty_list_raises_and_never_falls_back(self):
        """Not to another model, not to HirschAI: the message names the fix."""
        self._anthropic_row(default=True, models=None)
        for provider in ("anthropic", ""):
            with self.subTest(provider=provider):
                with self.assertRaises(ExecutorError) as raised:
                    resolve_executor(self.alice, provider)
                self.assertIn("press Check", str(raised.exception))
                self.assertIn("Account → LLM providers", str(raised.exception))

    def test_blank_provider_uses_the_default_executor(self):
        self._anthropic_row(default=True)
        ex = resolve_executor(self.alice)
        self.assertEqual(ex.provider, "anthropic")
        self.assertEqual(ex.model, DEFAULT_PICK)

    def test_blank_provider_with_nothing_configured_is_hirschai(self):
        self.assertTrue(resolve_executor(self.alice).is_hirschai)

    def test_blank_provider_cant_name_a_model_for_the_tower(self):
        """It would load any model on the model server and evict the tuned ones."""
        ex = resolve_executor(self.alice, "", "sneaky-model")
        self.assertTrue(ex.is_hirschai)
        self.assertIsNone(ex.model)

    def test_blank_provider_validates_the_model_against_the_stored_list(self):
        self._anthropic_row(default=True)
        with self.assertRaises(ExecutorError):
            resolve_executor(self.alice, "", "gpt-4o")
        ex = resolve_executor(self.alice, "", "claude-opus-4-8")
        self.assertEqual((ex.provider, ex.model), ("anthropic", "claude-opus-4-8"))

    def test_anything_but_text_raises(self):
        for provider, model in ((5, ""), ("", 5), (["ollama"], ""), ("", {"a": 1})):
            with self.assertRaises(ExecutorError):
                resolve_executor(self.alice, provider, model)

    def test_null_means_blank(self):
        self.assertTrue(resolve_executor(self.alice, None, None).is_hirschai)


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class DefaultExecutorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username="alice")

    def test_prefers_the_commercial_default_row(self):
        keyed_row(self.alice, default=True, models=ANTHROPIC_MODELS)
        ex = default_executor(self.alice)
        self.assertEqual(ex.provider, "anthropic")
        self.assertEqual(ex.model, DEFAULT_PICK)

    def test_a_default_key_without_a_list_raises_instead_of_running_on_hirschai(self):
        keyed_row(self.alice, default=True)
        with self.assertRaises(ExecutorError) as raised:
            default_executor(self.alice)
        self.assertEqual(raised.exception.provider, "anthropic")
        self.assertIn("press Check", str(raised.exception))

    def test_default_row_without_key_falls_through_to_hirschai(self):
        LLMConfig.objects.create(user=self.alice, provider="anthropic", default=True)
        self.assertTrue(default_executor(self.alice).is_hirschai)

    def test_default_row_without_consent_falls_through_to_hirschai(self):
        keyed_row(self.alice, default=True, consent=False)
        self.assertTrue(default_executor(self.alice).is_hirschai)

    def test_no_reachability_gate(self):
        """A model server that is asleep is normal: the run queues and waits for it. Asking it
        here would also wake it on every new application."""
        with patch("llm_connector.probe.request.urlopen") as urlopen:
            ex = default_executor(self.alice)
        self.assertTrue(ex.is_hirschai)
        urlopen.assert_not_called()


def _ids(*model_ids):
    return [{"id": i, "label": i, "knobs": {}} for i in model_ids]


class CatalogTests(TestCase):
    """The rules over a stored list: no model id lives in code."""

    def test_the_commercial_providers_are_the_ones_users_bring_keys_for(self):
        self.assertEqual(
            set(COMMERCIAL_PROVIDERS), set(Provider.values) - {HIRSCHAI_PROVIDER}
        )

    def test_hirschai_is_not_commercial(self):
        # The tower's models live on its system row, not in a pick list.
        self.assertNotIn(HIRSCHAI_PROVIDER, COMMERCIAL_PROVIDERS)

    def test_the_default_pick_is_the_newest_of_the_family(self):
        self.assertEqual(default_model("anthropic", ANTHROPIC_MODELS), DEFAULT_PICK)
        self.assertEqual(
            default_model("openai", _ids("o3", "gpt-5.6-sol", "gpt-5.6-terra")),
            "gpt-5.6-sol",
        )

    def test_without_the_family_the_newest_of_all(self):
        self.assertEqual(
            default_model("anthropic", _ids("claude-opus-4-8", "claude-haiku-4-5")),
            "claude-opus-4-8",
        )
        self.assertEqual(default_model("nope", _ids("x-2", "x-1")), "x-2")

    def test_no_list_no_default(self):
        self.assertIsNone(default_model("anthropic", []))
        self.assertIsNone(default_model("anthropic", None))

    def test_known_models(self):
        self.assertTrue(is_known_model(ANTHROPIC_MODELS, "claude-opus-4-8"))
        self.assertFalse(is_known_model(ANTHROPIC_MODELS, "gpt-4o"))
        self.assertFalse(is_known_model([], "claude-opus-4-8"))
        self.assertIsNone(find_model(None, "claude-opus-4-8"))

    def test_knobs_come_from_the_models_entry(self):
        self.assertEqual(
            knobs_for(ANTHROPIC_MODELS, "claude-haiku-4-5-20251001"),
            {"effort": {"choices": ["low", "medium", "high"]}},
        )
        self.assertEqual(knobs_for(ANTHROPIC_MODELS, "gpt-4o"), {})

    def test_knobs_are_a_copy(self):
        knobs_for(ANTHROPIC_MODELS, "claude-opus-4-8")["effort"]["extra"] = 1
        self.assertNotIn("extra", ANTHROPIC_MODELS[0]["knobs"]["effort"])

    def test_the_picker_marks_one_default_and_keeps_private_fields_back(self):
        rows = picker_models("anthropic", ANTHROPIC_MODELS)
        self.assertEqual([r["id"] for r in rows], [m["id"] for m in ANTHROPIC_MODELS])
        self.assertEqual([r["id"] for r in rows if r.get("default")], [DEFAULT_PICK])
        for row in rows:
            with self.subTest(row["id"]):
                self.assertNotIn("thinking", row)
                self.assertIn("knobs", row)
        self.assertEqual(picker_models("anthropic", []), [])


@override_settings(LLM_LOGGING=False)
class RefreshModelsTests(TestCase):
    """The one place a key's list comes from: the provider, asked with a short timeout and one
    try, under the same consent gate as every call on the key."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username="alice")

    def setUp(self):
        FakeAdapter.instances.clear()

    def test_stores_the_list_and_when(self):
        row = fake_row(self.alice, model="fake-7")
        listed = refresh_models(row)
        row.refresh_from_db()
        self.assertEqual(listed, [{"id": "fake-7", "label": "Fake", "knobs": {}}])
        self.assertEqual(row.models, listed)
        self.assertIsNotNone(row.models_checked_at)

    def test_asks_quickly_and_once(self):
        refresh_models(fake_row(self.alice))
        config = FakeAdapter.instances[-1].config
        self.assertEqual(config["timeout"], REFRESH_TIMEOUT_S)
        self.assertEqual(config["max_retries"], 0)

    def test_a_key_without_consent_is_never_listed(self):
        row = fake_row(self.alice, consent=False)
        with self.assertRaisesRegex(ExecutorError, "agree to the transfer"):
            refresh_models(row)
        self.assertEqual(FakeAdapter.instances, [])  # no adapter, so no request

    def test_a_failed_listing_keeps_the_last_list(self):
        row = fake_row(self.alice)
        row.models = _ids("fake-old")
        row.save()
        with (
            patch.object(FakeAdapter, "list_models", side_effect=RuntimeError("down")),
            self.assertRaises(RuntimeError),
        ):
            refresh_models(row)
        row.refresh_from_db()
        self.assertEqual([m["id"] for m in row.models], ["fake-old"])


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class ExecutorKnobsTests(TestCase):
    """What a run's params are validated against: the run's model's entry in the stored list."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username="alice")
        keyed_row(cls.alice, models=ANTHROPIC_MODELS)

    def test_the_models_own_knobs(self):
        knobs = executor_knobs(Executor("anthropic", "claude-opus-4-8", self.alice))
        self.assertEqual(knobs["effort"]["choices"], ["low", "medium", "high", "xhigh", "max"])

    def test_hirschai_and_unknown_models_have_none(self):
        self.assertEqual(executor_knobs(Executor(HIRSCHAI_PROVIDER, None, self.alice)), {})
        self.assertEqual(executor_knobs(Executor("anthropic", "gpt-4o", self.alice)), {})
        self.assertEqual(executor_knobs(Executor("openai", "gpt-5.6-sol", self.alice)), {})


@override_settings(HIRSCHAI=TEST_HIRSCHAI)
class ProbeTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_reachable_when_tags_answers(self):
        with patch("llm_connector.probe.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.status = 200
            self.assertTrue(hirschai_reachable())
        self.assertTrue(mock_open.call_args[0][0].full_url.endswith("/api/tags"))

    def test_unreachable_on_any_error(self):
        with patch(
            "llm_connector.probe.request.urlopen", side_effect=OSError("down")
        ):
            self.assertFalse(hirschai_reachable())

    @override_settings(HIRSCHAI_ENV={"api_key": "web-key"})
    def test_the_live_check_sends_the_key(self):
        with patch("llm_connector.probe.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.status = 200
            hirschai_reachable()
        req = mock_open.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer web-key")

    def test_the_state_only_repeats_what_real_calls_found(self):
        with patch("llm_connector.probe.request.urlopen") as urlopen:
            self.assertIsNone(hirschai_state())
            note_hirschai_answered()
            self.assertIs(hirschai_state(), True)
            note_hirschai_failed()
            self.assertIs(hirschai_state(), False)
        urlopen.assert_not_called()


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

    def test_a_key_without_consent_sends_nothing(self):
        fake_row(self.alice, consent=False)
        with self.assertRaises(ExecutorError):
            Executor("fake", None, self.alice).complete("hi")
        self.assertEqual(FakeAdapter.instances, [])  # no adapter, so no request

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


class KnobSpecTests(TestCase):
    """[fullstack]-model-knobs, per model since `[fullstack]-live-model-catalog`: a model's
    knob spec is DATA on its stored entry, built from the provider's capability data, validated
    once (`validate_params`), mapped mechanically by the adapters, advertised per model by the
    executors endpoint."""

    ADAPTIVE = knobs_for(ANTHROPIC_MODELS, "claude-opus-4-8")
    BUDGET = knobs_for(ANTHROPIC_MODELS, "claude-haiku-4-5-20251001")

    def test_empty_params_are_always_valid(self):
        self.assertEqual(validate_params(self.ADAPTIVE, {}), [])
        self.assertEqual(validate_params({}, {}), [])

    def test_valid_values_pass(self):
        for effort in ("low", "xhigh", "max"):
            with self.subTest(effort):
                self.assertEqual(validate_params(self.ADAPTIVE, {"effort": effort}), [])
        self.assertEqual(validate_params(self.BUDGET, {"effort": "high"}), [])

    def test_a_level_the_model_doesnt_offer_is_caught(self):
        self.assertTrue(validate_params(self.BUDGET, {"effort": "max"}))
        self.assertTrue(validate_params(self.ADAPTIVE, {"effort": ["high"]}))

    def test_unknown_knobs_are_caught_temperature_included(self):
        # Temperature is a 400 on every current Claude model: no model offers it.
        self.assertTrue(validate_params(self.ADAPTIVE, {"top_k": 5}))
        self.assertTrue(validate_params(self.ADAPTIVE, {"temperature": 0.5}))

    def test_a_model_without_knobs_rejects_any_params(self):
        self.assertTrue(validate_params({}, {"effort": "high"}))

    def test_non_dict_params_are_caught(self):
        self.assertTrue(validate_params(self.ADAPTIVE, "effort=high"))
