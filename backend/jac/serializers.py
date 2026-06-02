from typing import TYPE_CHECKING

from rest_framework import serializers
from rest_framework.relations import RelatedField
from rest_framework.validators import UniqueTogetherValidator

from jac.models import (
    Certification,
    Domain,
    Education,
    Job,
    Language,
    Location,
    Project,
    Skill,
)

_SerializerBase = serializers.Serializer if TYPE_CHECKING else object


class ScopeRelatedToUserMixin(_SerializerBase):
    """Restrict the named related-field querysets to rows owned by the request
    user. Without this, a `PrimaryKeyRelatedField(queryset=Skill.objects.all())`
    on a user-owned serializer lets user A reference user B's PKs on POST/PATCH.

    Subclasses set `user_scoped_fields` to a tuple of field names to scope
    strictly to the request user, and `domain_scoped_fields` to fields whose
    related model is `Domain` (which should accept the user's own rows plus
    the system defaults — see `DomainManager.for_user`).

    Works for both single (`PrimaryKeyRelatedField`) and many=True (wrapped in
    `ManyRelatedField`, where the queryset lives on `child_relation`).
    """

    user_scoped_fields: tuple[str, ...] = ()
    domain_scoped_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return
        for name in self.user_scoped_fields:
            field = self.fields.get(name)
            if field is None:
                continue
            related: RelatedField = getattr(field, "child_relation", field)
            if hasattr(related, "queryset") and related.queryset is not None:
                related.queryset = related.queryset.filter(user=request.user)
        for name in self.domain_scoped_fields:
            field = self.fields.get(name)
            if field is None:
                continue
            related = getattr(field, "child_relation", field)
            related.queryset = Domain.objects.for_user(request.user)


class DomainSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Domain
        fields = ["id", "name", "description", "user"]
        read_only_fields = ["id"]
        validators = [
            UniqueTogetherValidator(
                queryset=Domain.objects.all(),
                fields=["user", "name"],
            )
        ]


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


class EducationSerializer(ScopeRelatedToUserMixin, serializers.ModelSerializer):
    user_scoped_fields = ("location",)

    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        required=False,
        allow_null=True,
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
            "user",
        ]
        read_only_fields = ["id"]


class CertificationSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

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
            "user",
        ]
        read_only_fields = ["id"]


class SkillSerializer(ScopeRelatedToUserMixin, serializers.ModelSerializer):
    user_scoped_fields = ("certification",)
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
            "years_of_experience",
            "description",
            "user",
        ]
        read_only_fields = ["id", "years_of_experience"]

        validators = [
            UniqueTogetherValidator(
                queryset=Skill.objects.all(),
                fields=["user", "name"],
            )
        ]

    def get_years_of_experience(self, obj):
        return obj.years_of_experience


class JobSerializer(ScopeRelatedToUserMixin, serializers.ModelSerializer):
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
            "user",
        ]
        read_only_fields = ["id"]


class ProjectSerializer(ScopeRelatedToUserMixin, serializers.ModelSerializer):
    user_scoped_fields = ("location", "skills")
    domain_scoped_fields = ("domains",)

    location = serializers.PrimaryKeyRelatedField(
        queryset=Location.objects.all(),
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
            "location",
            "started",
            "ended",
            "url",
            "description",
            "user",
        ]
        read_only_fields = ["id"]


class LanguageSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Language
        fields = [
            "id",
            "name",
            "fluency",
            "description",
            "user",
        ]
        read_only_fields = ["id"]


class CvSerializer(serializers.Serializer):
    user = serializers.IntegerField(read_only=True)
    domains = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    started = serializers.DateField(allow_null=True)
    ended = serializers.DateField(allow_null=True)
    min_skill_proficiency = serializers.CharField(allow_blank=True)
    entries = serializers.ListField()
