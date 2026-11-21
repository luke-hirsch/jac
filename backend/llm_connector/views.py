"""DRF viewsets for the LLM connector."""

import time

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from llm_connector.catalog import COMMERCIAL_PROVIDERS, picker_models, refresh_models
from llm_connector.client import LLMClient
from llm_connector.conf import HIRSCHAI_PROVIDER, ExecutorError, default_executor
from llm_connector.models import (
    AiJob,
    LLMConfig,
    LLMRequestLog,
    Provider,
    TransferConsent,
)
from llm_connector.probe import hirschai_state
from llm_connector.serializers import (
    AiJobSerializer,
    LLMConfigSerializer,
    LLMRequestLogSerializer,
)
from llm_connector.tasks import request_prewarm
from lukehirsch.permissions import IsOwner


class ConsentView(APIView):
    """The kept "I agree" to one kind of transfer out of the EU (`TransferConsent`): GET when it
    was given (null: not yet), POST gives it (a second click keeps the first time), DELETE
    withdraws it and deletes what it covered, so the next key or token asks again. Session only:
    the token a consent covers can't give or withdraw it."""

    permission_classes = [IsAuthenticated]
    scope = TransferConsent.Scope.providers

    def covered(self, user):
        """What may send data out on this consent, deleted when it's withdrawn."""
        return LLMConfig.objects.filter(user=user)

    def _answer(self, given_at, code=status.HTTP_200_OK):
        return Response(
            {
                "scope": self.scope,
                "given_at": given_at.isoformat() if given_at else None,
            },
            status=code,
        )

    @extend_schema(
        request=None, responses=OpenApiResponse(description="{scope, given_at}")
    )
    def get(self, request):
        return self._answer(TransferConsent.given(request.user, self.scope))

    @extend_schema(
        request=None, responses=OpenApiResponse(description="{scope, given_at}")
    )
    def post(self, request):
        consent, created = TransferConsent.give(request.user, self.scope)
        return self._answer(
            consent.given_at, status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    @extend_schema(request=None, responses={204: None})
    def delete(self, request):
        with transaction.atomic():
            TransferConsent.objects.filter(user=request.user, scope=self.scope).delete()
            self.covered(request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LLMConfigViewSet(viewsets.ModelViewSet):
    serializer_class = LLMConfigSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return LLMConfig.objects.filter(user=self.request.user)

    @extend_schema(
        request=None,
        responses=OpenApiResponse(
            description="{ok: true, latency_ms, models} or {ok: false, error, models}: "
            "`models` is how many models the key reaches as stored after the check"
        ),
    )
    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        """Refresh the key's model list from the provider, then a pong on the default pick. A
        listing that fails keeps the last list."""
        config = self.get_object()
        try:
            refresh_models(config)
            client = LLMClient(config.provider, user=request.user)
            start = time.monotonic()
            client.complete("Respond with exactly one word: pong")
            latency_ms = int((time.monotonic() - start) * 1000)
            return Response(
                {"ok": True, "latency_ms": latency_ms, "models": len(config.models)}
            )
        except Exception as exc:  # noqa: BLE001 — any failure is the check's finding
            return Response(
                {"ok": False, "error": str(exc), "models": len(config.models)}
            )


class LLMRequestLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LLMRequestLogSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return LLMRequestLog.objects.filter(user=self.request.user)


class AiJobView(APIView):
    """GET one of the user's short AI jobs: what a page polls after a 202. Someone else's id is a
    404, indistinguishable from a missing one."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        job = get_object_or_404(AiJob, pk=pk, user=request.user)
        return Response(AiJobSerializer(job).data)


class PrewarmView(APIView):
    """POST: this user is about to need the model, so wake it ahead of their request. A user on their
    own key never touches the tower. 204 whatever happens: nothing is waiting for this."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "prewarm"

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        try:
            on_hirschai = default_executor(request.user).is_hirschai
        except ExecutorError:
            # A default key without a model list: still not the tower.
            on_hirschai = False
        if on_hirschai:
            request_prewarm()
        return Response(status=204)


class ExecutorListView(APIView):
    """Everything the generate panel needs in ONE request: HirschAI (`reachable` is what
    the last real call found out, never a probe: this runs on page loads) + every commercial
    provider (configured?, default?, models with their knobs). The model lists are what the
    last key save or Check stored on the user's row: polled every 30 s, this never asks a
    provider.
    `modes` are jac vocabulary served as opaque labels — deliberate denormalisation
    so the SPA needs no second endpoint; `high` is commercial-only by design."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        own = {c.provider: c for c in LLMConfig.objects.filter(user=request.user)}
        # A key without the transfer consent counts as unconfigured: nothing runs on it.
        commercial_default = any(c.default and c.usable for c in own.values())
        rows = [
            {
                "provider": HIRSCHAI_PROVIDER,
                "label": "HirschAI",
                "self_hosted": True,
                "configured": True,
                "reachable": hirschai_state(),
                "default": not commercial_default,
                "models": [],
                "modes": ["standard"],
            }
        ]
        for provider in COMMERCIAL_PROVIDERS:
            row = own.get(provider)
            usable = bool(row and row.usable)
            rows.append(
                {
                    "provider": provider,
                    "label": Provider(provider).label,
                    "self_hosted": False,
                    "configured": usable,
                    "reachable": None,
                    "default": bool(usable and row.default),
                    "models": picker_models(provider, row.models) if usable else [],
                    "modes": ["standard", "high"],
                }
            )
        return Response(rows)
