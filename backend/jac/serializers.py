import json
import logging
import re
from pathlib import Path
from typing import TypeVar

from django.conf import settings
from django.urls import reverse
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from jac.llm_prompts import ParagraphRewrite, ShortenLetter
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
from jac.queues import runs_ahead
from jac.register import ADDRESS_FORMS
from llm_connector.catalog import validate_params
from llm_connector.conf import ExecutorError, resolve_executor
from lukehirsch.mixin import ScopeRelatedToUserMixin
from lukehirsch.private_media import PrivateFileField
from spa.models import PersonalityProfile

logger = logging.getLogger(__name__)


T = TypeVar("T", bound=serializers.Serializer)

# A posting goes into every prompt of its run and every chat turn; the longest real ones are a few
# thousand characters.
MAX_POSTING_CHARS = 50_000
# What one application may hold. Far above any real one (a two-page CV's content is a few KB);
# every account can write these, and until now only nginx's 12 MB bounded them.
MAX_LETTER_CHARS = 20_000
MAX_NOTES_CHARS = 20_000
MAX_APPLICATION_JSON_BYTES = 256 * 1024


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
            "degree_level",
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

    _MAX_TEMPLATE_BYTES = 64 * 1024  # the shipped specs are ~600 bytes

    def validate_template(self, f):
        """A layout template is a JSON object, nothing else: /media/ is public, so anything a
        user uploads here is served from our own origin."""
        if f.size > self._MAX_TEMPLATE_BYTES:
            raise serializers.ValidationError("Layout template too large (max 64 KB).")
        try:
            spec = json.loads(f.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise serializers.ValidationError(
                "The layout template must be a JSON file."
            ) from exc
        finally:
            f.seek(0)
        if not isinstance(spec, dict):
            raise serializers.ValidationError(
                "The layout template must be a JSON object."
            )
        return f


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
    queue_ahead = serializers.SerializerMethodField()

    class Meta:
        model = GenerationRun
        fields = [
            "id",
            "job_application",
            "status",
            "stage",
            "queue_ahead",
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


    def get_queue_ahead(self, obj) -> int:
        return runs_ahead(obj)


class GenerationRunSummarySerializer(serializers.ModelSerializer):
    """Compact nested shape for `JobApplicationSerializer.runs` — no `result` payload,
    the SPA fetches a run's detail (or subscribes to its socket) separately."""

    class Meta:
        model = GenerationRun
        fields = ["id", "status", "stage", "mode", "provider", "model", "created_at"]
        read_only_fields = fields


class ApplicationBatchSerializer(serializers.Serializer):
    """Several postings, one request: one model-server wake, several runs, one mail."""

    postings = serializers.ListField(
        child=serializers.CharField(max_length=MAX_POSTING_CHARS),
        allow_empty=False,
        max_length=settings.GENERATION_BATCH_MAX,
    )
    provider = serializers.CharField(required=False, allow_blank=True, default="")
    model = serializers.CharField(required=False, allow_blank=True, default="")


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
        return JobPostAddressSerializer(addr).data if addr else None  # type: ignore


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
    posting_text = serializers.CharField(
        write_only=True, required=False, max_length=MAX_POSTING_CHARS
    )
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
            "sections_off",
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
        extra_kwargs = {
            "posting": {"required": False},
            "cover_letter": {"max_length": MAX_LETTER_CHARS},
            "notes": {"max_length": MAX_NOTES_CHARS},
        }

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

    _SECTIONS = frozenset(
        ("jobs", "educations", "projects", "skills", "certifications", "languages")
    )

    @staticmethod
    def _small_json(value):
        if len(json.dumps(value)) > MAX_APPLICATION_JSON_BYTES:
            raise serializers.ValidationError("This is too large to store.")
        return value

    def validate_cv_content(self, value):
        return self._small_json(value)

    def validate_letter_meta(self, value):
        return self._small_json(value)

    def validate_sections_off(self, value):
        """A list of known section keys, deduped. Unknown keys are rejected rather than
        ignored: a typo that silently does nothing is worse than a 400. The keys are the
        plural cv_content section names — "certification" is the plausible typo."""
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise serializers.ValidationError("Expected a list of section names.")
        unknown = sorted(set(value) - self._SECTIONS)
        if unknown:
            raise serializers.ValidationError(f"Unknown sections: {unknown}")
        return list(dict.fromkeys(value))

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


class _TextField(serializers.CharField):
    """Text only. CharField makes "5" out of a 5, and a passage of `5` is a client bug."""

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class RewriteRequestSerializer(serializers.Serializer):
    """Request body for `POST …/applications/<pk>/rewrite/`, and its OpenAPI schema. Nothing here
    is persisted: the passage travels in, the rewritten passage travels out."""

    text = _TextField(
        max_length=ParagraphRewrite._MAX_CHARS,
        error_messages={
            "max_length": "Passage too long — select a paragraph, not the letter."
        },
    )
    # A free-text box in the popover: a request ("more formal"), not a second passage.
    instruction = _TextField(max_length=500, required=False, allow_blank=True)
    language = _TextField(max_length=8, required=False, allow_blank=True)
    address_form = serializers.ChoiceField(
        ADDRESS_FORMS, required=False, allow_blank=True
    )
    provider = _TextField(required=False, allow_blank=True)
    model = _TextField(required=False, allow_blank=True)


class ApplicationTransitionSerializer(serializers.Serializer):
    """Request body for `POST …/applications/<pk>/transition/`, and its OpenAPI schema. Whether the
    move is legal, and the delivery method and outcome values, stay `apply_transition`'s call."""

    to = serializers.ChoiceField(JobApplication.StatusChoices.values)
    delivery_method = _TextField(required=False, allow_blank=True, default="")
    response_outcome = _TextField(required=False, allow_blank=True, default="")
    note = _TextField(required=False, allow_blank=True, default="")


class ShortenRequestSerializer(serializers.Serializer):
    """Request body for `POST …/applications/<pk>/shorten/`, and its OpenAPI schema.

    A real serializer because `target_words` is an integer with a range: inline that would
    be an `int()` cast, a `ValueError` branch and two bound checks, all of which a serializer
    field states in one line. The upper bound is generous on purpose — the client derives the
    budget from a measured page fit, so anything sane it sends should pass; the range only
    stops garbage from reaching the model.

    Nothing here is persisted: the body travels in, a shorter body travels out.
    """

    body = _TextField(max_length=ShortenLetter._MAX_CHARS)
    target_words = serializers.IntegerField(min_value=60, max_value=1000)
    # The LETTER's language, not the posting's — the user can switch the letter to German
    # in the editor after the run. Blank falls back to the posting in the view.
    language = serializers.CharField(max_length=8, required=False, allow_blank=True)
    # The letter's form of "you" (letter_meta.address_form): a German shorten keeps it.
    address_form = serializers.ChoiceField(
        ADDRESS_FORMS, required=False, allow_blank=True
    )
    provider = serializers.CharField(required=False, allow_blank=True)
    model = serializers.CharField(required=False, allow_blank=True)


def _attachment_download_path(field_file) -> str:
    """The owner-checked download URL; `?v=` (the stored uuid's prefix) changes on re-upload."""
    path = reverse("attachment-download", kwargs={"pk": field_file.instance.pk})
    return f"{path}?v={Path(field_file.name).stem[:8]}"


class CvAttachmentSerializer(ScopeRelatedToUserMixin, serializers.ModelSerializer):
    """Owner-scoped, reusable attachment. `file` must be a PDF under the size cap. The optional
    link (`job`/`education`/`certification`) is scoped to the requester's own entries (mixin) and
    at most one may be set. `user` is bound from the request, never client-supplied."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    user_scoped_fields = ("job", "education", "certification")
    _LINK_FIELDS = ("job", "education", "certification")
    _MAX_BYTES = 10 * 1024 * 1024
    _QUOTA_BYTES = 100 * 1024 * 1024  # per user: a library of a few dozen certificates
    file = PrivateFileField(path_for=_attachment_download_path)

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
        if self._bytes_in_use() + f.size > self._QUOTA_BYTES:
            raise serializers.ValidationError(
                "Your attachments would go over the 100 MB limit — delete one first."
            )
        return f

    def _bytes_in_use(self) -> int:
        """What the user's other attachments take on disk. A re-upload replaces this row's file,
        so that one doesn't count."""
        rows = CvAttachment.objects.filter(user=self.context["request"].user)
        if self.instance is not None:
            rows = rows.exclude(pk=self.instance.pk)
        total = 0
        for row in rows:
            try:
                total += row.file.size
            except OSError:  # gone from disk: takes no space
                continue
        return total

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # At most one entry link. On PATCH, fall back to the stored value for fields not sent.
        set_links = [
            name
            for name in self._LINK_FIELDS
            if (
                attrs.get(name) if name in attrs else getattr(self.instance, name, None)
            )
            is not None
        ]
        if len(set_links) > 1:
            raise serializers.ValidationError(
                "An attachment can link to at most one entry "
                f"(got {', '.join(set_links)})."
            )
        return attrs
