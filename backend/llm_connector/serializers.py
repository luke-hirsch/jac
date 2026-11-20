import logging

from django.utils import timezone
from rest_framework import serializers

from llm_connector.catalog import refresh_models
from llm_connector.conf import HIRSCHAI_PROVIDER
from llm_connector.jobs import jobs_ahead
from llm_connector.models import AiJob, LLMConfig, LLMRequestLog, Provider

logger = logging.getLogger(__name__)


def _list_models_for_new_key(row) -> None:
    """A new key's model list right away, so the generate panel can offer its models. The key
    is saved either way: a provider that doesn't answer leaves the list empty, and Check on the
    account page fills it later."""
    if not row.usable:
        return
    try:
        refresh_models(row)
    except Exception as exc:  # noqa: BLE001 — never blocks saving the key
        logger.warning(
            "listing the models of %s key %s failed: %s", row.provider, row.pk, exc
        )


class LLMConfigSerializer(serializers.ModelSerializer):
    """One credential per commercial provider. `api_key` is write-only; omitting it
    on PATCH keeps the stored key. Saving a key lists its models (`model_count`,
    `models_checked_at`); the list itself reaches the SPA through the executors endpoint.
    The tower is not configurable here — it is the operator's system row."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    api_key = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        style={"input_type": "password"},
    )
    has_api_key = serializers.BooleanField(read_only=True)
    transfer_consent = serializers.BooleanField(write_only=True, required=False)
    model_count = serializers.SerializerMethodField()

    class Meta:
        model = LLMConfig
        fields = (
            "id",
            "user",
            "provider",
            "default",
            "api_key",
            "has_api_key",
            "transfer_consent",
            "transfer_consent_at",
            "model_count",
            "models_checked_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "has_api_key",
            "transfer_consent_at",
            "models_checked_at",
            "created_at",
            "updated_at",
        )
        validators = [
            serializers.UniqueTogetherValidator(
                queryset=LLMConfig.objects.all(), fields=("user", "provider")
            )
        ]

    def validate_provider(self, value):
        if value == Provider.ollama:
            raise serializers.ValidationError(
                "HirschAI is built in — configure commercial providers only."
            )
        # Consent is given to one provider, so a row can't carry it over to another.
        if self.instance is not None and value != self.instance.provider:
            raise serializers.ValidationError(
                "A row keeps its provider: delete it and add the other one."
            )
        return value

    def validate(self, attrs):
        consented = self.instance is not None and self.instance.transfer_consent_at
        if attrs.get("api_key") and not consented and not attrs.get("transfer_consent"):
            raise serializers.ValidationError(
                {"transfer_consent": "Agree to the transfer before saving a key."}
            )
        return attrs

    def get_model_count(self, obj) -> int:
        return len(obj.models or [])

    def create(self, validated_data):
        api_key = validated_data.pop("api_key", "")
        consent = validated_data.pop("transfer_consent", False)
        instance = LLMConfig(**validated_data)
        if api_key:
            instance.api_key = api_key
        if consent:
            instance.transfer_consent_at = timezone.now()
        instance.save()
        if api_key:
            _list_models_for_new_key(instance)
        return instance

    def update(self, instance, validated_data):
        api_key = validated_data.pop("api_key", None)
        consent = validated_data.pop("transfer_consent", False)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if api_key:
            instance.api_key = api_key
            # The list belonged to the old key: another account may reach other models.
            instance.models, instance.models_checked_at = [], None
        if consent and not instance.transfer_consent_at:
            instance.transfer_consent_at = timezone.now()
        instance.save()
        if api_key:
            _list_models_for_new_key(instance)

        return instance


class LLMRequestLogSerializer(serializers.ModelSerializer):
    """Read-only spend audit row. Surfaced via a read-only viewset, so every
    field including `user` is read-only at the API layer.

    HirschAI's error text can name the model server's address and quote its replies: that's the
    operator's to read, on the row. A user's own commercial provider errors are useful to them.
    """

    error = serializers.SerializerMethodField()

    class Meta:
        model = LLMRequestLog
        fields = (
            "id",
            "user",
            "provider",
            "model",
            "prompt_tokens",
            "completion_tokens",
            "latency_ms",
            "request_messages",
            "response_text",
            "error",
            "created_at",
        )
        read_only_fields = fields

    def get_error(self, obj) -> str:
        if obj.error and obj.provider == HIRSCHAI_PROVIDER:
            return "The AI server couldn't answer this request."
        return obj.error


class AiJobSerializer(serializers.ModelSerializer):
    queue_ahead = serializers.SerializerMethodField()

    class Meta:
        model = AiJob
        fields = ["id", "kind", "status", "stage", "queue_ahead", "result", "error"]
        read_only_fields = fields

    def get_queue_ahead(self, obj) -> int:
        return jobs_ahead(obj)
