from rest_framework import serializers

from spa.models import PersonalityProfile, UserProfile
from spa.personality_questions import PERSONALITY_QUESTIONS


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

    def update(self, instance, validated_data):
        if "answers" in validated_data:
            from django.utils import timezone

            instance.answers_updated_at = timezone.now()
        return super().update(instance, validated_data)
