from rest_framework import serializers

from spa.models import PersonalityProfile, UserProfile
from spa.personality_questions import MAX_ANSWER_LEN, PERSONALITY_QUESTIONS


class UserProfileSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = UserProfile
        fields = (
            "id",
            "user",
            "display_name",
            "avatar",
            "bio",
            "phone",
            "website",
            "linkedin_url",
            "github_url",
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
        read_only_fields = ("id", "updated_at")


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
            raise serializers.ValidationError("Expected a mapping of question id -> answer.")
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
