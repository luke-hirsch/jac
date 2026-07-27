from django.utils.text import slugify
from rest_framework import serializers

from spa.models import PersonalityProfile, PersonalityQuestion, UserProfile
from spa.personality_questions import MAX_ANSWER_LEN


def _unique_question_slug(user, prompt: str) -> str:
    """A slug for a new user question, unique within the user's *visible* set (own rows +
    system defaults) so a user's key can never collide with — and shadow — a default's."""
    base = slugify(prompt)[:40] or "question"
    taken = set(
        PersonalityQuestion.objects.for_user(user).values_list("slug", flat=True)
    )
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


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
            "signature",
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
            "letter_tone",
            "letter_focus",
            "writing_sample",
            "style_dossier",
            "sample_updated_at",
            "style_built_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "dossier",
            "questions",
            "answers_updated_at",
            "dossier_built_at",
            "style_dossier",
            "sample_updated_at",
            "style_built_at",
            "updated_at",
        )

    def get_questions(self, obj):
        rows = sorted(
            PersonalityQuestion.objects.for_user(obj.user),
            key=lambda q: (q.user_id == obj.user_id, q.order, q.pk),
        )
        return [
            {
                "pk": q.pk,
                "slug": q.slug,
                "prompt": q.prompt,
                "editable": q.user_id == obj.user_id,
            }
            for q in rows
        ]

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
        from django.utils import timezone

        if "answers" in validated_data:
            instance.answers_updated_at = timezone.now()
        if (
            "writing_sample" in validated_data
            and validated_data["writing_sample"] != instance.writing_sample
        ):
            instance.sample_updated_at = timezone.now()
        return super().update(instance, validated_data)


class PersonalityQuestionSerializer(serializers.ModelSerializer):
    """CRUD over a user's own personality questions. `slug` is server-assigned on create and
    read-only thereafter (editing a prompt keeps the answer key intact). `editable` mirrors the
    embedded-questions flag so one shape serves both endpoints."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    editable = serializers.SerializerMethodField()

    class Meta:
        model = PersonalityQuestion
        fields = ("pk", "user", "slug", "prompt", "order", "editable")
        read_only_fields = ("slug", "order")

    def get_editable(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and obj.user_id == request.user.id)

    def validate_prompt(self, value):
        text = (value or "").strip()
        if not text:
            raise serializers.ValidationError("A question needs a prompt.")
        if len(text) > MAX_ANSWER_LEN:
            raise serializers.ValidationError(
                f"Question exceeds the {MAX_ANSWER_LEN}-character limit."
            )
        return text

    def create(self, validated_data):
        validated_data["slug"] = _unique_question_slug(
            validated_data["user"], validated_data["prompt"]
        )
        return super().create(validated_data)
