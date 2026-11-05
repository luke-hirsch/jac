from django.utils import timezone
from rest_framework import serializers

from llm_connector.conf import HIRSCHAI_PROVIDER
from llm_connector.jobs import jobs_ahead
from llm_connector.models import AiJob, LLMConfig, LLMRequestLog, Provider


class LLMConfigSerializer(serializers.ModelSerializer):
    """One credential per commercial provider. `api_key` is write-only; omitting it
    on PATCH keeps the stored key. The tower is not configurable here — it is the
    operator's system row."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    api_key = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        style={"input_type": "password"},
    )
    has_api_key = serializers.BooleanField(read_only=True)
    transfer_consent = serializers.BooleanField(write_only=True, required=False)

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
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "has_api_key",
            "transfer_consent_at",
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

    def create(self, validated_data):
        api_key = validated_data.pop("api_key", "")
        consent = validated_data.pop("transfer_consent", False)
        instance = LLMConfig(**validated_data)
        if api_key:
            instance.api_key = api_key
        if consent:
            instance.transfer_consent_at = timezone.now()
        instance.save()
        return instance

    def update(self, instance, validated_data):
        api_key = validated_data.pop("api_key", None)
        consent = validated_data.pop("transfer_consent", False)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if api_key:
            instance.api_key = api_key
        if consent and not instance.transfer_consent_at:
            instance.transfer_consent_at = timezone.now()
        instance.save()

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
