# Portfolio / personalized-link views — Phase 3+.

import logging
import mimetypes
from pathlib import Path

from allauth.account.internal.flows.reauthentication import did_recently_authenticate
from django.conf import settings
from django.contrib.auth import logout
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from llm_connector.base import LLMWarmingUp
from llm_connector.client import interactive
from llm_connector.conf import ExecutorError, resolve_executor
from llm_connector.jobs import start_ai_job
from llm_connector.models import AiJob
from llm_connector.serializers import AiJobSerializer
from lukehirsch.private_media import private_file_response
from lukehirsch.throttling import UploadThrottle
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
    owner_domains,
    rank_for_query,
    resolve_link_owner,
    resolve_owner,
)
from spa.serializers import (
    PersonalityProfileSerializer,
    PersonalityQuestionSerializer,
    PortfolioBlockSerializer,
    PortfolioIntroSerializer,
    PortfolioLinkSerializer,
    PortfolioRankSerializer,
    UserProfileSerializer,
)

logger = logging.getLogger(__name__)


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


class PortfolioLinkListCreateView(generics.ListCreateAPIView):
    """List every link — manual and application, revoked included (the owner sees
    history); POST creates a manual link (kind is read-only, application links come
    from jac's portfolio-link action)."""

    serializer_class = PortfolioLinkSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return (
            PortfolioLink.objects.filter(user=self.request.user)
            .select_related("application")
            .order_by("-created_at")
        )


class PortfolioLinkDetailView(generics.RetrieveUpdateDestroyAPIView):
    """PATCH title/intro/content/slug; DELETE hard-removes (revoke is the soft path —
    prefer it for anything ever sent out)."""

    serializer_class = PortfolioLinkSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PortfolioLink.objects.filter(user=self.request.user)


class PortfolioLinkRevokeView(APIView):
    """POST: soft-kill a link — public 404 from the next request on. Idempotent."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        link = get_object_or_404(PortfolioLink, pk=pk, user=request.user)
        link.revoke()
        return Response(
            PortfolioLinkSerializer(link, context={"request": request}).data
        )


####### Public Views


class PublicPortfolioAPIView(APIView):
    """Base for the anonymous portfolio endpoints: explicit AllowAny (the IndexView
    pattern — public by opt-in, never by omission), scoped throttling, and an
    X-Robots-Tag so the API URLs themselves never get indexed."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "portfolio"

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["X-Robots-Tag"] = "noindex"
        return response


class PortfolioResolveView(PublicPortfolioAPIView):
    """GET: personalised slug → payload. Revoked ≡ missing — the single filtered
    lookup yields identical 404s. Counts the visit unless the owner previews their
    own link. The one public path open on an unpublished handle: see `resolve_link_owner`."""

    def get(self, request, slug):
        owner = resolve_link_owner(request)
        if owner is None:
            raise Http404
        link = get_object_or_404(
            PortfolioLink.objects.filter(revoked_at__isnull=True, user=owner), slug=slug
        )
        if request.user.pk != link.user_id:  # type: ignore
            bump_visit(link)
        return Response(build_payload(link.user, link=link))


class PortfolioNativeView(PublicPortfolioAPIView):
    """GET: the native (questionnaire-driven) view of the configured owner's
    portfolio. `?domains=a,b` scopes; `?lucky=1` = favourites + a random tasting
    menu. Stateless — bots create zero rows."""

    def get(self, request):
        owner = resolve_owner(request)
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


class PortfolioRankView(PublicPortfolioAPIView):
    """POST: the embed finale. The tight `portfolio-rank` scope is the abuse valve —
    this anonymous endpoint costs model-server compute. A visitor is waiting, so it gets a short
    timeout and no retry; on any failure the page keeps its standard selection. `ai` says why:
    "ok", "warming_up" (503 + Retry-After: worth another visit soon) or "unavailable"."""

    throttle_scope = "portfolio-rank"

    def post(self, request):
        owner = resolve_owner(request)
        if owner is None:
            raise Http404
        ser = PortfolioRankSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            with interactive(settings.PORTFOLIO_RANK_TIMEOUT_S):
                ranked = rank_for_query(
                    owner,
                    ser.validated_data["query"],  # type: ignore
                    ser.validated_data.get("domains") or [],  # type: ignore
                )
        except LLMWarmingUp:
            return Response({"ranked": [], "ai": "warming_up"})
        except Exception:  # every AI feature has a path without AI
            logger.warning("portfolio rank failed", exc_info=True)
            return Response({"ranked": [], "ai": "unavailable"})
        return Response({"ranked": ranked, "ai": "ok"})


class PortfolioMetaView(PublicPortfolioAPIView):
    """GET: the questionnaire's building blocks — the owner's REAL domain shortlist (only
    domains with content, so no branch dead-ends) plus the style-axis vocab."""

    def get(self, request):
        owner = resolve_owner(request)
        if owner is None:
            raise Http404
        return Response(
            {
                "domains": owner_domains(owner),
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


class PortfolioIntroView(PublicPortfolioAPIView):
    """POST: queue the AI intro — the one generative anonymous call (HirschAI-only, tight
    `portfolio-intro` throttle). Answers 202 with a job id at once: the page is already there, and
    the intro joins it if it arrives, even when the model server has to wake first. "Pages first,
    AI second." """

    throttle_scope = "portfolio-intro"

    def post(self, request):
        owner = resolve_owner(request)
        if owner is None:
            raise Http404
        ser = PortfolioIntroSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        job = start_ai_job(
            "portfolio_intro",
            user=owner,
            payload={
                "domains": data.get("domains") or [],  # type: ignore
                "query": data.get("query") or "",  # type: ignore
                "focus": data["focus"],  # type: ignore
                "tone": data["tone"],  # type: ignore
            },
        )
        return Response({"id": str(job.pk)}, status=status.HTTP_202_ACCEPTED)


class PortfolioIntroStatusView(PublicPortfolioAPIView):
    """GET: where an intro stands. `{status, intro}`; `intro` stays '' until it's done, and for
    good if the job failed. The unguessable job id is the only key, and the row holds no more than
    the paragraph a visitor is about to read."""

    throttle_scope = "portfolio-poll"

    def get(self, request, pk):
        job = get_object_or_404(AiJob, pk=pk, kind="portfolio_intro")
        return Response(
            {"status": job.status, "intro": (job.result or {}).get("intro", "")}
        )
