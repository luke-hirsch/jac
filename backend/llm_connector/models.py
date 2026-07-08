from django.conf import settings
from django.db import models

from .crypto import decrypt, encrypt
from .validators import validate_safe_llm_url


class LLMConfig(models.Model):
    """Per-user LLM alias configuration. Maps `(user, alias)` to a provider +
    model + credentials. API keys are Fernet-encrypted at rest; the cleartext
    is only ever materialised via the `api_key` property.

    Resolution order (see `llm_connector.conf.get_alias_config`):
      1. LLMConfig(user, alias) if it exists,
      2. else fall back to settings.LLM["default"] (the zero-cost Ollama config).
    """

    class Provider(models.TextChoices):
        anthropic = "anthropic", "Anthropic"
        openai = "openai", "OpenAI"
        google = "google", "Google"
        custom = "custom", "Custom (OpenAI-compatible HTTP)"
        ollama = "ollama", "Ollama (native /api/chat)"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="llm_configs",
    )
    alias = models.CharField(max_length=64)
    provider = models.CharField(max_length=32, choices=Provider.choices)
    model = models.CharField(max_length=200)
    url = models.URLField(blank=True)
    max_tokens = models.PositiveIntegerField(null=True, blank=True)
    api_key_encrypted = models.TextField(blank=True)
    extra = models.JSONField(
        default=dict,
        blank=True,
        help_text="Provider-specific extras forwarded to the adapter, e.g. "
        '{"reasoning_effort": "medium"} or {"think": false}.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("user", "alias")]
        ordering = ["user_id", "alias"]

    def __str__(self):
        return f"{self.user} / {self.alias} ({self.provider})"

    @property
    def api_key(self) -> str:
        return decrypt(self.api_key_encrypted) if self.api_key_encrypted else ""

    @api_key.setter
    def api_key(self, value: str) -> None:
        self.api_key_encrypted = encrypt(value) if value else ""

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key_encrypted)

    def to_config_dict(self) -> dict:
        """Shape that matches a single entry in settings.LLM — what the adapter
        constructors expect.
        """
        config: dict = {"provider": self.provider, "model": self.model}
        if self.url:
            config["url"] = self.url
        if self.max_tokens:
            config["max_tokens"] = self.max_tokens
        key = self.api_key
        if key:
            config["api_key"] = key
        if self.extra:
            config.update(self.extra)
        return config

    def clean(self):
        super().clean()
        if self.provider in ("custom", "ollama") and self.url:
            validate_safe_llm_url(
                self.url
            )  # raises ValidationError on an internal host


class LLMRequestLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="llm_request_logs",
    )
    alias = models.CharField(max_length=100)
    provider = models.CharField(max_length=100)
    model = models.CharField(max_length=200)
    prompt_tokens = models.IntegerField(null=True, blank=True)
    completion_tokens = models.IntegerField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    request_messages = models.JSONField()
    response_text = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.alias} ({self.provider}) @ {self.created_at:%Y-%m-%d %H:%M:%S}"
