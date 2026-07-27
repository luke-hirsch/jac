import logging
import re
from typing import TypeVar

from django.conf import settings
from llm_connector.catalog import validate_params
from llm_connector.conf import ExecutorError, resolve_executor
from lukehirsch.mixin import ScopeRelatedToUserMixin
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator
from spa.models import PersonalityProfile

from jac.models import (
    ApplicationLayout,
    Certification,
    CvAttachment,
    Domain,
    Education,
    GenerationRun,
    Job,
    JobApplication,
    JobPostAddress,
    JobPosting,
    Language,
    Location,
    Mode,
    Project,
    Skill,
    normalize_mode,
)

logger = logging.getLogger(__name__)


T = TypeVar("T", bound=serializers.Serializer)


class FavouriteLimitMixin:
    """Enforces `CvEntry.FAVOURITE_LIMIT` at the API boundary.

    DRF never calls `Model.full_clean()`, so the model's `clean()` cap wouldn't fire on
    create/update through the API. This re-checks the same per-type favourite count here.
    Mix in *before* the ModelSerializer base so `super().validate` chains correctly.
    """

    def validate(self: serializers.Serializer, attrs):  # type: ignore[attr-defined]
        attrs = super().validate(attrs)  # type: ignore[attr-defined]
        model = self.Meta.model  # type: ignore[attr-defined]

        limit = getattr(model, "FAVOURITE_LIMIT", None)
        favourite = attrs.get("favourite", getattr(self.instance, "favourite", False))
        if favourite and limit is not None:
            user = attrs.get("user") or getattr(self.instance, "user", None)
            exclude_pk = self.instance.pk if self.instance else None
            if model.favourite_count(user, exclude_pk=exclude_pk) >= limit:
                raise serializers.ValidationError(
                    {
                        "favourite": (
                            f"You can mark at most {limit} "
                            f"{model._meta.verbose_name_plural} as favourite."
                        )
                    }
                )
        return attrs


class ScopeDomainsToUserMixin(ScopeRelatedToUserMixin):
    """Adds `domain_scoped_fields` on top of the user-scoped base mixin.

    `Domain` is the one related model whose queryset must include the
    sentinel user's system defaults alongside the request user's rows
    (see `DomainManager.for_user`), so it can't reuse the plain
    `filter(user=...)` rewrite from the base.
    """

    domain_scoped_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return
        for name in self.domain_scoped_fields:
            field = self.fields.get(name)
            if field is None:
                continue
            related = getattr(field, "child_relation", field)
            related.queryset = Domain.objects.for_user(request.user)


class DomainSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    is_default = serializers.SerializerMethodField()

    class Meta:
        model = Domain
        fields = ["id", "name", "description", "is_default", "user"]
        read_only_fields = ["id", "is_default"]
        validators = [
            UniqueTogetherValidator(
                queryset=Domain.objects.all(),
                fields=["user", "name"],
            )
        ]

    def get_is_default(self, obj) -> bool:
        return obj.user.username == settings.SYSTEM_USER_USERNAME


class LocationSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Location
        fields = [
            "id",
            "city",
            "country",
            "street",
            "zip",
            "longitude",
            "latitude",
            "user",
        ]
        read_only_fields = ["id"]


class EducationSerializer(
    FavouriteLimitMixin, ScopeDomainsToUserMixin, serializers.ModelSerializer
):
    user_scoped_fields = ("location", "skills")
    domain_scoped_fields = ("domains",)

    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        required=False,
        allow_null=True,
    )
    skills = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Skill.objects.all(),
        required=False,
    )
    domains = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Domain.objects.all(),
        required=False,
    )
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Education
        fields = [
            "id",
            "institution",
            "field_of_study",
            "started",
            "ended",
            "degree",
            "grade",
            "description",
            "location",
            "skills",
            "domains",
            "favourite",
            "user",
        ]
        read_only_fields = ["id"]


class CertificationSerializer(
    FavouriteLimitMixin, ScopeDomainsToUserMixin, serializers.ModelSerializer
):
    user_scoped_fields = ("skills",)
    domain_scoped_fields = ("domains",)

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    skills = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Skill.objects.all(),
        required=False,
    )
    domains = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Domain.objects.all(),
        required=False,
    )

    class Meta:
        model = Certification
        fields = [
            "id",
            "name",
            "issuer",
            "issued_on",
            "expires_on",
            "credential_id",
            "url",
            "description",
            "skills",
            "domains",
            "favourite",
            "user",
        ]
        read_only_fields = ["id"]


class SkillSerializer(
    FavouriteLimitMixin, ScopeDomainsToUserMixin, serializers.ModelSerializer
):
    user_scoped_fields = ("certification", "related_skills", "builds_on")
    domain_scoped_fields = ("domains",)

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    domains = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Domain.objects.all(),
        required=False,
    )

    certification = serializers.PrimaryKeyRelatedField(
        queryset=Certification.objects.all(),
        required=False,
        allow_null=True,
    )

    # Directed prerequisite edge (symmetrical=False). `builds_on` is the writable
    # forward side; `enables` is its read-only reverse (skills that build on this).
    builds_on = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Skill.objects.all(),
        required=False,
    )
    enables = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    # Computed field
    years_of_experience = serializers.SerializerMethodField()

    class Meta:
        model = Skill
        fields = [
            "id",
            "name",
            "proficiency",
            "category",
            "domains",
            "first_used",
            "certification",
            "years_of_experience_override",
            "years_of_experience",
            "description",
            "related_skills",
            "builds_on",
            "enables",
            "favourite",
            "user",
        ]
        read_only_fields = ["id", "years_of_experience", "enables"]

        validators = [
            UniqueTogetherValidator(
                queryset=Skill.objects.all(),
                fields=["user", "name"],
            )
        ]

    def get_years_of_experience(self, obj):
        return obj.years_of_experience

    def validate_related_skills(self, value):
        if self.instance and any(s.pk == self.instance.pk for s in value):
            raise serializers.ValidationError("A skill can't relate to itself.")
        return value

    def validate_builds_on(self, value):
        if self.instance and any(s.pk == self.instance.pk for s in value):
            raise serializers.ValidationError("A skill can't build on itself.")
        return value


class JobSerializer(
    FavouriteLimitMixin, ScopeDomainsToUserMixin, serializers.ModelSerializer
):
    user_scoped_fields = ("location", "skills")
    domain_scoped_fields = ("domains",)

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        required=False,
        allow_null=True,
    )
    skills = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Skill.objects.all(),
        required=False,
    )

    domains = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Domain.objects.all(),
        required=False,
    )

    class Meta:
        model = Job
        fields = [
            "id",
            "title",
            "company",
            "location",
            "job_type",
            "skills",
            "domains",
            "started",
            "ended",
            "url",
            "description",
            "favourite",
            "user",
        ]
        read_only_fields = ["id"]


class ProjectSerializer(
    FavouriteLimitMixin, ScopeDomainsToUserMixin, serializers.ModelSerializer
):
    user_scoped_fields = ("location", "skills", "job")
    domain_scoped_fields = ("domains",)

    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        required=False,
        allow_null=True,
    )
    job = serializers.PrimaryKeyRelatedField(
        queryset=Job.objects.all(),
        required=False,
        allow_null=True,
    )
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    skills = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Skill.objects.all(),
        required=False,
    )

    domains = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Domain.objects.all(),
        required=False,
    )

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "skills",
            "domains",
            "job",
            "location",
            "started",
            "ended",
            "url",
            "description",
            "favourite",
            "user",
        ]
        read_only_fields = ["id"]


class LanguageSerializer(FavouriteLimitMixin, serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Language
        fields = [
            "id",
            "name",
            "fluency",
            "description",
            "favourite",
            "user",
        ]
        read_only_fields = ["id"]


class CvSerializer(serializers.Serializer):
    """Response shape for `CVEntryListView`: the user's full career DB,
    grouped by entry type. Read-only — used to document the endpoint for
    drf-spectacular, not to deserialize input.
    """

    skills = SkillSerializer(many=True)
    jobs = JobSerializer(many=True)
    educations = EducationSerializer(many=True)
    certifications = CertificationSerializer(many=True)
    projects = ProjectSerializer(many=True)
    languages = LanguageSerializer(many=True)


class ApplicationLayoutSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    is_default = serializers.SerializerMethodField()

    class Meta:
        model = ApplicationLayout
        fields = ["id", "name", "template", "is_default", "user"]
        read_only_fields = ["id", "is_default"]
        validators = [
            UniqueTogetherValidator(
                queryset=ApplicationLayout.objects.all(),
                fields=["user", "name"],
            )
        ]

    def get_is_default(self, obj) -> bool:
        return obj.user.username == settings.SYSTEM_USER_USERNAME


class JobPostAddressSerializer(serializers.ModelSerializer):
    """Read-only nested shape for the extracted employer address."""

    class Meta:
        model = JobPostAddress
        fields = [
            "company",
            "contact_name",
            "street",
            "address_line2",
            "zip",
            "city",
            "country",
            "email",
            "phone",
        ]
        read_only_fields = fields


class GenerationRunCreateSerializer(
    ScopeRelatedToUserMixin, serializers.ModelSerializer
):
    user_scoped_fields = ("job_application",)
    mode = serializers.CharField(required=False, allow_blank=True, default="")
    provider = serializers.CharField(required=False, allow_blank=True, default="")
    model = serializers.CharField(required=False, allow_blank=True, default="")
    params = serializers.JSONField(required=False, default=dict)
    letter_tone = serializers.CharField(required=False, allow_blank=True, default="")
    letter_focus = serializers.CharField(required=False, allow_blank=True, default="")

    class Meta:
        model = GenerationRun
        fields = [
            "job_application",
            "mode",
            "provider",
            "model",
            "params",
            "letter_tone",
            "letter_focus",
            "domains",
            "started",
            "ended",
            "min_skill_proficiency",
        ]

    def validate(self, attrs):
        mode = normalize_mode((attrs.get("mode") or "").strip())
        if mode == Mode.manual:
            raise serializers.ValidationError(
                {
                    "mode": [
                        "manual never runs a generation — curate the application directly."
                    ]
                }
            )
        try:
            executor = resolve_executor(
                self.context["request"].user,
                attrs.get("provider", ""),
                attrs.get("model", ""),
            )
        except ExecutorError as exc:
            raise serializers.ValidationError({"provider": [str(exc)]})
        problems = validate_params(executor.provider, attrs.get("params") or {})
        if problems:
            raise serializers.ValidationError({"params": problems})
        if mode == Mode.high and executor.is_hirschai:
            raise serializers.ValidationError(
                {
                    "mode": [
                        "high mode needs a commercial executor — HirschAI runs standard."
                    ]
                }
            )
        attrs["mode"] = mode
        attrs["provider"] = executor.provider
        attrs["model"] = executor.model or ""
        tone = (attrs.get("letter_tone") or "").strip()
        if tone and tone not in PersonalityProfile.Tone.values:
            raise serializers.ValidationError({"letter_tone": ["unknown tone"]})
        focus = (attrs.get("letter_focus") or "").strip()
        if focus and focus not in PersonalityProfile.Focus.values:
            raise serializers.ValidationError({"letter_focus": ["unknown focus"]})
        attrs["letter_tone"] = tone
        attrs["letter_focus"] = focus
        return attrs


class GenerationRunSerializer(serializers.ModelSerializer):
    posting_title = serializers.CharField(
        source="job_application.posting.title", read_only=True, default=""
    )

    class Meta:
        model = GenerationRun
        fields = [
            "id",
            "job_application",
            "status",
            "stage",
            "error",
            "result",
            "mode",
            "provider",
            "model",
            "params",
            "letter_tone",
            "letter_focus",
            "posting_title",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class GenerationRunSummarySerializer(serializers.ModelSerializer):
    """Compact nested shape for `JobApplicationSerializer.runs` — no `result` payload,
    the SPA fetches a run's detail (or subscribes to its socket) separately."""

    class Meta:
        model = GenerationRun
        fields = ["id", "status", "stage", "mode", "provider", "model", "created_at"]
        read_only_fields = fields


class JobPostingSerializer(serializers.ModelSerializer):
    address = serializers.SerializerMethodField()

    class Meta:
        model = JobPosting
        fields = [
            "id",
            "title",
            "posting_text",
            "language",
            "source_url",
            "address",
            "active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_address(self, obj) -> dict | None:
        addr = getattr(obj, "address", None)
        return JobPostAddressSerializer(addr).data if addr else None


class JobApplicationSerializer(ScopeRelatedToUserMixin, serializers.ModelSerializer):
    """CRUD for applications. Create takes either an owned `posting` pk or a raw
    `posting_text` (creating the JobPosting inline) — exactly one of the two; both are
    create-only. `layout` may reference the user's own layouts or the system defaults.
    """

    _PIN_RE = re.compile(r"^(skill|job|project|education|certification|language):\d+$")
    _PIN_MODELS = {
        "skill": Skill,
        "job": Job,
        "project": Project,
        "education": Education,
        "certification": Certification,
        "language": Language,
    }
    MAX_PINS = 50

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    user_scoped_fields = ("posting",)
    posting_text = serializers.CharField(write_only=True, required=False)
    posting_detail = JobPostingSerializer(source="posting", read_only=True)
    runs = GenerationRunSummarySerializer(many=True, read_only=True)

    class Meta:
        model = JobApplication
        fields = [
            "id",
            "posting",
            "posting_text",
            "posting_detail",
            "cv_content",
            "cover_letter",
            "letter_meta",
            "pinned_entries",
            "attachments",
            "layout",
            "status",
            "deadline",
            "delivery_method",
            "response_outcome",
            "notes",
            "sent_by_system",
            "approved_at",
            "sent_at",
            "responded_at",
            "runs",
            "created_at",
            "updated_at",
            "user",
        ]
        read_only_fields = [
            "id",
            "posting_detail",
            "runs",
            "created_at",
            "updated_at",
            "status",
            "delivery_method",
            "response_outcome",
            "sent_by_system",
            "approved_at",
            "sent_at",
            "responded_at",
        ]
        extra_kwargs = {"posting": {"required": False}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `layout` needs own + system rows, so the plain user-scope rewrite doesn't fit.
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            self.fields["layout"].queryset = ApplicationLayout.objects.for_user(
                request.user
            )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        has_posting = attrs.get("posting") is not None
        has_text = bool(attrs.get("posting_text", "").strip())
        if self.instance is None:
            if has_posting == has_text:
                raise serializers.ValidationError(
                    {"posting": "Provide exactly one of `posting` or `posting_text`."}
                )
        elif has_posting or "posting_text" in attrs:
            raise serializers.ValidationError(
                {"posting": "The posting is bound on create and cannot be changed."}
            )
        return attrs

    def validate_pinned_entries(self, value):
        """Shape + ownership. Stale pins (entry deleted later) are tolerated at
        merge time; garbage is not tolerated at write time."""
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise serializers.ValidationError("Expected a list of entry ids.")
        if len(value) > self.MAX_PINS:
            raise serializers.ValidationError(
                f"At most {self.MAX_PINS} pins — pin less, or build the CV manually."
            )
        bad = [v for v in value if not self._PIN_RE.match(v)]
        if bad:
            raise serializers.ValidationError(f"Malformed entry ids: {bad}")
        user = self.context["request"].user
        for etype, model in self._PIN_MODELS.items():
            pks = [int(v.split(":")[1]) for v in value if v.startswith(f"{etype}:")]
            if not pks:
                continue
            owned = set(
                model.objects.filter(user=user, pk__in=pks).values_list("pk", flat=True)
            )
            missing = [pk for pk in pks if pk not in owned]
            if missing:
                raise serializers.ValidationError(
                    f"Not found or not yours: {etype}:{missing}"
                )
        return list(dict.fromkeys(value))

    def validate_attachments(self, value):
        """Ordered list of the user's own CvAttachment ids (dedup, ownership-checked). Stale
        ids (attachment deleted later) are tolerated at merge time, garbage isn't at write."""
        if not isinstance(value, list) or not all(isinstance(v, int) for v in value):
            raise serializers.ValidationError("Expected a list of attachment ids.")
        deduped = list(dict.fromkeys(value))
        user = self.context["request"].user
        owned = set(
            CvAttachment.objects.filter(user=user, pk__in=deduped).values_list(
                "pk", flat=True
            )
        )
        missing = [pk for pk in deduped if pk not in owned]
        if missing:
            raise serializers.ValidationError(f"Not found or not yours: {missing}")
        return deduped

    def create(self, validated_data):
        text = validated_data.pop("posting_text", "").strip()
        if text:
            validated_data["posting"] = JobPosting.objects.create(
                user=validated_data["user"], posting_text=text
            )
        return super().create(validated_data)


class CvAttachmentSerializer(ScopeRelatedToUserMixin, serializers.ModelSerializer):
    """Owner-scoped, reusable attachment. `file` must be a PDF under the size cap. The optional
    link (`job`/`education`/`certification`) is scoped to the requester's own entries (mixin) and
    at most one may be set. `user` is bound from the request, never client-supplied."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    user_scoped_fields = ("job", "education", "certification")
    _LINK_FIELDS = ("job", "education", "certification")
    _MAX_BYTES = 10 * 1024 * 1024

    class Meta:
        model = CvAttachment
        fields = [
            "id",
            "file",
            "label",
            "job",
            "education",
            "certification",
            "created_at",
            "user",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_file(self, f):
        if f.size > self._MAX_BYTES:
            raise serializers.ValidationError("Attachment too large (max 10 MB).")
        head = f.read(5)
        f.seek(0)
        if head[:5] != b"%PDF-":
            raise serializers.ValidationError("Only PDF attachments are supported.")
        return f

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # At most one entry link. On PATCH, fall back to the stored value for fields not sent.
        set_links = [
            name
            for name in self._LINK_FIELDS
            if (attrs.get(name) if name in attrs else getattr(self.instance, name, None))
            is not None
        ]
        if len(set_links) > 1:
            raise serializers.ValidationError(
                "An attachment can link to at most one entry "
                f"(got {', '.join(set_links)})."
            )
        return attrs
