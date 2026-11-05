"""DRF viewsets for the LLM connector."""

import time

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from llm_connector.catalog import CATALOG, knobs_for, models_for
from llm_connector.client import LLMClient
from llm_connector.conf import HIRSCHAI_PROVIDER, default_executor
from llm_connector.models import AiJob, LLMConfig, LLMRequestLog, Provider
from llm_connector.probe import hirschai_state
from llm_connector.serializers import (
    AiJobSerializer,
    LLMConfigSerializer,
    LLMRequestLogSerializer,
)
from llm_connector.tasks import request_prewarm
from lukehirsch.permissions import IsOwner


class LLMConfigViewSet(viewsets.ModelViewSet):
    serializer_class = LLMConfigSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return LLMConfig.objects.filter(user=self.request.user)

    @extend_schema(
        request=None,
        responses=OpenApiResponse(
            description="{ok: true, latency_ms} or {ok: false, error}"
        ),
    )
    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        config = self.get_object()
        try:
            client = LLMClient(config.provider, user=request.user)
            start = time.monotonic()
            client.complete("Respond with exactly one word: pong")
            latency_ms = int((time.monotonic() - start) * 1000)
            return Response({"ok": True, "latency_ms": latency_ms})
        except Exception as exc:  # noqa: BLE001 — any failure is the check's finding
            return Response({"ok": False, "error": str(exc)})


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
        if default_executor(request.user).is_hirschai:
            request_prewarm()
        return Response(status=204)


class ExecutorListView(APIView):
    """Everything the generate panel needs in ONE request: HirschAI (`reachable` is what
    the last real call found out, never a probe: this runs on page loads) + every catalog
    provider (configured?, default?, models).
    `modes` are jac vocabulary served as opaque labels — deliberate denormalisation
    so the SPA needs no second endpoint; `high` is commercial-only by design."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        own = {c.provider: c for c in LLMConfig.objects.filter(user=request.user)}
        commercial_default = any(c.default and c.has_api_key for c in own.values())
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
                "knobs": {},
            }
        ]
        for provider in CATALOG:
            row = own.get(provider)
            rows.append(
                {
                    "provider": provider,
                    "label": Provider(provider).label,
                    "self_hosted": False,
                    "configured": bool(row and row.has_api_key),
                    "reachable": None,
                    "default": bool(row and row.default and row.has_api_key),
                    "models": models_for(provider),
                    "modes": ["standard", "high"],
                    "knobs": knobs_for(provider),
                }
            )
        return Response(rows)
