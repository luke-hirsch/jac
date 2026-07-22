from rest_framework import serializers

from llm_connector.models import LLMConfig, LLMRequestLog, Provider


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

    class Meta:
        model = LLMConfig
        fields = (
            "id",
            "user",
            "provider",
            "default",
            "api_key",
            "has_api_key",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "has_api_key", "created_at", "updated_at")
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
        return value

    def create(self, validated_data):
        api_key = validated_data.pop("api_key", "")
        instance = LLMConfig(**validated_data)
        if api_key:
            instance.api_key = api_key
        instance.save()
        return instance

    def update(self, instance, validated_data):
        api_key = validated_data.pop("api_key", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if api_key:
            instance.api_key = api_key
        instance.save()

        return instance


class LLMRequestLogSerializer(serializers.ModelSerializer):
    """Read-only spend audit row. Surfaced via a read-only viewset, so every
    field including `user` is read-only at the API layer.
    """

    class Meta:
        model = LLMRequestLog
        fields = (
            "id",
            "user",
            "alias",
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
