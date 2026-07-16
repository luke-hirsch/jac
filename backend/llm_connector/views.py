"""DRF viewsets for the LLM connector."""

import time

from drf_spectacular.utils import OpenApiResponse, extend_schema
from lukehirsch.permissions import IsOwner
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from llm_connector.catalog import CATALOG, models_for
from llm_connector.client import LLMClient
from llm_connector.conf import HIRSCHAI_PROVIDER
from llm_connector.models import LLMConfig, LLMRequestLog, Provider
from llm_connector.probe import hirschai_reachable
from llm_connector.serializers import LLMConfigSerializer, LLMRequestLogSerializer


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


class ExecutorListView(APIView):
    """Everything the generate panel needs in ONE request: HirschAI (with a live
    reachability flag) + every catalog provider (configured?, default?, models).
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
                "reachable": hirschai_reachable(),
                "default": not commercial_default,
                "models": [],
                "modes": ["standard"],
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
                }
            )
        return Response(rows)
