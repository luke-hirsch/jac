import re
from pathlib import Path

from django.db.models import Sum
from django.urls import reverse
from django.utils.text import slugify
from rest_framework import serializers

from lukehirsch.private_media import PrivateImageField
from spa.blocks import (
    MEDIA_HEAD_BYTES,
    clean_block_data,
    missing_fields,
    sniff_media,
    takes_file,
)
from spa.captcha import MAX_PAYLOAD_LEN
from spa.models import (
    PersonalityProfile,
    PersonalityQuestion,
    PortfolioBlock,
    PortfolioLink,
    UserProfile,
)
from spa.personality_questions import MAX_ANSWER_KEY_LEN, MAX_ANSWER_LEN, MAX_ANSWERS


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


MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _validate_image_size(image) -> None:
    if image.size > MAX_IMAGE_BYTES:
        raise serializers.ValidationError("Image too large (max 5 MB).")


def _signature_path_for(field_file) -> str:
    return f"{reverse('profile-signature')}?v={Path(field_file.name).stem[:8]}"


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
    # Private: reads back as the owner-only endpoint, never a /media/ URL (guide 03, gap 8).
    signature = PrivateImageField(
        path_for=_signature_path_for,
        required=False,
        validators=[_validate_image_size],
    )
    # The site owner's portfolio is public whatever `portfolio_public` says; the SPA must not
    # offer them a switch that does nothing.
    portfolio_always_public = serializers.SerializerMethodField()
    # The colour jac wears for this user (spa.portfolio.brand_for): `accent_color`, else the site
    # owner's, else "" for the theme's fallback. `accent_color` stays the field they set.
    brand = serializers.SerializerMethodField()

    class Meta:
        model = UserProfile
        fields = (
            "id",
            "name",
            "username",
            "handle",
            "portfolio_public",
            "portfolio_link_by_default",
            "portfolio_always_public",
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
            "accent_color",
            "brand",
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
        extra_kwargs = {"avatar": {"validators": [_validate_image_size]}}

    def get_name(self, obj) -> str:
        if obj.display_name:
            return obj.display_name
        full = f"{obj.user.first_name} {obj.user.last_name}".strip()
        return full or obj.user.username

    def get_portfolio_always_public(self, obj) -> bool:
        from spa.portfolio import is_site_owner

        return is_site_owner(obj.user)

    def get_brand(self, obj) -> str:
        from spa.portfolio import brand_for

        return brand_for(obj.user)

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
        if handle in settings.RESERVED_HANDLES:
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
            "style_rules",
            "style_no_dashes",
            "style_no_antithesis",
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
        sparse dict (answering 5 of 12) is valid. Only the size caps are enforced here.
        """
        if not isinstance(answers, dict):
            raise serializers.ValidationError(
                "Expected a mapping of question id -> answer."
            )
        if len(answers) > MAX_ANSWERS:
            raise serializers.ValidationError(f"At most {MAX_ANSWERS} answers.")
        cleaned: dict = {}
        for key, value in answers.items():
            if len(key) > MAX_ANSWER_KEY_LEN:
                raise serializers.ValidationError("That's not a question id.")
            if value is not None and not isinstance(value, str):
                raise serializers.ValidationError(f"Answer '{key}' must be text.")
            text = (value or "").strip()
            if not text:
                continue  # blank answer -> dropped, not stored
            if len(text) > MAX_ANSWER_LEN:
                raise serializers.ValidationError(
                    f"Answer '{key}' exceeds the {MAX_ANSWER_LEN}-character limit."
                )
            cleaned[key] = text
        return cleaned

    def validate_style_rules(self, value):
        """One rule per line, as the writers read them: trimmed, blank lines dropped."""
        return "\n".join(
            line.strip() for line in (value or "").splitlines() if line.strip()
        )

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
    taxonomy (own + system defaults). Runs the kind rules of spa.blocks because DRF never
    calls full_clean. `data` is the model's JSONField: a JSON body sends an object, a
    multipart one (with a file) the same object as JSON text, and DRF's JSONField reads both.
    The media caps are class attributes, so a test patches them (the CvAttachmentSerializer
    pattern)."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    links = serializers.ListField(child=serializers.CharField(), required=False)
    _MAX_MEDIA_BYTES = 50 * 1024 * 1024
    # Per user, over every block's audio or video: a handful of talks and demos.
    _MEDIA_QUOTA_BYTES = 250 * 1024 * 1024

    class Meta:
        model = PortfolioBlock
        fields = (
            "id",
            "user",
            "kind",
            "title",
            "body",
            "image",
            "media",
            "data",
            "alt_text",
            "domains",
            "links",
            "favourite",
            "order",
            "is_active",
            "updated_at",
        )
        read_only_fields = ("id", "updated_at")
        extra_kwargs = {"image": {"validators": [_validate_image_size]}}

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

    def validate_media(self, f):
        mb = 1024 * 1024
        if f.size > self._MAX_MEDIA_BYTES:
            raise serializers.ValidationError(
                f"File too large (max {self._MAX_MEDIA_BYTES // mb} MB)."
            )
        head = f.read(MEDIA_HEAD_BYTES)
        f.seek(0)
        ext = sniff_media(head)
        if ext is None:
            raise serializers.ValidationError(
                "Only MP4 or WebM video and MP3, M4A or Ogg audio."
            )
        if self._media_bytes_in_use() + f.size > self._MEDIA_QUOTA_BYTES:
            raise serializers.ValidationError(
                f"Your audio and video would go over the {self._MEDIA_QUOTA_BYTES // mb} MB "
                "limit. Delete one first."
            )
        # nginx picks the Content-Type by extension, so the stored one is what the bytes are.
        f.name = f"media{ext}"
        return f

    def _media_bytes_in_use(self) -> int:
        """What the user's other blocks' audio and video take on disk. A replacement doesn't
        count the file it replaces, and a file gone from disk takes no space."""
        rows = PortfolioBlock.objects.filter(user=self.context["request"].user).exclude(
            media=""
        )
        if self.instance is not None:
            rows = rows.exclude(pk=self.instance.pk)
        total = 0
        for row in rows:
            try:
                total += row.media.size
            except OSError:
                continue
        return total

    def validate(self, attrs):
        """The kind table (spa/blocks.py). On a PATCH whatever isn't sent comes from the stored
        block, so a PATCH that only changes `kind` is checked against the data already there."""
        instance = self.instance

        def current(name, empty):
            return attrs[name] if name in attrs else getattr(instance, name, empty)

        kind = current("kind", PortfolioBlock.Kind.text)
        # A file the kind doesn't show is refused on upload. One already stored stays when the
        # kind changes: switching back brings it back, and nothing is lost by a misclick.
        for name in ("image", "media"):
            if attrs.get(name) and not takes_file(kind, name):
                label = PortfolioBlock.Kind(kind).label
                raise serializers.ValidationError(
                    {name: f"{label} blocks take no {name} file."}
                )
        missing = missing_fields(
            kind,
            body=current("body", ""),
            image=current("image", None),
            media=current("media", None),
        )
        if missing:
            raise serializers.ValidationError(missing)
        attrs["data"] = clean_block_data(kind, current("data", {}))
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
    the absolute public URL the QR encodes, `<PORTFOLIO_URL>/<handle>/<slug>/`, so the
    domain lives in exactly one env var."""

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


class PortfolioStandardSerializer(serializers.ModelSerializer):
    """The owner's standard page: the title, the intro and the Highlights their home shows a
    visitor who skips the questions. A link without the link parts: no slug, no explore
    scope, no default flag. `url` is the address that shows it at once. The connector writes
    through this too, so every rule a write must keep lives here."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = PortfolioLink
        fields = ("title", "intro", "content", "url", "updated_at")
        read_only_fields = ("updated_at",)

    def get_url(self, obj) -> str:
        from spa.portfolio import standard_page_url

        return standard_page_url(obj.user)

    def validate_content(self, value):
        """`featured` only, in the link grammar, deduped in order. Anything else is dropped:
        the standard page has no explore scope of its own (a visitor's picks are the scope)."""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Expected an object.")
        featured = value.get("featured", [])
        if not isinstance(featured, list) or not all(
            isinstance(i, str) and FEATURED_ID_RE.match(i) for i in featured
        ):
            raise serializers.ValidationError(
                "featured must be a list of '<type>:<pk>' ids."
            )
        return {"featured": list(dict.fromkeys(featured))}


class CaptchaSerializer(serializers.Serializer):
    """The body of a request that can wake the model server: the solved check's payload
    (spa.captcha). Only its shape here. The view checks it last, once every other field is
    valid, so a refused field never spends it."""

    captcha = serializers.CharField(max_length=MAX_PAYLOAD_LEN)


class PortfolioPersonaliseSerializer(CaptchaSerializer):
    """Input for a questionnaire visit's AI job: a one-tweet query (the questionnaire's own
    MAX_ANSWER_LEN), at most 10 domain names, and the style axis (the personality tone/focus
    vocab)."""

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
