from django.conf import settings
from django.db import models
from lukehirsch.managers import SystemScopedManager

from .crypto import decrypt, encrypt
from .validators import validate_safe_llm_url


class Provider(models.TextChoices):
    anthropic = "anthropic", "Anthropic"
    openai = "openai", "OpenAI"
    ollama = "ollama", "HirschAI"


class LLMConfig(models.Model):
    """One row per (user, provider): the provider credential + the default flag.

    The model is deliberately thin — WHICH model runs is a per-run pick from
    `llm_connector.catalog`, never stored here. `url`/`max_tokens`/`extra` exist for the
    system-owned HirschAI row (tower url, chat/embed model names, think flag) and are not
    exposed over the user API. Rows owned by the system user are the shared executors
    (today: HirschAI), same pattern as Domain/ApplicationLayout.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="llm_configs",
    )

    default = models.BooleanField(
        default=False, help_text="True if this is the user's default alias."
    )
    provider = models.CharField(max_length=32, choices=Provider.choices)
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

    objects = SystemScopedManager()

    class Meta:
        unique_together = [("user", "provider")]
        ordering = ["user_id", "provider"]

    def __str__(self):
        return f"{self.user} / {self.provider} {'(default)' if self.default else ''}"

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
        config: dict = {"provider": self.provider}
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
        if self.provider == Provider.ollama and self.url:
            validate_safe_llm_url(
                self.url
            )  # raises ValidationError on an internal host

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.default:
            LLMConfig.objects.filter(user=self.user, default=True).exclude(
                pk=self.pk
            ).update(default=False)


class LLMRequestLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="llm_request_logs",
    )
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
        return f" {self.provider} - {self.model} -  {self.created_at:%Y-%m-%d %H:%M:%S}"
