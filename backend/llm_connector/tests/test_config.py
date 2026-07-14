"""Crypto + LLMConfig model + per-user config resolution + admin form."""

from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from llm_connector import complete
from llm_connector.client import LLMClient
from llm_connector.conf import get_pinned_alias, is_free_alias, pick_alias
from llm_connector.crypto import _fernet, decrypt, encrypt
from llm_connector.models import LLMConfig, LLMGradePin, LLMRequestLog

from ._helpers import _muted, FakeAdapter, FAKE_LLM


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

    def test_api_key_property_roundtrips_through_encryption(self):
        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="anthropic",
            model="claude-sonnet-4-6",
        )
        cfg.api_key = "sk-ant-abc"
        cfg.save()
        cfg.refresh_from_db()
        self.assertEqual(cfg.api_key, "sk-ant-abc")
        self.assertNotEqual(cfg.api_key_encrypted, "sk-ant-abc")
        self.assertTrue(cfg.has_api_key)

    def test_empty_api_key_clears_encrypted_value(self):
        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="anthropic",
            model="x",
        )
        cfg.api_key = "secret"
        cfg.api_key = ""
        self.assertEqual(cfg.api_key_encrypted, "")
        self.assertFalse(cfg.has_api_key)

    def test_unique_per_user_and_alias(self):
        LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="anthropic",
            model="x",
        )
        with self.assertRaises(Exception):
            LLMConfig.objects.create(
                user=self.user,
                alias="default",
                provider="openai",
                model="y",
            )

    def test_to_config_dict_includes_api_key_and_extras(self):
        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="reasoning",
            provider="openai",
            model="o4-mini",
            max_tokens=8192,
            extra={"reasoning_effort": "high"},
        )
        cfg.api_key = "sk-foo"
        cfg.save()
        d = cfg.to_config_dict()
        self.assertEqual(d["provider"], "openai")
        self.assertEqual(d["model"], "o4-mini")
        self.assertEqual(d["max_tokens"], 8192)
        self.assertEqual(d["api_key"], "sk-foo")
        self.assertEqual(d["reasoning_effort"], "high")

    def test_to_config_dict_omits_empty_fields(self):
        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="ollama",
            provider="custom",
            model="qwen",
            url="http://localhost:11434/v1",
        )
        d = cfg.to_config_dict()
        self.assertEqual(set(d.keys()), {"provider", "model", "url"})


@override_settings(LLM=FAKE_LLM, LLM_LOGGING=False)
class UserScopedResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="alice")
        cls.other = User.objects.create(username="bob")

    def setUp(self):
        FakeAdapter.instances.clear()

    def test_user_with_config_uses_their_config(self):
        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="reasoning",
            provider="fake",
            model="user-model",
            extra={"_response": "user-specific"},
        )
        cfg.api_key = "sk-personal"
        cfg.save()
        client = LLMClient("reasoning", user=self.user)
        self.assertEqual(client._config["model"], "user-model")
        self.assertEqual(client._config["api_key"], "sk-personal")
        self.assertEqual(client.complete("hi"), "user-specific")

    def test_user_without_config_falls_back_to_default(self):
        with _muted():
            client = LLMClient("reasoning", user=self.user)
        self.assertEqual(client._config["model"], "fake-1")
        self.assertEqual(client.complete("hi"), "pong")

    def test_user_default_alias_can_override_global_default(self):
        LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="fake",
            model="alice-default",
            extra={"_response": "alice-says-hi"},
        )
        client = LLMClient("default", user=self.user)
        self.assertEqual(client._config["model"], "alice-default")
        self.assertEqual(client.complete("hi"), "alice-says-hi")

    def test_no_user_reads_settings_directly(self):
        client = LLMClient("default")
        self.assertEqual(client._config["model"], "fake-1")

    def test_one_users_config_does_not_leak_to_another(self):
        LLMConfig.objects.create(
            user=self.user,
            alias="reasoning",
            provider="fake",
            model="alice-model",
        )
        with _muted():
            client_bob = LLMClient("reasoning", user=self.other)
        self.assertEqual(client_bob._config["model"], "fake-1")

    def test_missing_default_alias_raises_on_fallback(self):
        with override_settings(LLM={"other": {"provider": "fake", "model": "x"}}):
            with _muted(), self.assertRaises(ImproperlyConfigured):
                LLMClient("anything", user=self.user)

    def test_complete_helper_threads_user(self):
        LLMConfig.objects.create(
            user=self.user,
            alias="reasoning",
            provider="fake",
            model="m",
            extra={"_response": "personal"},
        )
        self.assertEqual(complete("hi", alias="reasoning", user=self.user), "personal")


@override_settings(LLM=FAKE_LLM, LLM_LOGGING=False)
class GradePinResolutionTests(TestCase):
    """`pick_alias`: per-strength favourite-model routing. A rung's PREFERRED_GRADE
    resolves to the user's pin for that tier; no pin / a stale pin / a paid pin under
    free_only all fall back to the run's main alias."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="alice")
        LLMConfig.objects.create(
            user=cls.user,
            alias="local",
            provider="ollama",
            model="qwen3:8b",
            url="http://localhost:11434",
        )
        LLMConfig.objects.create(
            user=cls.user,
            alias="claude",
            provider="anthropic",
            model="claude-opus-4-8",
        )

    def _pin(self, strength, alias):
        LLMGradePin.objects.create(user=self.user, strength=strength, alias=alias)

    def test_no_pin_falls_back_to_the_main_alias(self):
        self.assertEqual(
            pick_alias("standard", fallback="main", user=self.user), "main"
        )

    def test_pin_wins_over_the_main_alias(self):
        self._pin("standard", "local")
        self.assertEqual(
            pick_alias("standard", fallback="main", user=self.user), "local"
        )

    def test_no_preference_or_no_user_is_a_no_op(self):
        self._pin("standard", "local")
        self.assertEqual(pick_alias(None, fallback="main", user=self.user), "main")
        self.assertEqual(pick_alias("standard", fallback="main", user=None), "main")

    def test_stale_pin_is_ignored(self):
        self._pin("standard", "deleted-row")
        with _muted():
            self.assertEqual(
                pick_alias("standard", fallback="main", user=self.user), "main"
            )

    def test_default_is_always_pinnable(self):
        self._pin("light", "default")
        self.assertEqual(get_pinned_alias("light", user=self.user), "default")

    def test_free_only_refuses_a_paid_pin(self):
        self._pin("standard", "claude")
        self.assertEqual(
            pick_alias("standard", fallback="main", user=self.user, free_only=True),
            "main",
        )
        # …but without the cost guard the pin routes normally.
        self.assertEqual(
            pick_alias("standard", fallback="main", user=self.user), "claude"
        )

    def test_free_only_accepts_a_free_pin(self):
        self._pin("standard", "local")
        self.assertEqual(
            pick_alias("standard", fallback="main", user=self.user, free_only=True),
            "local",
        )

    def test_is_free_alias(self):
        self.assertTrue(is_free_alias("local", user=self.user))
        self.assertFalse(is_free_alias("claude", user=self.user))
        # the settings "default" (provider "fake" here) is not in FREE_PROVIDERS;
        # muted: resolving it without a user row logs the expected fallback warning.
        with _muted():
            self.assertFalse(is_free_alias("default", user=self.user))


@override_settings(LLM=FAKE_LLM, LLM_LOGGING=True)
class LLMRequestLogUserAttributionTests(TestCase):
    def test_log_attributes_user_when_provided(self):
        user = User.objects.create(username="alice")
        LLMConfig.objects.create(
            user=user,
            alias="reasoning",
            provider="fake",
            model="m",
        )
        LLMClient("reasoning", user=user).complete("hi")
        log = LLMRequestLog.objects.get()
        self.assertEqual(log.user, user)

    def test_log_user_is_null_when_no_user(self):
        LLMClient("default").complete("hi")
        log = LLMRequestLog.objects.get()
        self.assertIsNone(log.user)


class LLMConfigAdminFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="alice")

    def _form_data(self, **overrides):
        data = {
            "user": str(self.user.pk),
            "alias": "default",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
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

        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="anthropic",
            model="claude-sonnet-4-6",
        )
        cfg.api_key = "sk-original"
        cfg.save()
        form = LLMConfigAdminForm(self._form_data(api_key=""), instance=cfg)
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.api_key, "sk-original")

    def test_new_api_key_on_edit_replaces_existing(self):
        from llm_connector.admin import LLMConfigAdminForm

        cfg = LLMConfig.objects.create(
            user=self.user,
            alias="default",
            provider="anthropic",
            model="x",
        )
        cfg.api_key = "sk-old"
        cfg.save()
        form = LLMConfigAdminForm(self._form_data(api_key="sk-new"), instance=cfg)
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.api_key, "sk-new")
