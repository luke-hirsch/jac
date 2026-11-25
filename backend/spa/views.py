# Portfolio / personalized-link views — Phase 3+.

import mimetypes
from pathlib import Path

from allauth.account.internal.flows.reauthentication import did_recently_authenticate
from django.contrib.auth import logout
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from llm_connector.base import WAITING_STAGE
from llm_connector.conf import ExecutorError, resolve_executor
from llm_connector.jobs import start_ai_job
from llm_connector.models import AiJob
from llm_connector.serializers import AiJobSerializer
from llm_connector.tasks import request_prewarm
from lukehirsch.private_media import private_file_response
from lukehirsch.storage import delete_on_commit
from lukehirsch.throttling import UploadThrottle
from spa.captcha import REFUSED, check_captcha, new_challenge
from spa.models import (
    PersonalityProfile,
    PersonalityQuestion,
    PortfolioBlock,
    PortfolioLink,
    UserProfile,
)
from spa.portfolio import (
    build_payload,
    bump_visit,
    link_owner_for_handle,
    owner_domains,
    owner_for_handle,
    standard_page,
    standard_page_for,
)
from spa.serializers import (
    CaptchaSerializer,
    PersonalityProfileSerializer,
    PersonalityQuestionSerializer,
    PortfolioBlockSerializer,
    PortfolioLinkSerializer,
    PortfolioPersonaliseSerializer,
    PortfolioStandardSerializer,
    UserProfileSerializer,
)


class UserProfileView(generics.RetrieveUpdateAPIView):
    """GET/PUT/PATCH the request user's profile.

    UserProfile rows are auto-created on user creation via post_save signal,
    so `request.user.profile` is guaranteed to exist.
    """

    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadThrottle]

    def get_object(self):
        return UserProfile.objects.get(user=self.request.user)


class SignatureFileView(APIView):
    """GET the request user's signature image. There is no URL for anyone else's: the path
    carries no id."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        signature = UserProfile.objects.get(user=request.user).signature
        content_type = (
            mimetypes.guess_type(signature.name)[0] if signature else None
        ) or "application/octet-stream"
        return private_file_response(
            signature,
            content_type=content_type,
            filename=f"signature{Path(signature.name).suffix}",
        )


class AccountDeleteView(APIView):
    """DELETE the requesting user (and everything that cascades from auth.User).

    allauth headless has no built-in account-delete endpoint, so we roll our own.
    The response shape on the reauth-gate matches allauth's ReauthenticationResponse
    so the frontend's `withReauth` helper picks it up and prompts for password.
    """

    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        if not did_recently_authenticate(request):
            return Response(
                {"data": {"flows": [{"id": "reauthenticate"}]}},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        user = request.user
        logout(request)
        # The operator hears of it from spa.signals, wherever an account is deleted.
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PersonalityProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = PersonalityProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return PersonalityProfile.objects.get(user=self.request.user)


class PersonalityDossierRebuildView(APIView):
    """POST: queue a forced rebuild of a dossier; answers 202 and the page polls the job, whose
    result is `{dossier}`. Body {kind}: "style" rebuilds the writing-style dossier, anything else
    the personality dossier. Optional {provider, model}; blank = the user's default executor."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai-job"

    def post(self, request, *args, **kwargs):
        provider = request.data.get("provider", "")
        model = request.data.get("model", "")
        try:
            resolve_executor(request.user, provider, model)
        except ExecutorError as exc:
            return Response(
                {"provider": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST
            )
        kind = (
            "style_dossier"
            if request.data.get("kind") == "style"
            else "personality_dossier"
        )
        job = start_ai_job(
            kind,
            user=request.user,
            payload={"provider": provider, "model": model},
        )
        return Response(AiJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class PersonalityQuestionListCreateView(generics.ListCreateAPIView):
    """GET the user's visible questions (system defaults + own); POST adds one of the user's
    own. Small list — pagination off, so the response is a plain array matching the shape
    embedded in the personality endpoint."""

    serializer_class = PersonalityQuestionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return PersonalityQuestion.objects.for_user(self.request.user)  # type: ignore


class PersonalityQuestionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """PATCH/DELETE a question the user OWNS. System defaults are read-only: they are absent
    from this queryset, so addressing one 404s (no guard needed)."""

    serializer_class = PersonalityQuestionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PersonalityQuestion.objects.filter(user=self.request.user)


class PortfolioBlockListCreateView(generics.ListCreateAPIView):
    """Owner CRUD over portfolio blocks. Small list — pagination off, like the
    personality questions. Multipart for the image field (the avatar-upload pattern)."""

    serializer_class = PortfolioBlockSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadThrottle]
    pagination_class = None

    def get_queryset(self):
        return PortfolioBlock.objects.filter(user=self.request.user).prefetch_related(
            "domains"
        )


class PortfolioBlockDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PortfolioBlockSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [UploadThrottle]

    def get_queryset(self):
        return PortfolioBlock.objects.filter(user=self.request.user)

    def perform_update(self, serializer):
        """A replaced file leaves the disk with the save: the media quota counts the rows'
        files, so a replaced one mustn't stay behind. A replaced image goes the same way."""
        before = [serializer.instance.image, serializer.instance.media]
        block = serializer.save()
        kept = {block.image.name, block.media.name}
        delete_on_commit(*(f for f in before if f.name not in kept))


def _owner_links(user):
    """The owner's links, manual and application. The standard page is a row of the same
    table but no link: it has its own endpoint (`PortfolioStandardView`)."""
    return PortfolioLink.objects.filter(user=user).exclude(
        kind=PortfolioLink.Kind.standard
    )


class PortfolioLinkListCreateView(generics.ListCreateAPIView):
    """List every link — manual and application, revoked included (the owner sees
    history); POST creates a manual link (kind is read-only, application links come
    from jac's portfolio-link action)."""

    serializer_class = PortfolioLinkSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return (
            _owner_links(self.request.user)
            .select_related("application")
            .order_by("-created_at")
        )


class PortfolioLinkDetailView(generics.RetrieveUpdateDestroyAPIView):
    """PATCH title/intro/content/slug; DELETE hard-removes (revoke is the soft path —
    prefer it for anything ever sent out)."""

    serializer_class = PortfolioLinkSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return _owner_links(self.request.user)


class PortfolioLinkRevokeView(APIView):
    """POST: soft-kill a link — public 404 from the next request on. Idempotent."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        link = get_object_or_404(_owner_links(request.user), pk=pk)
        link.revoke()
        return Response(
            PortfolioLinkSerializer(link, context={"request": request}).data
        )


class PortfolioStandardView(APIView):
    """GET/PATCH the owner's standard page. GET shows an empty page while there's no row, so
    opening the portfolio pages writes nothing; the first PATCH makes the row."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        page = standard_page(request.user) or PortfolioLink(
            user=request.user,
            kind=PortfolioLink.Kind.standard,
            content={"featured": []},
        )
        return Response(
            PortfolioStandardSerializer(page, context={"request": request}).data
        )

    def patch(self, request):
        ser = PortfolioStandardSerializer(
            standard_page_for(request.user),
            data=request.data,
            partial=True,
            context={"request": request},
        )
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


####### Public Views


class PublicPortfolioAPIView(APIView):
    """Base for the anonymous portfolio endpoints: explicit AllowAny (the IndexView
    pattern — public by opt-in, never by omission), scoped throttling, and an
    X-Robots-Tag so the API URLs themselves never get indexed. The owner comes from the
    `<handle>` in the path, never from the Host header."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "portfolio"

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["X-Robots-Tag"] = "noindex"
        return response


def _require_captcha(payload: str, purpose: str) -> None:
    """A wake costs the visitor a solved check (spa.captcha), once per purpose. A refused one is a
    field error on `captcha`, the 400 every check answers. Call it last, after the other fields,
    so a refused field doesn't spend the payload."""
    if not check_captcha(payload, purpose):
        raise ValidationError({"captcha": [REFUSED]})


class PortfolioCaptchaView(PublicPortfolioAPIView):
    """GET: a fresh proof-of-work challenge (spa.captcha), solved in the browser before a request
    that can wake the model server. No handle: one challenge serves any portfolio. Never cached,
    because each one is single use."""

    throttle_scope = "captcha"

    def get(self, request):
        response = Response(new_challenge())
        response["Cache-Control"] = "no-store"
        return response


class PortfolioResolveView(PublicPortfolioAPIView):
    """GET: personalised slug → payload. Revoked ≡ missing — the single filtered
    lookup yields identical 404s. Counts the visit unless the owner previews their
    own link. The one public path open on an unpublished handle: see `link_owner_for_handle`."""

    def get(self, request, handle, slug):
        owner = link_owner_for_handle(handle)
        if owner is None:
            raise Http404
        link = get_object_or_404(
            _owner_links(owner).filter(revoked_at__isnull=True), slug=slug
        )
        if request.user.pk != link.user_id:  # type: ignore
            bump_visit(link)
        return Response(build_payload(link.user, link=link))


class PortfolioNativeView(PublicPortfolioAPIView):
    """GET: the native (questionnaire-driven) view of the handle's portfolio.
    `?domains=a,b` scopes; `?lucky=1` = favourites + a random tasting
    menu. Stateless — bots create zero rows."""

    def get(self, request, handle):
        owner = owner_for_handle(handle)
        if owner is None:
            raise Http404
        domains = [
            d.strip()
            for d in (request.query_params.get("domains") or "").split(",")
            if d.strip()
        ]
        lucky = request.query_params.get("lucky") in ("1", "true")
        return Response(
            build_payload(
                owner,
                domains=domains,
                lucky=lucky,
                focus=request.query_params.get("focus") or "balanced",
                tone=request.query_params.get("tone") or "neutral",
            )
        )


class PortfolioMetaView(PublicPortfolioAPIView):
    """GET: the questionnaire's building blocks — the owner's REAL domain shortlist (only
    domains with content, so no branch dead-ends) plus the style-axis vocab, and the owner's
    accent, which the questionnaire wears before there's a payload to carry it."""

    def get(self, request, handle):
        owner = owner_for_handle(handle)
        if owner is None:
            raise Http404
        return Response(
            {
                "domains": owner_domains(owner),
                "accent": owner.profile.accent_color or "",  # type: ignore
                "tones": [
                    {"value": v, "label": str(label)}
                    for v, label in PersonalityProfile.Tone.choices
                ],
                "focuses": [
                    {"value": v, "label": str(label)}
                    for v, label in PersonalityProfile.Focus.choices
                ],
            }
        )


class PortfolioPersonaliseView(PublicPortfolioAPIView):
    """POST: queue the AI half of a questionnaire visit, the ranking by what the visitor wrote and
    the welcome (`spa.ai_jobs.portfolio_personalise`, HirschAI only). Anonymous model-server
    compute, so the tight `portfolio-personalise` scope is the valve. Answers 202 with a job id at
    once: the standard page is already there, and the visitor chooses between waiting for the
    personal one and browsing the standard one meanwhile, even while the model server wakes."""

    throttle_scope = "portfolio-personalise"

    def post(self, request, handle):
        owner = owner_for_handle(handle)
        if owner is None:
            raise Http404
        ser = PortfolioPersonaliseSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        _require_captcha(data["captcha"], "personalise")  # type: ignore
        job = start_ai_job(
            "portfolio_personalise",
            user=owner,
            payload={
                "domains": data.get("domains") or [],  # type: ignore
                "query": data.get("query") or "",  # type: ignore
                "focus": data["focus"],  # type: ignore
                "tone": data["tone"],  # type: ignore
            },
        )
        return Response({"id": str(job.pk)}, status=status.HTTP_202_ACCEPTED)


class PortfolioPrewarmView(PublicPortfolioAPIView):
    """POST: a visitor has started the questionnaire, so the rank and the AI intro are coming. Wake the
    model server ahead of them. The page sends it on a first interaction, not on load: crawlers load
    pages. Answers like every public path on a portfolio that isn't there."""

    throttle_scope = "prewarm"

    def post(self, request, handle):
        if owner_for_handle(handle) is None:
            raise Http404
        ser = CaptchaSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        _require_captcha(ser.validated_data["captcha"], "prewarm")  # type: ignore
        request_prewarm()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PortfolioPersonaliseStatusView(PublicPortfolioAPIView):
    """GET: where a visit's AI job stands. `{status, waking, ranked?, intro}`: `waking` while the
    model server starts; `ranked` is absent until the ranking is done and null when there's none;
    `intro` stays '' until it's written, and for good if it failed. The unguessable job id is the
    only key, and the row holds no more than entry ids and the paragraph a visitor is about to
    read."""

    throttle_scope = "portfolio-poll"

    def get(self, request, pk):
        job = get_object_or_404(AiJob, pk=pk, kind="portfolio_personalise")
        result = job.result or {}
        body = {
            "status": job.status,
            "waking": job.stage == WAITING_STAGE,
            "intro": result.get("intro", ""),
        }
        if "ranked" in result:
            body["ranked"] = result["ranked"]
        return Response(body)
