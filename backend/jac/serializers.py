import logging
from typing import TypeVar

from django.conf import settings
from lukehirsch.mixin import ScopeRelatedToUserMixin
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from jac.models import (
    KNOWN_MODE_INPUTS,
    ApplicationLayout,
    Certification,
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
    ResumeSnippet,
    Skill,
    mode_to_grade,
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


class ResumeSnippetSerializer(ScopeDomainsToUserMixin, serializers.ModelSerializer):
    user_scoped_fields = ("skills", "job", "project")
    domain_scoped_fields = ("domains",)

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    domains = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Domain.objects.all(),
        required=False,
    )
    skills = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Skill.objects.all(),
        required=False,
    )
    job = serializers.PrimaryKeyRelatedField(
        queryset=Job.objects.all(),
        required=False,
        allow_null=True,
    )
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = ResumeSnippet
        fields = [
            "id",
            "title",
            "content",
            "kind",
            "domains",
            "skills",
            "job",
            "project",
            "language",
            "is_active",
            "created_at",
            "updated_at",
            "user",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


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
    # Canonical key is `mode`. A legacy `grade` (light/standard/strong) is still accepted and
    # mapped (the SPA flips to `mode` in the model-first-generate-panel guide). Blank/unknown → `instruct`.
    mode = serializers.CharField(required=False, allow_blank=True, default="")

    class Meta:
        model = GenerationRun
        fields = [
            "job_application",
            "mode",
            "alias",
            "verify_grounding",
            "verifier_alias",
            "personal_paragraph",
            "research_alias",
            "max_body_snippets",
            "domains",
            "started",
            "ended",
            "min_skill_proficiency",
        ]

    def validate(self, attrs):
        # Bridge: prefer an explicit `mode`, else a legacy `grade` key the old SPA still sends.
        # A blank or unrecognised value coerces to `instruct` (never a 400 — a typo shouldn't
        # fail the request), warning only when the value was non-blank and genuinely unknown.
        raw = (attrs.get("mode") or self.initial_data.get("grade") or "").strip()
        if raw and raw not in KNOWN_MODE_INPUTS:
            logger.warning("Unrecognised mode %r coerced to %r", raw, Mode.instruct)
        mode = normalize_mode(raw)
        if mode == Mode.manual:
            # "No AI" never enqueues a run — the SPA builds manual applications directly
            # (manual-no-run-mode guide). This 400 is the server-side guarantee; the UI
            # guard alone would be bypassable.
            raise serializers.ValidationError(
                {
                    "mode": [
                        "manual never runs a generation — curate the application directly."
                    ]
                }
            )
        attrs["mode"] = mode
        return attrs


class GenerationRunSerializer(serializers.ModelSerializer):
    posting_title = serializers.CharField(
        source="job_application.posting.title", read_only=True, default=""
    )
    # Compat: the SPA still reads `grade` until the model-first-generate-panel guide. Derive it from `mode`.
    grade = serializers.SerializerMethodField()

    def get_grade(self, obj) -> str:
        return mode_to_grade(obj.mode)

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
            "grade",
            "alias",
            "personal_paragraph",
            "verify_grounding",
            "evaluation",
            "score",
            "posting_title",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class GenerationRunSummarySerializer(serializers.ModelSerializer):
    """Compact nested shape for `JobApplicationSerializer.runs` — no `result` payload,
    the SPA fetches a run's detail (or subscribes to its socket) separately."""

    grade = serializers.SerializerMethodField()

    def get_grade(self, obj) -> str:
        return mode_to_grade(obj.mode)

    class Meta:
        model = GenerationRun
        fields = ["id", "status", "stage", "mode", "grade", "alias", "created_at"]
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

    def create(self, validated_data):
        text = validated_data.pop("posting_text", "").strip()
        if text:
            validated_data["posting"] = JobPosting.objects.create(
                user=validated_data["user"], posting_text=text
            )
        return super().create(validated_data)
