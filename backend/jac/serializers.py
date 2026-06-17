from django.conf import settings
from lukehirsch.mixin import ScopeRelatedToUserMixin
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from jac.models import (
    Certification,
    Domain,
    Education,
    Job,
    Language,
    Location,
    Project,
    ResumeSnippet,
    Skill,
)


class FavouriteLimitMixin:
    """Enforces `CvEntry.FAVOURITE_LIMIT` at the API boundary.

    DRF never calls `Model.full_clean()`, so the model's `clean()` cap wouldn't fire on
    create/update through the API. This re-checks the same per-type favourite count here.
    Mix in *before* the ModelSerializer base so `super().validate` chains correctly.
    """

    def validate(self, attrs):
        attrs = super().validate(attrs)
        model = self.Meta.model
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
            "is_active",
            "created_at",
            "updated_at",
            "user",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
