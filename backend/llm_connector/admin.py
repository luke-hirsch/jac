from django import forms
from django.contrib import admin

from .models import LLMConfig, LLMRequestLog


class LLMConfigAdminForm(forms.ModelForm):
    """Surfaces a write-only `api_key` field that round-trips through the
    model's encrypted property. Empty submissions preserve the existing key
    so editors can update other fields without re-entering the secret.
    """

    api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the existing key. Submit a new value to replace it.",
    )

    class Meta:
        model = LLMConfig
        fields = (
            "user",
            "default",
            "provider",
            "url",
            "max_tokens",
            "extra",
            "api_key",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.has_api_key:
            self.fields["api_key"].help_text = (
                "An encrypted API key is currently set. Leave blank to keep it, "
                "or submit a new value to replace it."
            )

    def save(self, commit=True):
        instance = super().save(commit=False)
        new_key = self.cleaned_data.get("api_key")
        if new_key:
            instance.api_key = new_key
        if commit:
            instance.save()
        return instance


@admin.register(LLMConfig)
class LLMConfigAdmin(admin.ModelAdmin):
    form = LLMConfigAdminForm
    list_display = ("user", "provider", "default", "api_key_set", "updated_at")
    list_filter = ("provider", "default")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("user",)

    @admin.display(boolean=True, description="API key")
    def api_key_set(self, obj):
        return obj.has_api_key


@admin.register(LLMRequestLog)
class LLMRequestLogAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "provider",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "latency_ms",
        "created_at",
        "has_error",
    )
    list_filter = ("provider", "model")
    search_fields = ("user__username", "provider", "model", "error")
    readonly_fields = (
        "user",
        "provider",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "latency_ms",
        "created_at",
        "request_messages",
        "response_text",
        "error",
    )

    @admin.display(boolean=True, description="Error?")
    def has_error(self, obj):
        return bool(obj.error)
