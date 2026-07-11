from rest_framework import serializers

from spa.models import PersonalityProfile, UserProfile
from spa.personality_questions import MAX_ANSWER_LEN, PERSONALITY_QUESTIONS


class UserProfileSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    first_name = serializers.CharField(
        source="user.first_name", required=False, allow_blank=True, max_length=150
    )
    last_name = serializers.CharField(
        source="user.last_name", required=False, allow_blank=True, max_length=150
    )

    class Meta:
        model = UserProfile
        fields = (
            "id",
            "name",
            "username",
            "email",
            "first_name",
            "last_name",
            "display_name",
            "avatar",
            "bio",
            "phone",
            "website",
            "linkedin_url",
            "github_url",
            "show_socials",
            "timezone",
            "theme",
            "contrast",
            "email_reminders",
            "updated_at",
            "street",
            "address_line2",
            "zip",
            "city",
            "country",
        )
        read_only_fields = ("id", "name", "username", "email", "updated_at")

    def get_name(self, obj) -> str:
        if obj.display_name:
            return obj.display_name
        full = f"{obj.user.first_name} {obj.user.last_name}".strip()
        return full or obj.user.username

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", None)
        if user_data:
            for attr, value in user_data.items():
                setattr(instance.user, attr, value)
            instance.user.save(update_fields=list(user_data))
        return super().update(instance, validated_data)


class PersonalityProfileSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    questions = serializers.SerializerMethodField()

    class Meta:
        model = PersonalityProfile
        fields = (
            "id",
            "user",
            "answers",
            "dossier",
            "questions",
            "answers_updated_at",
            "dossier_built_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "dossier",
            "questions",
            "answers_updated_at",
            "dossier_built_at",
            "updated_at",
        )

    def get_questions(self, obj):
        return PERSONALITY_QUESTIONS

    def validate_answers(self, answers):
        """Drop blank/whitespace-only answers; reject any answer over the one-tweet cap.

        Keys are not pinned to the question pool — the frontend owns which questions render, so a
        sparse dict (answering 5 of 12) is valid. Only the per-answer length cap is enforced here.
        """
        if not isinstance(answers, dict):
            raise serializers.ValidationError(
                "Expected a mapping of question id -> answer."
            )
        cleaned: dict = {}
        for key, value in answers.items():
            text = (value or "").strip()
            if not text:
                continue  # blank answer -> dropped, not stored
            if len(text) > MAX_ANSWER_LEN:
                raise serializers.ValidationError(
                    f"Answer '{key}' exceeds the {MAX_ANSWER_LEN}-character limit."
                )
            cleaned[key] = text
        return cleaned

    def update(self, instance, validated_data):
        if "answers" in validated_data:
            from django.utils import timezone

            instance.answers_updated_at = timezone.now()
        return super().update(instance, validated_data)
