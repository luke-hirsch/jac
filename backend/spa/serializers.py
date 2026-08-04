import re

from django.db.models import Sum
from django.utils.text import slugify
from rest_framework import serializers

from spa.models import (
    PersonalityProfile,
    PersonalityQuestion,
    PortfolioBlock,
    PortfolioLink,
    UserProfile,
)
from spa.personality_questions import MAX_ANSWER_LEN


def _unique_question_slug(user, prompt: str) -> str:
    """A slug for a new user question, unique within the user's *visible* set (own rows +
    system defaults) so a user's key can never collide with — and shadow — a default's."""
    base = slugify(prompt)[:40] or "question"
    taken = set(
        PersonalityQuestion.objects.for_user(user).values_list("slug", flat=True)  # type: ignore
    )
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


FEATURED_ID_RE = re.compile(
    r"^(skill|job|education|certification|project|language|block):\d+$"
)
MAX_BLOCK_LINKS = 50


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
    # Explicit CharField overrides the model's SlugField auto-mapping: DRF would otherwise
    # attach a slug-regex validator that 400s on free-form input ("Jane Doe") *before*
    # validate_handle runs. We want the raw value to reach validate_handle and be slugified.
    # Uniqueness is enforced there (case-insensitive), so no auto UniqueValidator either.
    handle = serializers.CharField(max_length=100, required=False)

    class Meta:
        model = UserProfile
        fields = (
            "id",
            "name",
            "username",
            "handle",
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

    def validate_handle(self, value):
        from django.conf import settings
        from django.utils.text import slugify

        handle = slugify(value)[:40].strip("-")
        if len(handle) < 2:
            raise serializers.ValidationError("Handle must be at least 2 characters.")
        if handle in settings.RESERVED_SUBDOMAINS:
            raise serializers.ValidationError("That handle is reserved.")
        clash = UserProfile.objects.filter(handle__iexact=handle)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("That handle is already taken.")
        return handle


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
            PersonalityQuestion.objects.for_user(obj.user),  # type: ignore
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


class PortfolioBlockSerializer(serializers.ModelSerializer):
    """Owner CRUD over portfolio blocks. `domains` accepts pks from the user's visible
    taxonomy (own + system defaults). Mirrors the model's kind↔payload rule because DRF
    never calls full_clean."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    links = serializers.ListField(child=serializers.CharField(), required=False)

    class Meta:
        model = PortfolioBlock
        fields = (
            "id",
            "user",
            "kind",
            "title",
            "body",
            "image",
            "alt_text",
            "domains",
            "links",
            "favourite",
            "order",
            "is_active",
            "updated_at",
        )
        read_only_fields = ("id", "updated_at")

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        # `is_authenticated` guard: drf-spectacular introspects this serializer with an
        # AnonymousUser request when generating /api/schema/, and `for_user(AnonymousUser)`
        # casts to int and crashes. Anonymous never POSTs a block (IsAuthenticated), so the
        # default queryset is fine for them.
        if request is not None and request.user.is_authenticated:
            from jac.models import Domain  # runtime-only; models use string refs

            fields["domains"].child_relation.queryset = Domain.objects.for_user(
                request.user
            )
        return fields

    def validate(self, attrs):
        kind = attrs.get("kind", getattr(self.instance, "kind", ""))
        image = attrs.get("image", getattr(self.instance, "image", None))
        body = attrs.get("body", getattr(self.instance, "body", ""))
        if kind == PortfolioBlock.Kind.image and not image:
            raise serializers.ValidationError(
                {"image": "An image block needs an image."}
            )
        if kind == PortfolioBlock.Kind.text and not (body or "").strip():
            raise serializers.ValidationError({"body": "A text block needs a body."})
        return attrs

    def validate_links(self, value):
        """Grammar-only (like `content.featured`): '<type>:<pk>' ids, order-preserving
        dedupe, capped. Existence is NOT checked — dead ids drop at resolve time."""
        seen, out = set(), []
        for i in value:
            if not FEATURED_ID_RE.match(i):
                raise serializers.ValidationError(
                    "links must be a list of '<type>:<pk>' ids."
                )
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out[:MAX_BLOCK_LINKS]


class PortfolioLinkSerializer(serializers.ModelSerializer):
    """Owner-side link CRUD. Manage-created links are always `manual` (kind/application
    are read-only — application links come from jac's portfolio-link action); `url` is
    the absolute public URL the QR encodes, built from the owner's handle via
    PORTFOLIO_ORIGIN_TEMPLATE so the domain lives in exactly one env var."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    url = serializers.SerializerMethodField()
    visits = serializers.SerializerMethodField()

    class Meta:
        model = PortfolioLink
        fields = (
            "id",
            "user",
            "slug",
            "kind",
            "title",
            "intro",
            "application",
            "content",
            "revoked_at",
            "url",
            "visits",
            "is_default",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "kind",
            "application",
            "revoked_at",
            "created_at",
            "updated_at",
        )

    def get_url(self, obj) -> str:
        from spa.portfolio import public_portfolio_url

        return public_portfolio_url(obj)

    def get_visits(self, obj) -> int:
        # Small owner lists — the per-row aggregate is fine; revisit if links grow.
        return obj.visits.aggregate(total=Sum("count"))["total"] or 0

    def validate_slug(self, value):
        slug = slugify(value)[:80]
        if not slug:
            raise serializers.ValidationError("Slug can't be empty.")
        # Per-user active-slug uniqueness. The partial DB constraint
        # (`unique_active_slug_per_user`) isn't enforced by DRF's auto-validators, so
        # mirror it here on the *normalized* slug — the value that actually gets saved.
        user = self.instance.user if self.instance else self.context["request"].user
        clash = PortfolioLink.objects.filter(
            user=user, slug=slug, revoked_at__isnull=True
        )
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                "You already have an active portfolio with this slug."
            )
        return slug

    def validate_content(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Expected an object.")
        featured = value.get("featured", [])
        domains = value.get("domains", [])
        if not isinstance(featured, list) or not all(
            isinstance(i, str) and FEATURED_ID_RE.match(i) for i in featured
        ):
            raise serializers.ValidationError(
                "featured must be a list of '<type>:<pk>' ids."
            )
        if not isinstance(domains, list) or not all(
            isinstance(d, str) and d.strip() for d in domains
        ):
            raise serializers.ValidationError("domains must be a list of names.")
        return {
            "featured": featured,
            "domains": domains,
            "hide_explore": bool(value.get("hide_explore", False)),
        }


class PortfolioRankSerializer(serializers.Serializer):
    """Input caps for the embed finale — one-tweet query (the questionnaire's own
    MAX_ANSWER_LEN), at most 10 domain names."""

    query = serializers.CharField(max_length=MAX_ANSWER_LEN)
    domains = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, max_length=10
    )


class PortfolioIntroSerializer(serializers.Serializer):
    """Input for the AI-intro endpoint — same caps as the rank finale, plus the style
    axis (reusing the personality tone/focus vocab)."""

    query = serializers.CharField(
        max_length=MAX_ANSWER_LEN, required=False, allow_blank=True
    )
    domains = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, max_length=10
    )
    tone = serializers.ChoiceField(
        choices=PersonalityProfile.Tone.choices,
        required=False,
        default=PersonalityProfile.Tone.neutral,
    )
    focus = serializers.ChoiceField(
        choices=PersonalityProfile.Focus.choices,
        required=False,
        default=PersonalityProfile.Focus.balanced,
    )
